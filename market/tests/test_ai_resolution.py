"""Outcome from AI for public events (spec E4, phase 3e). The model and Telegram are always mocked."""
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.test import override_settings
from django.utils import timezone

from market import ai_resolution as ai, assist, resolution, services
from market.api.serializers import author
from market.models import BotJob, Dispute, Event, Group, GroupMembership, LedgerEntry, Question, Trader
from market.telegram import handle_update
from market.tests.test_bot_resolution_dialog import (ANYA, BORIS, CREATOR, bot, make, media, people, press,  # noqa: F401
                                                     text)

URL = "https://liga.example/match/1"
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def turned_on():
    with override_settings(AI_RESOLVE_ENABLED=True, AI_RESOLVE_API_KEY="k", AI_RESOLVE_MODEL="m"):
        yield


def answer(outcome="yes", url=URL, confidence=0.99, reason="Лига опубликовала протокол.", cited=(URL,), **extra):
    return {"content": json.dumps({"outcome": outcome, "url": url, "confidence": confidence, "reason": reason, **extra}),
            "annotations": [{"type": "url_citation", "url_citation": {"url": u, "title": "t"}} for u in cited]}


def http(message):
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": message}]}
    return patch("market.ai_resolution.requests.post", return_value=response)


@pytest.fixture
def market(people):
    creator, anya, boris, _ = people
    now = timezone.now()
    q = services.create_market(creator, title="Победит ли Зенит в матче?", criteria="ДА, если победит по протоколу",
                               category="sport", closes_at=now + timedelta(hours=2),
                               resolves_at=now + timedelta(hours=3), ai_check=True, ai_source="протокол лиги")
    services.buy(anya, q, "yes", 100)
    services.buy(boris, q, "no", 50)
    moment = now - timedelta(minutes=1)
    Question.objects.filter(pk=q.pk).update(status=Question.Status.CLOSED, deadline=moment, resolves_at=moment)
    q.refresh_from_db()
    return q


def looked_at(question, message=None):
    with http(message or answer()):
        ai.process_queue()
    question.refresh_from_db()
    return question


def silent(question, hours=25):
    moment = timezone.now() - timedelta(hours=hours)
    Question.objects.filter(pk=question.pk).update(resolves_at=moment, deadline=moment, asked_at=moment)
    question.refresh_from_db()
    return question


def total():
    return sum(Trader.objects.values_list("balance", flat=True), Decimal(0))


# --- the model's answer ---------------------------------------------------------------------------

def test_a_sure_answer_with_a_cited_link_is_accepted():
    assert ai.check(answer()) == {"outcome": "yes", "url": URL, "reason": "Лига опубликовала протокол."}


@pytest.mark.parametrize("message", [
    answer(outcome="unknown", url=None, cited=()),
    answer(confidence=0.94),
    answer(cited=()),  # no search result behind the link
    answer(cited=("https://other.example/",)),
    answer(url="http://liga.example/match/1", cited=("http://liga.example/match/1",)),
    answer(url="https://user:pass@liga.example/", cited=("https://user:pass@liga.example/",)),
    answer(url=None),
])
def test_doubt_or_an_unproved_link_is_no_proposal(message):
    assert ai.check(message) is None


@pytest.mark.parametrize("message", [
    {"content": "не json"},
    {"content": None},
    {"content": json.dumps({"outcome": "yes"})},
    answer(extra="лишнее поле"),
    answer(outcome="cancel"),
    answer(confidence="0.99"),
    answer(confidence=float("nan")),
    answer(confidence=True),
])
def test_a_broken_answer_is_refused(message):
    with pytest.raises(ai.NoAnswer):
        ai.check(message)


def test_a_fenced_answer_is_read_and_a_long_reason_is_cut():
    message = answer(reason="я" * 500)
    message["content"] = f"```json\n{message['content']}\n```"
    assert len(ai.check(message)["reason"]) == ai.REASON_MAX


def test_the_model_gets_only_the_market_text_and_a_web_search_tool(market):
    with override_settings(AI_RESOLVE_ENGINE="exa"):
        body = ai.request_body(market)
    assert body["tools"] == [{"type": "openrouter:web_search", "parameters": {"engine": "exa", "max_results": 5}}]
    sent = json.dumps(body, ensure_ascii=False)
    assert "Победит ли Зенит" in sent and "протокол лиги" in sent
    assert str(CREATOR) not in sent and "Автор" not in sent


# --- the worker -----------------------------------------------------------------------------------

def test_worker_keeps_the_proposal_and_touches_no_money(market):
    before = total()
    looked_at(market)
    assert (market.ai_state, market.ai_outcome, market.ai_evidence_url) == ("proposed", "yes", URL)
    assert market.resolution_state == "" and total() == before
    assert Event.objects.filter(name=ai.EVENT, props__result="proposed").exists()


def test_a_second_pass_does_not_ask_the_model_again(market):
    looked_at(market)
    with http(answer()) as post:
        assert ai.process_queue() == 0
    post.assert_not_called()


def test_not_found_means_the_usual_question(market):
    looked_at(market, answer(outcome="unknown", url=None, cited=()))
    assert market.ai_state == "none" and market.ai_outcome == ""


def test_a_failure_is_retried_then_given_up(market):
    with patch("market.ai_resolution.requests.post", side_effect=requests.Timeout):
        ai.process_queue()
    market.refresh_from_db()
    assert market.ai_state == "checking" and market.ai_attempts == 1
    with patch("market.ai_resolution.requests.post", side_effect=requests.Timeout) as post:
        ai.process_queue()  # the pause before the retry
        post.assert_not_called()
        ai.process_queue(now=timezone.now() + timedelta(minutes=2))
    market.refresh_from_db()
    assert market.ai_state == "none" and market.ai_attempts == 2


def test_an_answer_after_the_creator_was_asked_is_dropped(market):
    def late(*args, **kwargs):
        Question.objects.filter(pk=market.pk).update(asked_at=timezone.now())
        response = MagicMock()
        response.json.return_value = {"choices": [{"message": answer()}]}
        return response
    with patch("market.ai_resolution.requests.post", side_effect=late):
        ai.process_queue()
    market.refresh_from_db()
    assert market.ai_state == "none" and market.ai_outcome == ""


def test_daily_limit_gives_up_without_a_request(market):
    with override_settings(AI_RESOLVE_DAILY_LIMIT=0), http(answer()) as post:
        ai.process_queue()
    post.assert_not_called()
    market.refresh_from_db()
    assert market.ai_state == "none"


def test_switched_off_the_worker_does_nothing_and_nobody_waits(market):
    with override_settings(AI_RESOLVE_ENABLED=False), http(answer()) as post:
        assert ai.process_queue() == 0
        assert not ai.waiting_for_ai(market, timezone.now())
    post.assert_not_called()


# --- which markets --------------------------------------------------------------------------------

def _fields(**changes):
    now = timezone.now()
    return {"title": "Победит ли Зенит в матче?", "criteria": "ДА, если победит по протоколу", "category": "sport",
            "closes_at": now + timedelta(hours=2), "resolves_at": now + timedelta(hours=3),
            "ai_check": True, "ai_source": "протокол лиги", **changes}


def test_chat_market_is_never_marked_its_text_stays_private(people):
    creator = people[0]
    group = Group.objects.create(name="Чат", kind="chat", telegram_chat_id=-500, bot_active=True)
    GroupMembership.objects.create(group=group, trader=creator, status="active")
    q = services.create_market(creator, group=group, **_fields())
    assert q.ai_check is False and q.ai_source == ""


def test_market_with_options_and_without_source_are_not_marked(people):
    creator = people[0]
    assert services.create_market(creator, options=["Аня", "Боря"], **_fields()).ai_check is False
    assert services.create_market(creator, **_fields(ai_source="")).ai_check is False


def test_a_link_as_the_source_is_refused(people):
    with pytest.raises(services.MarketError):
        services.create_market(people[0], **_fields(ai_source="https://evil.example"))


def test_helper_returns_a_source_name_but_never_a_link():
    now = timezone.now()
    data = {"forbidden": None, "question": None, "why": None, "options": None, "category": "sport",
            "criteria": "ДА — если победит.\nНЕТ — если нет.",
            "closes_at": (now + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
            "resolves_at": (now + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")}
    title = "Победит ли Зенит в матче?"
    assert assist.check({**data, "public_source": "протокол лиги"}, title, None, now)["public_source"] == "протокол лиги"
    assert assist.check({**data, "public_source": "https://liga.example"}, title, None, now)["public_source"] is None
    assert assist.check({**data, "public_source": None}, title, None, now)["public_source"] is None
    assert assist.check(data, title, None, now)["public_source"] is None


# --- the creator ----------------------------------------------------------------------------------

def test_creators_question_waits_for_ai_but_not_past_the_limit(bot, market):
    resolution.tick(timezone.now())
    market.refresh_from_db()
    assert market.asked_at is None and not bot.sent("sendMessage", CREATOR)
    resolution.tick(market.resolves_at + timedelta(minutes=11))
    market.refresh_from_db()
    assert market.asked_at is not None
    assert "Чем закончилось?" in bot.sent("sendMessage", CREATOR)[-1]["text"]


def test_creator_gets_ais_outcome_with_a_link_and_two_buttons(bot, market):
    looked_at(market)
    resolution.tick(timezone.now())
    ask = bot.sent("sendMessage", CREATOR)[-1]
    assert "ИИ считает: ДА" in ask["text"] and URL in ask["text"]
    assert [b["callback_data"] for b in ask["reply_markup"]["inline_keyboard"][0]] == [f"aiok:{market.pk}", f"aino:{market.pk}"]


def test_confirming_makes_it_the_creators_outcome_with_the_link(bot, people, market):
    looked_at(market)
    resolution.tick(timezone.now())
    handle_update(press(CREATOR, f"aiok:{market.pk}"))
    market.refresh_from_db()
    assert market.proposed_by_id == people[0].pk and market.proposed_by_ai is False
    assert market.proposed_outcome == "yes" and market.evidence_note == URL
    assert market.resolution_state == Question.Resolution.PENDING
    handle_update(press(CREATOR, f"aiok:{market.pk}"))  # a double tap changes nothing
    market.refresh_from_db()
    assert market.proposed_by_id == people[0].pk
    Question.objects.filter(pk=market.pk).update(dispute_until=timezone.now() - timedelta(seconds=1))
    services.finalize_resolution(market)
    assert LedgerEntry.objects.filter(trader=people[0], kind=LedgerEntry.Kind.CREATOR_FEE).exists()


def test_someone_else_cannot_confirm(bot, people, market):
    looked_at(market)
    resolution.tick(timezone.now())
    handle_update(press(ANYA, f"aiok:{market.pk}"))
    market.refresh_from_db()
    assert market.resolution_state == ""


def test_correcting_ai_drops_the_proposal_and_silence_goes_to_participants(bot, people, market):
    looked_at(market)
    resolution.tick(timezone.now())
    handle_update(press(CREATOR, f"aino:{market.pk}"))
    market.refresh_from_db()
    assert market.ai_state == "none" and market.resolution_state == ""
    assert bot.sent("sendMessage", CREATOR)[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"res:{market.pk}:yes"
    silent(market)
    done = resolution.tick(timezone.now())
    market.refresh_from_db()
    assert done["ai_stated"] == 0 and market.proposed_by_ai is False and market.participants_asked_at is not None


# --- silence --------------------------------------------------------------------------------------

def stated(market):
    looked_at(market)
    silent(market)
    done = resolution.tick(timezone.now())
    assert done["ai_stated"] == 1
    market.refresh_from_db()
    return market


def test_silence_puts_the_proposal_into_the_dispute_window_and_asks_no_participants(bot, people, market):
    stated(market)
    assert market.proposed_by_ai and market.proposed_by_id is None and market.proposal_bond == 0
    assert market.resolution_state == Question.Resolution.PENDING and market.evidence_note == URL
    assert market.participants_asked_at is None
    jobs = BotJob.objects.filter(question=market)
    assert {j.trader_id for j in jobs} == {people[0].pk, people[1].pk, people[2].pk}
    assert "Итог предложил ИИ" in jobs.first().payload["text"] and URL in jobs.first().payload["text"]
    assert "Оспорить" in json.dumps(jobs.first().payload, ensure_ascii=False)


def test_stood_outcome_burns_the_creators_share_and_returns_the_subsidy(bot, people, market):
    creator = people[0]
    stated(market)
    subsidy_before = creator.ledger.filter(kind=LedgerEntry.Kind.SUBSIDY_RETURN).count()
    Question.objects.filter(pk=market.pk).update(dispute_until=timezone.now() - timedelta(seconds=1))
    services.finalize_resolution(market)
    market.refresh_from_db()
    assert market.status == Question.Status.RESOLVED and market.outcome == "yes" and not market.creator_at_fault
    assert not LedgerEntry.objects.filter(kind__in=[LedgerEntry.Kind.CREATOR_FEE, LedgerEntry.Kind.PROPOSER_FEE]).exists()
    assert creator.ledger.filter(kind=LedgerEntry.Kind.SUBSIDY_RETURN).count() == subsidy_before + 1
    assert author(creator)["resolved_by_ai"] == 1 and author(creator)["resolved"] == 0


def test_upheld_dispute_returns_the_stake_without_blaming_the_creator(bot, people, market):
    creator, _, boris, _ = people
    stated(market)
    balance = Trader.objects.get(pk=boris.pk).balance
    dispute = services.open_dispute(boris, market, "Матч перенесли")
    market.refresh_from_db()
    assert market.evidence_requested_at is None  # the link is the proof, and there is no author to ask
    services.moderator_decide(market, "no")
    market.refresh_from_db()
    dispute.refresh_from_db()
    assert dispute.upheld is True and market.outcome == "no" and not market.creator_at_fault
    assert creator.ledger.filter(kind=LedgerEntry.Kind.SUBSIDY_RETURN).exists()
    assert not LedgerEntry.objects.filter(kind=LedgerEntry.Kind.CREATOR_FEE).exists()
    assert Trader.objects.get(pk=boris.pk).balance > balance


def test_creator_is_told_the_upheld_dispute_is_no_fault_of_theirs(bot, people, market):
    from market.bot_resolution import participant_outcome_decided
    from market.models import Notification
    stated(market)
    services.open_dispute(people[2], market, "Матч перенесли")
    services.moderator_decide(market, "no")
    participant_outcome_decided(market.pk, "no", True)
    assert "Итог предлагал ИИ" in Notification.objects.get(key=f"moderator:{market.pk}").text
    assert Event.objects.filter(name=ai.EVENT, props__result="dispute_upheld").exists()


def test_moderator_may_cancel_ais_outcome_without_blaming_the_creator(bot, people, market):
    creator, _, boris, _ = people
    stated(market)
    services.open_dispute(boris, market, "Матч отменили")
    services.moderator_decide(market, "cancel")
    market.refresh_from_db()
    assert market.status == Question.Status.ANNULLED and not market.creator_at_fault
    assert Dispute.objects.get(question=market).upheld is True


def test_creator_may_dispute_ais_outcome_but_a_stranger_may_not(bot, people, market):
    creator, _, _, stranger = people
    stated(market)
    with pytest.raises(services.MarketError):
        services.open_dispute(stranger, market, "Не участвовал")
    assert services.open_dispute(creator, market, "Итог неверный").trader_id == creator.pk


def test_market_card_says_the_outcome_came_from_ai(bot, people, market):
    stated(market)
    state = resolution.public_state(market, people[1])
    assert state["by_ai"] is True and state["by_participant"] is False and state["note"] == URL
    assert state["can_dispute"] is True
    assert resolution.public_state(market, people[0])["can_dispute"] is True


def test_propose_by_ai_needs_a_proposal_and_a_silent_day(people, market):
    with pytest.raises(services.MarketError):
        services.propose_by_ai(market)  # no proposal yet
    looked_at(market)
    with pytest.raises(services.MarketError):
        services.propose_by_ai(market)  # the creator still has time
