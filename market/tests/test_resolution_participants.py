"""Spec E4, phase 3d: an outcome without a photo, an outcome by participants, proof on dispute."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal
from threading import Barrier
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.db import close_old_connections, connection
from django.utils import timezone

from market import chat_disputes, chats, demo_telegram, disputes_bot, resolution, services
from market.api.serializers import author
from market.models import (BotJob, BotMessage, Dispute, Group, GroupMembership, LedgerEntry, Notification, Payout,
                           Question, Trade, Trader)
from market.services import MarketError
from market.telegram import TelegramError, handle_update
from market.tests.bot_job_helpers import drain_jobs
from market.tests.test_bot_resolution_dialog import (ANYA, BORIS, CREATOR, STRANGER, bot, make, market, media,  # noqa: F401
                                                     people, photo, press, text)
from market.tests.test_chat_disputes import Bot as ChatBot

D = Decimal
CENT = D("0.01")
START = D(4000)  # the four people of the `people` fixture, 1000 each
BOND = D(20)
STAKE = D(20)


def overdue(question, hours=25):
    """The creator has been silent for `hours` after the outcome date."""
    moment = timezone.now() - timedelta(hours=hours)
    Question.objects.filter(pk=question.pk).update(resolves_at=moment, deadline=moment)
    question.refresh_from_db()
    return question


def close_window(question):
    Question.objects.filter(pk=question.pk).update(dispute_until=timezone.now() - timedelta(seconds=1))
    question.refresh_from_db()
    return question


def balance(trader):
    return Trader.objects.get(pk=trader.pk).balance


def ledger(trader, kind):
    return list(LedgerEntry.objects.filter(trader=trader, kind=kind).values_list("amount", flat=True))


def column(rows, name):
    """An exact sum: SQLite's SUM over decimals goes through floats."""
    return sum(rows.values_list(name, flat=True), D(0))


def total():
    return column(Trader.objects.all(), "balance")


def fee_share(*questions):
    pool = sum((Question.objects.get(pk=q.pk).fee_pool for q in questions), D(0))
    return (pool * D("0.5")).quantize(CENT, rounding=ROUND_FLOOR)


def assert_conserved(questions, *, share=D(0), burned=D(0), start=START):
    """Balances + everything burned = what was there before; nothing is printed on the way.

    Burned here: fees minus the share someone got, the market maker's profit over the subsidy, and `burned`
    (lost bonds and stakes).
    """
    ids = [q.pk for q in questions]
    trades = Trade.objects.filter(question_id__in=ids)
    cost, fee = column(trades, "cost"), column(trades, "fee")
    paid = column(Payout.objects.filter(question_id__in=ids, kind=Payout.Kind.PAYOUT), "amount")
    subsidy = sum((Question.objects.get(pk=i).subsidy for i in ids), D(0))
    maker = subsidy + cost - fee - paid
    back = min(max(maker, D(0)), subsidy).quantize(CENT, rounding=ROUND_FLOOR)
    assert total() + (maker - back) + (fee - share) + burned == start
    assert column(LedgerEntry.objects.all(), "amount") == total() - start  # every change is in the ledger


@pytest.fixture
def proposed(people, market):
    """Anya stated ДА after the creator stayed silent."""
    return services.propose_by_participant(people[1], overdue(market), "yes")


@pytest.fixture
def multi(people):
    creator, anya, boris, _ = people
    now = timezone.now()
    root = services.create_market(creator, title="Защита кончится до ...", criteria="По часам", category="study",
                                  closes_at=now + timedelta(hours=4), resolves_at=now + timedelta(hours=5),
                                  options=["до 20:00", "до 21:00"])
    subs = list(root.submarkets.order_by("id"))
    services.buy(anya, subs[0], "yes", 60)
    services.buy(boris, subs[1], "yes", 40)
    moment = timezone.now() - timedelta(hours=25)
    Question.objects.filter(pk__in=[root.pk, *(s.pk for s in subs)]).update(resolves_at=moment, deadline=moment)
    root.refresh_from_db()
    return root, subs


# --- the creator: no photo needed -----------------------------------------------------------------------

def test_creator_outcome_without_photo_settles_as_usual(people, market):
    creator = people[0]
    q = services.propose_resolution(creator, market, "yes")
    assert q.proposed_by_id == creator.pk and q.proposal_bond == 0 and not q.evidence and not q.evidence_file_id
    q = services.finalize_resolution(close_window(q))
    assert q.status == Question.Status.RESOLVED and q.outcome == "yes" and not q.creator_at_fault
    assert ledger(creator, LedgerEntry.Kind.CREATOR_FEE) == [fee_share(q)]
    assert ledger(creator, LedgerEntry.Kind.SUBSIDY_RETURN)
    assert not LedgerEntry.objects.filter(kind__in=[LedgerEntry.Kind.PROPOSER_FEE, LedgerEntry.Kind.PROPOSAL_BOND,
                                                    LedgerEntry.Kind.PROPOSAL_BOND_REFUND]).exists()
    assert_conserved([q], share=fee_share(q))


def test_wrong_creator_without_photo_still_loses_the_subsidy(people, market):
    creator, _, boris, _ = people
    q = services.propose_resolution(creator, market, "yes")
    services.open_dispute(boris, q)
    q = services.moderator_decide(q, "no")
    assert q.creator_at_fault and not ledger(creator, LedgerEntry.Kind.SUBSIDY_RETURN)
    assert author(creator)["disputes_lost"] == 1 and author(creator)["resolved_by_participants"] == 0


# --- who may state the outcome --------------------------------------------------------------------------

def test_participant_rules(people, market):
    creator, anya, _, stranger = people
    with pytest.raises(MarketError, match="через 24 ч"):
        services.propose_by_participant(anya, market, "yes")  # the outcome date has only just passed
    q = overdue(market)
    with pytest.raises(MarketError, match="Создатель"):
        services.propose_by_participant(creator, q, "yes")
    with pytest.raises(MarketError, match="только участник"):
        services.propose_by_participant(stranger, q, "yes")
    with pytest.raises(MarketError, match="ДА или НЕТ"):
        services.propose_by_participant(anya, q, "cancel")
    assert balance(anya) == D(1000) - Trade.objects.get(trader=anya).cost
    assert not LedgerEntry.objects.filter(kind=LedgerEntry.Kind.PROPOSAL_BOND).exists()
    q.refresh_from_db()
    assert q.resolution_state == ""


def test_participant_while_betting_is_refused(people):
    creator, anya, *_ = people
    now = timezone.now()
    q = services.create_market(creator, title="Рано?", criteria="c", category="tech",
                               closes_at=now + timedelta(hours=2), resolves_at=now + timedelta(hours=3))
    services.buy(anya, q, "yes", 10)
    with pytest.raises(MarketError, match="Приём прогнозов"):
        services.propose_by_participant(anya, q, "yes")


def test_not_enough_coins_for_the_bond(people, market):
    anya = people[1]
    Trader.objects.filter(pk=anya.pk).update(balance=D("19.99"))
    with pytest.raises(MarketError, match="не хватает"):
        services.propose_by_participant(anya, overdue(market), "yes")
    market.refresh_from_db()
    assert market.resolution_state == "" and balance(anya) == D("19.99")


def test_participant_outcome_is_recorded_with_the_bond(people, proposed):
    anya = people[1]
    assert proposed.resolution_state == "pending" and proposed.proposed_outcome == "yes"
    assert proposed.proposed_by_id == anya.pk and proposed.proposal_bond == BOND
    assert proposed.dispute_until - proposed.proposed_at == timedelta(hours=24)
    assert ledger(anya, LedgerEntry.Kind.PROPOSAL_BOND) == [-BOND]


def test_first_participant_wins_and_a_repeat_changes_nothing(people, proposed):
    _, anya, boris, _ = people
    before = balance(anya), balance(boris)
    with pytest.raises(MarketError, match="другой участник"):
        services.propose_by_participant(boris, proposed, "no")
    with pytest.raises(MarketError, match="уже предложил"):
        services.propose_by_participant(anya, proposed, "yes")
    proposed.refresh_from_db()
    assert proposed.proposed_outcome == "yes" and proposed.proposed_by_id == anya.pk
    assert (balance(anya), balance(boris)) == before
    assert LedgerEntry.objects.filter(kind=LedgerEntry.Kind.PROPOSAL_BOND).count() == 1


def test_the_creator_may_answer_late_and_then_participants_cannot(people, market):
    creator, anya, *_ = people
    q = services.propose_resolution(creator, overdue(market), "no")
    with pytest.raises(MarketError, match="Автор уже указал"):
        services.propose_by_participant(anya, q, "yes")
    assert not LedgerEntry.objects.filter(kind=LedgerEntry.Kind.PROPOSAL_BOND).exists()


def test_creator_cannot_overrule_a_participant_by_stating_the_outcome(people, proposed):
    with pytest.raises(MarketError, match="уже указан"):
        services.propose_resolution(people[0], proposed, "no")


# --- when the outcome stands ------------------------------------------------------------------------------

def test_outcome_stands_bond_back_and_fee_share_to_the_proposer(people, proposed):
    creator, anya, *_ = people
    q = services.finalize_resolution(close_window(proposed))
    assert q.status == Question.Status.RESOLVED and q.outcome == "yes" and not q.creator_at_fault
    assert ledger(anya, LedgerEntry.Kind.PROPOSAL_BOND_REFUND) == [BOND]
    assert ledger(anya, LedgerEntry.Kind.PROPOSER_FEE) == [fee_share(q)] and fee_share(q) > 0
    assert not ledger(creator, LedgerEntry.Kind.CREATOR_FEE)
    assert ledger(creator, LedgerEntry.Kind.SUBSIDY_RETURN)
    assert_conserved([q], share=fee_share(q))
    assert author(creator)["resolved_by_participants"] == 1 and author(creator)["disputes_lost"] == 0


def test_rejected_dispute_keeps_the_participants_outcome(people, proposed):
    creator, anya, boris, _ = people
    services.open_dispute(boris, proposed)
    q = services.moderator_decide(proposed, "yes")
    assert not q.creator_at_fault and Dispute.objects.get().upheld is False
    assert ledger(anya, LedgerEntry.Kind.PROPOSAL_BOND_REFUND) == [BOND]
    assert ledger(anya, LedgerEntry.Kind.PROPOSER_FEE) == [fee_share(q)]
    assert not ledger(creator, LedgerEntry.Kind.CREATOR_FEE)
    assert not ledger(boris, LedgerEntry.Kind.DISPUTE_REFUND)
    assert_conserved([q], share=fee_share(q), burned=STAKE)
    assert author(creator)["resolved_by_participants"] == 1


# --- when a dispute against it is upheld --------------------------------------------------------------------

def test_upheld_dispute_burns_the_bond_and_the_fee_share(people, proposed):
    creator, anya, boris, _ = people
    services.open_dispute(boris, proposed)
    q = services.moderator_decide(proposed, "no")
    assert q.outcome == "no" and q.status == Question.Status.RESOLVED
    assert not q.creator_at_fault  # the creator isn't punished for a participant's mistake
    assert ledger(creator, LedgerEntry.Kind.SUBSIDY_RETURN)
    assert ledger(boris, LedgerEntry.Kind.DISPUTE_REFUND) == [STAKE]
    assert not ledger(anya, LedgerEntry.Kind.PROPOSAL_BOND_REFUND)
    # Nobody earned the share: the proposer was wrong, the creator stayed silent.
    assert not LedgerEntry.objects.filter(kind__in=[LedgerEntry.Kind.PROPOSER_FEE, LedgerEntry.Kind.CREATOR_FEE]).exists()
    assert_conserved([q], burned=BOND)
    assert author(creator)["disputes_lost"] == 0 and author(creator)["resolved_by_participants"] == 0


def test_moderator_cancels_a_participants_outcome(people, proposed):
    creator, anya, boris, _ = people
    services.open_dispute(boris, proposed)
    q = services.moderator_decide(proposed, "cancel")
    assert q.status == Question.Status.ANNULLED and not q.creator_at_fault and q.resolution_state == "final"
    assert ledger(boris, LedgerEntry.Kind.DISPUTE_REFUND) == [STAKE]
    assert not ledger(anya, LedgerEntry.Kind.PROPOSAL_BOND_REFUND)
    assert ledger(creator, LedgerEntry.Kind.SUBSIDY_RETURN)
    # Everyone is whole except the bond, and no platform coins were added.
    assert total() == START - BOND
    q.refresh_from_db()
    assert q.refund_compensation == 0


def test_complaint_cancelling_the_market_returns_a_held_bond(people, proposed):
    anya = people[1]
    services.cancel_moderated(proposed)
    assert ledger(anya, LedgerEntry.Kind.PROPOSAL_BOND_REFUND) == [BOND]


def test_decision_tells_the_creator_they_keep_the_subsidy(people, proposed, django_capture_on_commit_callbacks):
    creator, _, boris, _ = people
    services.open_dispute(boris, proposed)
    with django_capture_on_commit_callbacks(execute=True):
        services.moderator_decide(proposed, "no")
    note = Notification.objects.get(key=f"moderator:{proposed.pk}", recipient=creator)
    assert "спор признан" in note.text and "возвращаются как обычно" in note.text


# --- disputes --------------------------------------------------------------------------------------------------

def test_creator_may_dispute_a_participants_outcome(people, proposed):
    creator, anya, boris, _ = people
    assert resolution.public_state(proposed, creator)["can_dispute"]
    before = balance(creator)
    dispute = services.open_dispute(creator, proposed, "Было не так")
    assert dispute.stake == STAKE and balance(creator) == before - STAKE
    q = services.moderator_decide(proposed, "no")
    assert ledger(creator, LedgerEntry.Kind.DISPUTE_REFUND) == [STAKE] and not q.creator_at_fault
    assert_conserved([q], burned=BOND)


def test_who_may_not_dispute(people, market):
    creator, anya, boris, stranger = people
    own = services.propose_resolution(creator, market, "yes")
    with pytest.raises(MarketError, match="только участник"):
        services.open_dispute(creator, own)  # their own outcome
    assert not resolution.public_state(own, creator)["can_dispute"]


def test_proposer_and_outsiders_may_not_dispute_a_participants_outcome(people, proposed):
    _, anya, boris, stranger = people
    with pytest.raises(MarketError, match="Свой итог"):
        services.open_dispute(anya, proposed)
    with pytest.raises(MarketError, match="только участник"):
        services.open_dispute(stranger, proposed)
    state = resolution.public_state(proposed, anya)
    assert not state["can_dispute"] and state["by_participant"] is True
    assert resolution.public_state(proposed, boris)["can_dispute"]
    assert not resolution.public_state(proposed, stranger)["can_dispute"]


def test_public_state_marks_the_creators_outcome(people, market):
    q = services.propose_resolution(people[0], market, "yes")
    state = resolution.public_state(q, people[1])
    assert state["by_participant"] is False and state["evidence_url"] is None


# --- a market with options ------------------------------------------------------------------------------------

def test_multi_participant_marks_every_option(people, multi):
    root, subs = multi
    anya = people[1]
    for wrong in (None, {subs[0].pk: "yes"}, {subs[0].pk: "yes", subs[1].pk: "maybe"}):
        with pytest.raises(MarketError, match="каждого варианта"):
            services.propose_by_participant(anya, root, "yes", submarket_outcomes=wrong)
    with pytest.raises(MarketError, match="каждого варианта"):
        services.propose_by_participant(anya, root, "cancel")
    assert not LedgerEntry.objects.filter(kind=LedgerEntry.Kind.PROPOSAL_BOND).exists()
    q = services.propose_by_participant(anya, subs[0], "yes", submarket_outcomes={subs[0].pk: "yes", subs[1].pk: "no"})
    assert q.pk == root.pk and q.proposed_by_id == anya.pk
    assert [s.proposed_outcome for s in q.submarkets.order_by("id")] == ["yes", "no"]


def test_multi_outcome_stands_one_fee_share_from_all_options(people, multi):
    creator, anya, *_ = people
    root, subs = multi
    q = services.propose_by_participant(anya, root, "yes", submarket_outcomes={subs[0].pk: "yes", subs[1].pk: "no"})
    q = services.finalize_resolution(close_window(q))
    assert q.status == Question.Status.RESOLVED
    family = [q, *subs]
    assert ledger(anya, LedgerEntry.Kind.PROPOSER_FEE) == [fee_share(*family)] and fee_share(*family) > 0
    assert ledger(anya, LedgerEntry.Kind.PROPOSAL_BOND_REFUND) == [BOND]
    assert not ledger(creator, LedgerEntry.Kind.CREATOR_FEE)
    assert_conserved(family, share=fee_share(*family))


def test_multi_upheld_dispute_burns_the_bond(people, multi):
    creator, anya, boris, _ = people
    root, subs = multi
    q = services.propose_by_participant(anya, root, "yes", submarket_outcomes={subs[0].pk: "yes", subs[1].pk: "no"})
    services.open_dispute(boris, q)
    q = services.moderator_decide(q, "yes", submarket_outcomes={subs[0].pk: "no", subs[1].pk: "yes"})
    assert not q.creator_at_fault and [s.outcome for s in q.submarkets.order_by("id")] == ["no", "yes"]
    assert not LedgerEntry.objects.filter(kind__in=[LedgerEntry.Kind.PROPOSER_FEE, LedgerEntry.Kind.CREATOR_FEE,
                                                    LedgerEntry.Kind.PROPOSAL_BOND_REFUND]).exists()
    assert ledger(creator, LedgerEntry.Kind.SUBSIDY_RETURN)
    assert_conserved([q, *subs], burned=BOND)


def test_multi_cancel_by_moderator_keeps_the_creators_subsidy(people, multi):
    creator, anya, boris, _ = people
    root, subs = multi
    q = services.propose_by_participant(anya, root, "yes", submarket_outcomes={subs[0].pk: "yes", subs[1].pk: "no"})
    services.open_dispute(boris, q)
    q = services.moderator_decide(q, "cancel")
    assert q.status == Question.Status.ANNULLED and not q.creator_at_fault
    assert {s.status for s in q.submarkets.all()} == {Question.Status.ANNULLED}
    assert total() == START - BOND


# --- proof on dispute ---------------------------------------------------------------------------------------------

def test_first_dispute_asks_the_outcomes_author_for_proof_once(people, proposed):
    creator, anya, boris, _ = people
    services.open_dispute(boris, proposed)
    proposed.refresh_from_db()
    assert proposed.evidence_requested_at is not None
    first = proposed.evidence_requested_at
    job = BotJob.objects.get(key=f"evidence-request:{proposed.pk}")
    assert job.trader_id == anya.pk and "фото, скриншот или ссылку" in job.payload["text"]
    services.open_dispute(creator, proposed)
    proposed.refresh_from_db()
    assert proposed.evidence_requested_at == first
    assert BotJob.objects.filter(key__startswith="evidence-request").count() == 1


def test_no_request_when_the_proof_is_already_there(people, market):
    creator, _, boris, _ = people
    q = services.propose_resolution(creator, market, "yes", evidence_file_id="PHOTO")
    services.open_dispute(boris, q)
    q.refresh_from_db()
    assert q.evidence_requested_at is None
    assert not BotJob.objects.filter(key__startswith="evidence-request").exists()


def test_attach_evidence_rules(people, proposed):
    creator, anya, boris, _ = people
    with pytest.raises(MarketError, match="когда итог оспорили"):
        services.attach_evidence(anya, proposed, note="https://example.com")
    services.open_dispute(boris, proposed)
    with pytest.raises(MarketError, match="тот, кто указал итог"):
        services.attach_evidence(creator, proposed, note="https://example.com")
    with pytest.raises(MarketError, match="фото, скриншот или ссылку"):
        services.attach_evidence(anya, proposed, note="   ")
    q = services.attach_evidence(anya, proposed, evidence=ContentFile(b"\xff\xd8", name="p.jpg"),
                                 evidence_file_id="F", note="С сайта")
    assert q.evidence_file_id == "F" and q.evidence_note == "С сайта" and q.evidence.name.startswith("evidence/")
    assert services.has_evidence(q)
    Question.objects.filter(pk=q.pk).update(evidence_requested_at=timezone.now() - timedelta(hours=24, seconds=1))
    with pytest.raises(MarketError, match="Время на доказательство вышло"):
        services.attach_evidence(anya, q, note="https://example.com/2")


def test_attaching_proof_moves_no_coins(people, proposed):
    _, anya, boris, _ = people
    services.open_dispute(boris, proposed)
    before = list(Trader.objects.order_by("pk").values_list("balance", flat=True))
    services.attach_evidence(anya, proposed, note="https://example.com")
    assert list(Trader.objects.order_by("pk").values_list("balance", flat=True)) == before


# --- the worker -----------------------------------------------------------------------------------------------------

def test_worker_24_48_72_hours(bot, people, market):
    creator = people[0]
    now = timezone.now()
    resolution.tick(now)
    market.refresh_from_db()
    assert market.asked_at and market.participants_asked_at is None and market.reminders_sent == 0
    assert not BotJob.objects.exists()

    at_24 = now + timedelta(hours=24, minutes=1)
    done = resolution.tick(at_24)
    market.refresh_from_db()
    assert done["participants_asked"] == 1 and done["reminded"] == 1
    assert market.participants_asked_at == at_24 and market.reminders_sent == 1
    jobs = BotJob.objects.filter(key__startswith="ask-participants")
    assert {j.trader.telegram_chat_id for j in jobs} == {ANYA, BORIS}  # not the creator, not outsiders
    assert "Автор не указал итог. Знаешь, чем закончилось?" in jobs[0].payload["text"]
    reminder = bot.sent("sendMessage", CREATOR)[-1]
    assert reminder["text"].startswith("Напоминание") and "за 48 ч" in reminder["text"]

    drain_jobs()
    question = bot.sent("sendMessage", ANYA)[-1]
    assert [b["text"] for b in question["reply_markup"]["inline_keyboard"][0]] == ["ДА", "НЕТ"]
    assert "Отменить" not in str(question["reply_markup"])

    calls = len(bot.calls)
    done = resolution.tick(at_24 + timedelta(minutes=1))  # nothing repeats
    assert done["participants_asked"] == 0 and done["reminded"] == 0 and len(bot.calls) == calls
    assert jobs.count() == 2

    assert resolution.tick(now + timedelta(hours=48, minutes=1))["reminded"] == 1
    market.refresh_from_db()
    assert market.reminders_sent == 2 and "за 24 ч" in bot.sent("sendMessage", CREATOR)[-1]["text"]
    assert jobs.count() == 2

    assert resolution.tick(now + timedelta(hours=72, minutes=1))["cancelled"] == 1
    market.refresh_from_db()
    assert market.status == Question.Status.ANNULLED and market.resolution_state == "final"
    assert balance(creator) == D(1000)


def test_participant_outcome_stops_the_72_hour_cancel(people, market):
    with patch("market.telegram.api", return_value={"message_id": 1}):
        q = services.propose_by_participant(people[1], overdue(market, hours=71), "yes")
        done = resolution.tick(timezone.now() + timedelta(hours=2))
    q.refresh_from_db()
    assert done["cancelled"] == 0 and q.status == Question.Status.CLOSED and q.resolution_state == "pending"


def test_no_questions_once_the_outcome_is_stated(people, market):
    with patch("market.telegram.api", return_value={"message_id": 1}):
        services.propose_resolution(people[0], market, "no")
        done = resolution.tick(timezone.now() + timedelta(hours=25))
    assert done["participants_asked"] == 0 and done["reminded"] == 0
    assert not BotJob.objects.filter(key__startswith="ask-participants").exists()


def test_failed_participant_question_is_retried(people, market):
    now = timezone.now()
    at_24 = now + timedelta(hours=24, minutes=1)
    with patch("market.telegram.api", return_value={"message_id": 1}), \
            patch("market.bot_jobs.enqueue_message", side_effect=RuntimeError("queue down")):
        assert resolution.tick(at_24)["participants_asked"] == 0
    market.refresh_from_db()
    assert market.participants_asked_at is None
    with patch("market.telegram.api", return_value={"message_id": 1}):
        assert resolution.tick(at_24)["participants_asked"] == 1


def test_evidence_reminder_once_halfway(bot, people, proposed):
    _, anya, boris, _ = people
    services.open_dispute(boris, proposed)
    now = timezone.now()
    reminders = lambda: [t for t in bot.texts(ANYA) if t.startswith("Напоминание")]  # noqa: E731

    assert resolution.tick(now + timedelta(hours=11))["evidence_reminded"] == 0
    assert not reminders()
    at_half = now + timedelta(hours=12, minutes=1)
    assert resolution.tick(at_half)["evidence_reminded"] == 1
    proposed.refresh_from_db()
    assert proposed.evidence_reminded_at == at_half and len(reminders()) == 1
    assert "фото, скриншот или ссылку" in reminders()[0]
    assert resolution.tick(at_half + timedelta(hours=5))["evidence_reminded"] == 0
    assert len(reminders()) == 1


def test_no_evidence_reminder_after_the_proof_or_the_deadline(bot, people, proposed):
    _, anya, boris, _ = people
    services.open_dispute(boris, proposed)
    now = timezone.now()
    assert resolution.tick(now + timedelta(hours=25))["evidence_reminded"] == 0  # too late to be useful
    services.attach_evidence(anya, proposed, note="https://example.com")
    assert resolution.tick(now + timedelta(hours=13))["evidence_reminded"] == 0
    assert not [t for t in bot.texts(ANYA) if t.startswith("Напоминание")]


def test_telegram_failure_on_the_reminder_keeps_money_and_retries(people, proposed):
    _, anya, boris, _ = people
    services.open_dispute(boris, proposed)
    before = list(Trader.objects.order_by("pk").values_list("balance", flat=True))
    at_half = timezone.now() + timedelta(hours=13)
    with patch("market.telegram.api", side_effect=TelegramError("down")):
        assert resolution.tick(at_half)["evidence_reminded"] == 0
    proposed.refresh_from_db()
    assert proposed.evidence_reminded_at is None
    assert list(Trader.objects.order_by("pk").values_list("balance", flat=True)) == before
    with patch("market.telegram.api", return_value={"message_id": 1}):
        assert resolution.tick(at_half)["evidence_reminded"] == 1


def test_worker_settles_a_participants_outcome_and_tells_them(people, proposed):
    creator, anya, *_ = people
    close_window(proposed)
    with patch("market.telegram.api", return_value={"message_id": 1}):
        done = resolution.tick(timezone.now())
        again = resolution.tick(timezone.now())
    assert done["finalized"] == 1 and again["finalized"] == 0
    assert ledger(anya, LedgerEntry.Kind.PROPOSAL_BOND_REFUND) == [BOND]  # exactly once
    message = BotMessage.objects.get(key=f"proposer-result:{proposed.pk}")
    assert message.kind == "creator" and message.trader_id == anya.pk  # a resolution step: never muted
    assert "Твой итог встал" in message.text and "Залог +20" in message.text and "доля комиссии" in message.text


def test_worker_tells_a_proposer_who_lost_the_bond(people, proposed):
    _, anya, boris, _ = people
    services.open_dispute(boris, proposed)
    services.moderator_decide(proposed, "no")
    with patch("market.telegram.api", return_value={"message_id": 1}):
        resolution.tick(timezone.now())
    assert "залог 20 не возвращается" in BotMessage.objects.get(key=f"proposer-result:{proposed.pk}").text


def test_demo_transport_has_every_worker_step():
    for name in ("ask_creator", "remind_creator", "ask_participants", "remind_evidence", "send_results"):
        assert callable(getattr(demo_telegram, name))


# --- the bot: creator ------------------------------------------------------------------------------------------------

def test_multi_creator_confirms_in_one_press_without_a_photo(bot, people, multi):
    root, subs = multi
    handle_update(text(CREATOR, f"/start resolve_{root.pk}"))
    callbacks = str(bot.sent("sendMessage", CREATOR)[-1]["reply_markup"])
    assert f"resnext:{root.pk}" not in callbacks and f"resphoto:{root.pk}" in callbacks
    handle_update(press(CREATOR, f"resopt:{root.pk}:{subs[0].pk}:yes"))
    handle_update(press(CREATOR, f"resnext:{root.pk}"))
    root.refresh_from_db()
    assert root.resolution_state == ""  # not every option is marked
    assert "для каждого варианта" in bot.texts(CREATOR)[-1]
    handle_update(press(CREATOR, f"resopt:{root.pk}:{subs[1].pk}:no"))
    assert f"resnext:{root.pk}" in str(bot.sent("editMessageText", CREATOR)[-1]["reply_markup"])
    handle_update(press(CREATOR, f"resnext:{root.pk}"))
    root.refresh_from_db()
    assert root.resolution_state == "pending" and not root.evidence and root.proposed_by_id == people[0].pk
    assert [s.proposed_outcome for s in root.submarkets.order_by("id")] == ["yes", "no"]
    assert "Итоги опубликованы:" in bot.texts(CREATOR)[-1]


# --- the bot: participants --------------------------------------------------------------------------------------------

def test_participant_confirms_the_bond_in_the_bot(bot, people, market):
    _, anya, boris, _ = people
    q = overdue(market)
    before = balance(anya)
    handle_update(press(ANYA, f"pres:{q.pk}:yes"))
    confirm = bot.sent("sendMessage", ANYA)[-1]
    assert "Предложить итог за 20 монет — вернутся, если итог встанет" in confirm["text"]
    assert confirm["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"presok:{q.pk}:yes"
    assert balance(anya) == before  # nothing is taken before the confirmation
    q.refresh_from_db()
    assert q.resolution_state == ""

    handle_update(press(ANYA, f"presok:{q.pk}:yes"))
    q.refresh_from_db()
    assert q.resolution_state == "pending" and q.proposed_by_id == anya.pk and balance(anya) == before - BOND
    assert "Готово, итог предложен" in bot.texts(ANYA)[-1]

    assert not bot.sent(chat_id=BORIS)  # queued, not sent from the poller
    assert {j.trader.telegram_chat_id for j in BotJob.objects.all()} == {BORIS, CREATOR}
    drain_jobs()
    for user in (BORIS, CREATOR):
        note = bot.sent("sendMessage", user)[-1]
        assert "его предложил участник: <b>ДА</b>" in note["text"]
        assert note["reply_markup"]["inline_keyboard"][0][0] == {"text": "Оспорить", "callback_data": f"disp:{q.pk}"}


def test_participant_declines_the_bond(bot, people, market):
    anya = people[1]
    q = overdue(market)
    before = balance(anya)
    handle_update(press(ANYA, f"pres:{q.pk}:no"))
    handle_update(press(ANYA, f"presno:{q.pk}"))
    q.refresh_from_db()
    assert q.resolution_state == "" and balance(anya) == before


def test_late_participant_learns_the_outcome_is_taken(bot, people, market):
    _, anya, boris, _ = people
    q = overdue(market)
    handle_update(press(ANYA, f"pres:{q.pk}:yes"))
    handle_update(press(BORIS, f"pres:{q.pk}:no"))  # both saw the question
    handle_update(press(ANYA, f"presok:{q.pk}:yes"))
    before = balance(boris)
    handle_update(press(BORIS, f"presok:{q.pk}:no", 12))
    assert "другой участник" in bot.texts(BORIS)[-1]
    handle_update(press(BORIS, f"pres:{q.pk}:no", 13))
    assert "другой участник" in bot.texts(BORIS)[-1]
    q.refresh_from_db()
    assert q.proposed_outcome == "yes" and balance(boris) == before
    assert LedgerEntry.objects.filter(kind=LedgerEntry.Kind.PROPOSAL_BOND).count() == 1


def test_double_press_takes_one_bond_and_queues_one_broadcast(bot, people, market):
    anya = people[1]
    q = overdue(market)
    before = balance(anya)
    handle_update(press(ANYA, f"presok:{q.pk}:yes"))
    jobs = BotJob.objects.count()
    handle_update(press(ANYA, f"presok:{q.pk}:yes"))
    assert balance(anya) == before - BOND and BotJob.objects.count() == jobs
    assert bot.texts(ANYA)[-1] == "Ты уже предложил итог этого рынка."


def test_outsider_cannot_propose_from_a_forged_press(bot, people, market):
    q = overdue(market)
    handle_update(press(STRANGER, f"presok:{q.pk}:yes"))
    assert "только участник" in bot.texts(STRANGER)[-1]
    forged = press(STRANGER, f"presok:{q.pk}:yes")
    forged["callback_query"]["message"]["chat"]["id"] = ANYA
    handle_update(forged)
    q.refresh_from_db()
    assert q.resolution_state == ""


def test_bond_error_reaches_the_participant(bot, people, market):
    anya = people[1]
    q = overdue(market)
    Trader.objects.filter(pk=anya.pk).update(balance=D(5))
    handle_update(press(ANYA, f"presok:{q.pk}:yes"))
    assert "не хватает" in bot.texts(ANYA)[-1]
    assert not BotJob.objects.exists()


def test_multi_participant_marks_options_in_the_bot(bot, people, multi):
    from market import bot_resolution
    root, subs = multi
    anya = people[1]
    keyboard = bot_resolution.participant_keyboard(root)
    assert "Отменить" not in str(keyboard) and f"pres:{root.pk}:" not in str(keyboard)
    handle_update(press(ANYA, f"presopt:{root.pk}:y?"))
    edited = bot.sent("editMessageText", ANYA)[-1]
    assert "1. до 20:00 — ДА" in edited["text"] and "2. до 21:00 — не выбрано" in edited["text"]
    assert f"pres:{root.pk}:" not in str(edited["reply_markup"])
    handle_update(press(ANYA, f"pres:{root.pk}:y?"))  # an incomplete code doesn't get to the bond
    assert "для каждого варианта" in bot.texts(ANYA)[-1]
    handle_update(press(ANYA, f"presopt:{root.pk}:yn"))
    assert f"pres:{root.pk}:yn" in str(bot.sent("editMessageText", ANYA)[-1]["reply_markup"])
    handle_update(press(ANYA, f"pres:{root.pk}:yn"))
    assert "до 20:00: <b>ДА</b>" in bot.texts(ANYA)[-1] and "до 21:00: <b>НЕТ</b>" in bot.texts(ANYA)[-1]
    handle_update(press(ANYA, f"presok:{root.pk}:yn"))
    root.refresh_from_db()
    assert root.proposed_by_id == anya.pk
    assert [s.proposed_outcome for s in root.submarkets.order_by("id")] == ["yes", "no"]
    assert ledger(anya, LedgerEntry.Kind.PROPOSAL_BOND) == [-BOND]


def test_creator_disputes_a_participants_outcome_in_the_bot(bot, people, proposed):
    creator = people[0]
    before = balance(creator)
    handle_update(press(CREATOR, f"disp:{proposed.pk}"))
    handle_update(press(CREATOR, f"dispok:{proposed.pk}"))
    assert bot.texts(CREATOR)[-1] == "Спор открыт, списано 20 монет. Итог проверит модератор."
    assert balance(creator) == before - STAKE and Dispute.objects.filter(trader=creator).exists()


# --- the bot: proof -----------------------------------------------------------------------------------------------------

@pytest.fixture
def disputed(people, proposed):
    services.open_dispute(people[2], proposed)
    proposed.refresh_from_db()
    return proposed


def test_request_reaches_the_author_and_a_photo_is_stored(bot, people, disputed):
    drain_jobs()
    request = bot.sent("sendMessage", ANYA)[-1]["text"]
    assert "оспорили" in request and "фото, скриншот или ссылку" in request and "24 часа" in request
    assert "модератор" in request

    handle_update(text(ANYA, "Да точно так и было"))
    assert "Пришли фото, скриншот или ссылку" in bot.texts(ANYA)[-1]
    disputed.refresh_from_db()
    assert not services.has_evidence(disputed)

    handle_update(photo(ANYA, "С сайта"))
    disputed.refresh_from_db()
    assert disputed.evidence_file_id == "big" and disputed.evidence_note == "С сайта"
    assert disputed.evidence.read() == b"\xff\xd8fake-jpeg"
    assert bot.texts(ANYA)[-1] == "Доказательство получено — его увидит модератор."

    # The proof is here: plain text is no longer part of this dialog.
    handle_update(text(ANYA, "спасибо"))
    assert "Пришли фото, скриншот или ссылку" not in bot.texts(ANYA)[-1]


def test_link_counts_as_proof(bot, people, disputed):
    handle_update(text(ANYA, "Вот https://example.com/result"))
    disputed.refresh_from_db()
    assert disputed.evidence_note == "Вот https://example.com/result" and not disputed.evidence_file_id
    assert services.has_evidence(disputed)


def test_only_the_author_can_send_proof(bot, people, disputed):
    handle_update(photo(BORIS))
    disputed.refresh_from_db()
    assert not services.has_evidence(disputed)


def test_after_the_deadline_the_bot_does_not_wait_for_proof(bot, people, disputed):
    Question.objects.filter(pk=disputed.pk).update(evidence_requested_at=timezone.now() - timedelta(hours=25))
    handle_update(text(ANYA, "https://example.com/late"))
    disputed.refresh_from_db()
    assert not services.has_evidence(disputed)


def test_moderator_sees_the_author_and_the_proof(people, disputed, settings):
    settings.SITE_URL = "https://example.test"
    with patch("market.disputes_bot.bot.render") as render:
        disputes_bot.show_card(123, disputed)
        card = render.call_args.args[1]
        assert "Итог указал: участник Аня" in card and "Доказательство не прислано" in card
        assert "ждём до" in card
        latest = disputed.disputes.get()
        disputes_bot.handle_callback(123, f"ds:ask:no:{disputed.pk}:{latest.pk}")
        assert "участник теряет залог" in render.call_args.args[1]
        disputes_bot.handle_callback(123, f"ds:ask:yes:{disputed.pk}:{latest.pk}")
        assert "ему возвращается залог и доля комиссии" in render.call_args.args[1]
        Question.objects.filter(pk=disputed.pk).update(evidence_requested_at=timezone.now() - timedelta(hours=25))
        disputes_bot.show_card(123, Question.objects.get(pk=disputed.pk))
        assert "срок вышел" in render.call_args.args[1]
        Question.objects.filter(pk=disputed.pk).update(evidence_file_id="P")
        disputes_bot.show_card(123, Question.objects.get(pk=disputed.pk))
        card = render.call_args.args[1]
        assert "Доказательство: прислано" in card and "не прислано" not in card


def test_moderator_sees_the_creator_as_the_author(people, market, settings):
    settings.SITE_URL = "https://example.test"
    q = services.propose_resolution(people[0], market, "yes")
    services.open_dispute(people[2], q)
    q.refresh_from_db()
    with patch("market.disputes_bot.bot.render") as render:
        disputes_bot.show_card(123, q)
    assert "Итог указал: автор рынка" in render.call_args.args[1]


# --- chat admins ---------------------------------------------------------------------------------------------------------

ADMIN = 1100


@pytest.fixture
def chat_market(people):
    creator, anya, boris, _ = people
    admin = make("Ольга", ADMIN)
    group = Group.objects.create(name="Группа ПИ-24", kind="chat", telegram_chat_id=-100555, code=chats.new_code(),
                                 bot_active=True, member_count=30)
    for trader in (creator, anya, boris, admin):
        GroupMembership.objects.create(group=group, trader=trader, status="active", checked_at=timezone.now())
    now = timezone.now()
    q = services.create_market(creator, title="Сдаст ли Петя матан?", criteria="По ведомости", category="study",
                               closes_at=now + timedelta(hours=2), resolves_at=now + timedelta(hours=3), group=group)
    services.buy(anya, q, "yes", 100)
    services.buy(boris, q, "no", 50)
    q = services.propose_by_participant(anya, overdue(q), "yes")
    services.open_dispute(boris, q)
    q.refresh_from_db()
    return q


def run_chat_tick(bot_):
    with patch("market.telegram.api", side_effect=bot_):
        return chat_disputes.tick(timezone.now())


def test_chat_admins_see_a_participants_outcome_without_proof(people, chat_market):
    bot_ = ChatBot([ADMIN])
    assert run_chat_tick(bot_)["sent"] == 1
    card = bot_.sent("sendMessage", ADMIN)[0]["text"]
    assert "Итог участника: <b>ДА</b>" in card and "Итог предложил участник" in card
    assert "Доказательство не прислано." in card
    assert "Аня" not in card  # the bot never says who bet what


def test_chat_admins_get_proof_that_arrives_later(people, chat_market):
    anya = people[1]
    bot_ = ChatBot([ADMIN])
    run_chat_tick(bot_)
    services.attach_evidence(anya, chat_market, evidence_file_id="P1")
    assert run_chat_tick(bot_)["proof"] == 1
    assert bot_.sent("sendPhoto", ADMIN)[-1]["photo"] == "P1"
    edited = bot_.sent("editMessageText", ADMIN)[-1]["text"]
    assert "Доказательство участника — выше." in edited and "не прислано" not in edited
    assert "proof" not in run_chat_tick(bot_)  # shown once
    assert len(bot_.sent("sendPhoto", ADMIN)) == 1


def test_chat_admin_confirms_a_participants_outcome(people, chat_market):
    anya = people[1]
    bot_ = ChatBot([ADMIN])
    run_chat_tick(bot_)
    review = chat_market.chat_review
    callback = {"id": "cb", "from": {"id": ADMIN}, "data": f"cdis:{review.pk}:ok",
                "message": {"message_id": 50, "chat": {"id": ADMIN, "type": "private"}}}
    with patch("market.telegram.api", side_effect=bot_):
        chat_disputes.handle_callback(callback)
    chat_market.refresh_from_db()
    assert chat_market.status == Question.Status.RESOLVED and not chat_market.creator_at_fault
    assert ledger(anya, LedgerEntry.Kind.PROPOSAL_BOND_REFUND) == [BOND]
    assert "итог участника верный (ДА)" in bot_.sent("editMessageText", ADMIN)[-1]["text"]


# --- a real race (PostgreSQL row locks) ---------------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_two_participants_racing_give_one_outcome_and_one_bond():
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row locks")
    creator = make("Автор", 2001)
    first, second = make("Аня", 2002), make("Борис", 2003)
    now = timezone.now()
    q = services.create_market(creator, title="Гонка?", criteria="c", category="fun",
                               closes_at=now + timedelta(hours=2), resolves_at=now + timedelta(hours=3))
    services.buy(first, q, "yes", 50)
    services.buy(second, q, "no", 50)
    q = overdue(q)
    barrier = Barrier(2)

    def propose(args):
        trader, outcome = args
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
            barrier.wait(timeout=10)
            services.propose_by_participant(trader, q, outcome)
            return "ok"
        except MarketError as exc:
            return str(exc)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(propose, [(first, "yes"), (second, "no")]))
    assert results.count("ok") == 1 and any("другой участник" in r for r in results)
    assert LedgerEntry.objects.filter(kind=LedgerEntry.Kind.PROPOSAL_BOND).count() == 1
    q.refresh_from_db()
    winner = first if results[0] == "ok" else second
    assert q.proposed_by_id == winner.pk and q.proposed_outcome == ("yes" if winner == first else "no")
    assert sorted(ledger(winner, LedgerEntry.Kind.PROPOSAL_BOND)) == [-BOND]
