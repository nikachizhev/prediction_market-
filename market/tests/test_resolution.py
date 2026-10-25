"""Spec E4: creator states the outcome with proof, 24h dispute window, moderator, 72h timeout."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.db.models import Sum
from django.test import override_settings
from django.utils import timezone

from market import resolution, services
from market.models import Dispute, LedgerEntry, Question, Trader
from market.services import MarketError

D = Decimal


def make(name, balance=1000, chat=None):
    return Trader.objects.create(name=name, pseudonym=name, balance=D(balance), telegram_chat_id=chat)


@pytest.fixture(autouse=True)
def media(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        yield


@pytest.fixture
def people(db):
    return make("Автор", chat=1), make("Аня", chat=2), make("Борис", chat=3)


@pytest.fixture
def market(people):
    creator, anya, boris = people
    now = timezone.now()
    q = services.create_market(creator, title="Снег до декабря?", criteria="Росгидромет", category="weather",
                               closes_at=now + timedelta(hours=2), resolves_at=now + timedelta(hours=3))
    services.buy(anya, q, "yes", 100)
    services.buy(boris, q, "no", 50)
    # Betting is over.
    Question.objects.filter(pk=q.pk).update(deadline=now - timedelta(minutes=1), resolves_at=now - timedelta(minutes=1))
    q.refresh_from_db()
    return q


def photo():
    return ContentFile(b"\xff\xd8fake-jpeg", name="proof.jpg")


def propose(q, creator, outcome="yes", **kw):
    return services.propose_resolution(creator, q, outcome, evidence=photo(), note="Скрин", **kw)


def test_only_creator_after_close_with_photo(people, market):
    creator, anya, _ = people
    with pytest.raises(MarketError, match="создатель"):
        propose(market, anya)
    with pytest.raises(MarketError, match="фото"):
        services.propose_resolution(creator, market, "yes")
    q = propose(market, creator)
    assert q.resolution_state == "pending" and q.status == Question.Status.CLOSED
    assert q.dispute_until - q.proposed_at == timedelta(hours=24) and q.evidence.name.startswith("evidence/")
    with pytest.raises(MarketError, match="уже указан"):
        propose(q, creator, "no")


def test_cannot_propose_while_betting(people):
    creator = people[0]
    now = timezone.now()
    q = services.create_market(creator, title="t?", criteria="c", category="tech",
                               closes_at=now + timedelta(hours=2), resolves_at=now + timedelta(hours=3))
    with pytest.raises(MarketError, match="Приём"):
        propose(q, creator)


def test_cancel_needs_no_photo(people, market):
    q = services.propose_resolution(people[0], market, "cancel")
    assert q.proposed_outcome == "cancel"


def test_no_dispute_finalizes_after_window(people, market):
    creator, anya, boris = people
    q = propose(market, creator)
    with pytest.raises(MarketError, match="открыто"):
        services.finalize_resolution(q)
    Question.objects.filter(pk=q.pk).update(dispute_until=timezone.now() - timedelta(seconds=1))
    q = services.finalize_resolution(q)
    assert q.status == Question.Status.RESOLVED and q.outcome == "yes" and q.resolution_state == "final"
    creator.refresh_from_db()
    assert creator.ledger.filter(kind=LedgerEntry.Kind.CREATOR_FEE).exists()


def test_dispute_rules(people, market):
    creator, anya, boris = people
    outsider = make("Чужой")
    q = propose(market, creator)
    with pytest.raises(MarketError, match="участник"):
        services.open_dispute(outsider, q)
    with pytest.raises(MarketError, match="участник"):
        services.open_dispute(creator, q)
    services.open_dispute(boris, q, "Снег был")
    boris.refresh_from_db()
    assert boris.ledger.filter(kind=LedgerEntry.Kind.DISPUTE).get().amount == D(-20)
    with pytest.raises(MarketError, match="уже"):
        services.open_dispute(boris, q)
    q.refresh_from_db()
    assert q.resolution_state == "disputed"
    # A dispute stops automatic finalization.
    Question.objects.filter(pk=q.pk).update(dispute_until=timezone.now() - timedelta(seconds=1))
    with pytest.raises(MarketError):
        services.finalize_resolution(q)
    with pytest.raises(MarketError, match="закрыто"):
        services.open_dispute(anya, q)


def test_upheld_dispute_refunds_stake_and_punishes_creator(people, market):
    creator, anya, boris = people
    q = propose(market, creator, "yes")
    services.open_dispute(boris, q)
    q = services.moderator_decide(q, "no")
    assert q.outcome == "no" and q.creator_at_fault
    boris.refresh_from_db(); creator.refresh_from_db()
    assert boris.ledger.filter(kind=LedgerEntry.Kind.DISPUTE_REFUND).get().amount == D(20)
    assert not creator.ledger.filter(kind=LedgerEntry.Kind.SUBSIDY_RETURN).exists()
    assert Dispute.objects.get().upheld is True
    from market.api.serializers import author
    assert author(creator)["disputes_lost"] == 1


def test_rejected_dispute_burns_stake(people, market):
    creator, anya, boris = people
    q = propose(market, creator, "yes")
    services.open_dispute(boris, q)
    before = Trader.objects.aggregate(t=Sum("balance"))["t"]
    q = services.moderator_decide(q, "yes")
    assert not q.creator_at_fault and Dispute.objects.get().upheld is False
    boris.refresh_from_db()
    assert not boris.ledger.filter(kind=LedgerEntry.Kind.DISPUTE_REFUND).exists()
    assert Trader.objects.aggregate(t=Sum("balance"))["t"] > before  # payouts happened


def test_moderator_needs_open_dispute(people, market):
    q = propose(market, people[0])
    with pytest.raises(MarketError, match="спора"):
        services.moderator_decide(q, "no")


def test_public_state(people, market):
    creator, anya, boris = people
    assert resolution.public_state(market, anya) is None
    q = propose(market, creator)
    state = resolution.public_state(q, anya)
    assert state["proposed_outcome"] == "yes" and state["evidence_url"].startswith("/media/evidence/")
    assert state["can_dispute"] and not resolution.public_state(q, creator)["can_dispute"]


@pytest.fixture
def bot():
    with patch("market.bot_resolution.telegram.send") as send:
        yield send


def test_worker_asks_reminds_and_cancels_after_72h(people, market, bot):
    creator = people[0]
    now = timezone.now()
    resolution.tick(now)
    assert bot.call_count == 1 and "Чем закончилось?" in bot.call_args.args[1]
    resolution.tick(now)
    assert bot.call_count == 1  # asked once
    resolution.tick(now + timedelta(hours=24, minutes=1))
    resolution.tick(now + timedelta(hours=48, minutes=1))
    market.refresh_from_db()
    assert market.reminders_sent == 2 and bot.call_count == 3
    resolution.tick(now + timedelta(hours=72, minutes=1))
    market.refresh_from_db()
    assert market.status == Question.Status.ANNULLED and market.resolution_state == "final"
    creator.refresh_from_db()
    assert creator.balance == D(1000)  # subsidy back: everyone is refunded


def test_worker_finalizes_and_sends_results_once(people, market, bot):
    creator, anya, boris = people
    propose(market, creator, "yes")
    later = timezone.now() + timedelta(hours=25)
    with patch("django.utils.timezone.now", return_value=later):
        done = resolution.tick(later)
    assert done["finalized"] == 1 and done["results"] == 1
    texts = {c.args[0]: (c.args[1], c.kwargs) for c in bot.call_args_list}
    assert "Выплата" in texts[2][0] and texts[2][1].get("message_effect_id")
    assert "мимо" in texts[3][0] and "message_effect_id" not in texts[3][1]
    calls = bot.call_count
    resolution.tick(later)
    assert bot.call_count == calls


def test_telegram_failure_does_not_block_money(people, market, bot):
    bot.side_effect = RuntimeError("telegram down")
    propose(market, people[0], "no")
    later = timezone.now() + timedelta(hours=25)
    with patch("django.utils.timezone.now", return_value=later):
        done = resolution.tick(later)
    market.refresh_from_db()
    assert done["finalized"] == 1 and market.status == Question.Status.RESOLVED and market.results_sent_at is None
