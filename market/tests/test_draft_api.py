"""Phase 3d API for quick creation (spec E9.4, E9.5; docs/api.md «Фаза 3d»): GET /api/drafts/{code},
`draft` and the new `assist` values in POST /api/markets, `options` and `mode` of the helper, `oneline_wizard`.

Telegram and the model are always mocked.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from market import assist, bot_draft
from market.api import serializers as s
from market.models import Event, LedgerEntry, MarketDraft, Question
from market.tests.test_api import anya, boris, bot_send, bot_token, create_payload, trader  # noqa: F401 (fixtures)
from market.tests.test_assist import answer, events, model, reply
from market.tests.test_chat_api import chat, join, telegram

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("bot_token")]

ANYA, BORIS = 1001, 1002
NOT_FOUND = {"error": "Черновик не найден."}


@pytest.fixture(autouse=True)
def helper_on():
    with override_settings(LLM_API_KEY="test-key", ASSIST_ENABLED=True, TELEGRAM_APP_NAME="app"):
        yield


def make_draft(group, user_id, **kw):
    now = timezone.now()
    fields = dict(code=bot_draft.new_code(), trader=trader(user_id), group=group, source_text="спорим, перенесут",
                  source_message_id=40, assisted=True, title="Перенесут ли коллоквиум по матанализу?",
                  criteria="ДА — если перенесут.\nНЕТ — если пройдёт по расписанию.", category="study",
                  closes_at=now + timedelta(days=2), resolves_at=now + timedelta(days=3), options=[],
                  expires_at=now + timedelta(hours=24))
    fields.update(kw)
    return MarketDraft.objects.create(**fields)


def from_draft(draft, **kw):
    fields = dict(title=draft.title, criteria=draft.criteria, category=draft.category, chat=draft.group.code,
                  draft=draft.code)
    fields.update(kw)
    return create_payload(**fields)


# --- GET /api/drafts/{code} -----------------------------------------------------------------

def test_own_draft(anya):
    group = chat()
    join(group, ANYA)
    draft = make_draft(group, ANYA, options=["ПИ-21", "ПИ-22"])
    code, data = anya.get(f"drafts/{draft.code}")
    assert code == 200
    assert data == {
        "code": draft.code, "chat": {"code": group.code, "title": group.name}, "title": draft.title,
        "criteria": draft.criteria, "category": "study",
        "closes_at": s.iso(draft.closes_at),
        "resolves_at": s.iso(draft.resolves_at),
        "options": ["ПИ-21", "ПИ-22"], "assisted": True, "market_id": None,
    }
    assert datetime.fromisoformat(data["closes_at"]) == draft.closes_at


def test_text_only_draft(anya):
    group = chat()
    join(group, ANYA)
    draft = make_draft(group, ANYA, assisted=False, criteria="", category="", closes_at=None, resolves_at=None)
    _, data = anya.get(f"drafts/{draft.code}")
    assert data["assisted"] is False and data["category"] is None
    assert data["closes_at"] is None and data["resolves_at"] is None and data["options"] == []


def test_someone_elses_draft_is_not_found(anya, boris):
    group = chat()
    join(group, ANYA), join(group, BORIS)
    draft = make_draft(group, ANYA)
    assert boris.get(f"drafts/{draft.code}") == (404, NOT_FOUND)
    assert boris.get("drafts/unknown") == (404, NOT_FOUND)


def test_expired_draft_is_not_found(anya):
    group = chat()
    join(group, ANYA)
    draft = make_draft(group, ANYA, expires_at=timezone.now() - timedelta(seconds=1))
    assert anya.get(f"drafts/{draft.code}") == (404, NOT_FOUND)


def test_draft_of_a_chat_one_left_is_not_found(anya):
    group = chat()
    join(group, ANYA, status="left")
    draft = make_draft(group, ANYA)
    assert anya.get(f"drafts/{draft.code}") == (404, NOT_FOUND)
    # The membership is checked in Telegram when the last answer is stale.
    join_stale = group.memberships.get()
    join_stale.checked_at = None
    join_stale.save()
    with telegram({ANYA: "left"}):
        assert anya.get(f"drafts/{draft.code}") == (404, NOT_FOUND)


def test_draft_shows_the_market_made_from_it(anya):
    group = chat()
    join(group, ANYA)
    draft = make_draft(group, ANYA)
    with telegram(count=28):
        _, created = anya.post("markets", from_draft(draft))
    assert anya.get(f"drafts/{draft.code}")[1]["market_id"] == created["market"]["id"]


# --- POST /api/markets + draft ---------------------------------------------------------------

def test_create_from_draft(anya):
    group = chat()
    join(group, ANYA)
    draft = make_draft(group, ANYA)
    key = str(uuid.uuid4())
    with telegram(count=28):
        code, data = anya.post("markets", from_draft(draft, request_key=key, assist="oneline_edited"))
    assert code == 200 and data["balance"] == "930.00"
    question = Question.objects.get()
    draft.refresh_from_db()
    assert draft.question == question and question.group == group and question.announce is True
    assert Event.objects.get(name="market_create").props == {
        "market": question.pk, "category": "study", "chat": True, "assist": "oneline_edited",
        "draft": True, "announce": True}
    # A repeat of the same request returns the same market without a second charge.
    with telegram(count=28):
        code, again = anya.post("markets", from_draft(draft, request_key=key))
    assert code == 200 and again["market"]["id"] == question.pk and again["balance"] == "930.00"
    assert LedgerEntry.objects.filter(kind=LedgerEntry.Kind.CREATION).count() == 1


def test_draft_already_used_by_the_chat_button(anya):
    group = chat()
    join(group, ANYA)
    draft = make_draft(group, ANYA)
    with telegram(count=28):
        anya.post("markets", from_draft(draft, request_key=str(uuid.uuid4())))
        code, data = anya.post("markets", from_draft(draft, request_key=str(uuid.uuid4())))
        assert (code, data) == (400, {"error": "Рынок по этому черновику уже создан."})
        assert anya.post("markets", from_draft(draft))[0] == 400
    assert Question.objects.count() == 1


def test_draft_must_be_own_live_and_of_the_same_chat(anya, boris):
    group, other = chat(), chat(telegram_chat_id=-100999)
    for g in (group, other):
        join(g, ANYA), join(g, BORIS)
    own = make_draft(group, ANYA)
    expired = make_draft(group, ANYA, expires_at=timezone.now() - timedelta(seconds=1))
    with telegram(count=28):
        assert anya.post("markets", from_draft(own, chat=other.code)) == (404, NOT_FOUND)
        assert anya.post("markets", {**from_draft(own), "chat": None}) == (404, NOT_FOUND)
        assert anya.post("markets", from_draft(expired)) == (404, NOT_FOUND)
        assert boris.post("markets", from_draft(own)) == (404, NOT_FOUND)
        assert anya.post("markets", {**from_draft(own), "draft": "nope"}) == (404, NOT_FOUND)
        assert anya.post("markets", {**from_draft(own), "draft": 5}) == (404, NOT_FOUND)
    assert not Question.objects.exists()


def test_create_error_leaves_the_draft_free(anya):
    group = chat()
    join(group, ANYA)
    draft = make_draft(group, ANYA)
    with telegram(count=28):
        code, _ = anya.post("markets", from_draft(draft, criteria=""))
    assert code == 400
    draft.refresh_from_db()
    assert draft.question is None


def test_create_from_draft_under_premoderation(anya):
    group = chat()
    join(group, ANYA)
    draft = make_draft(group, ANYA)
    with override_settings(PREMODERATION_ENABLED=True), telegram(count=28):
        code, data = anya.post("markets", from_draft(draft))
    assert code == 200 and data["market"] is None and data["publication"] == "pending"
    draft.refresh_from_db()
    assert draft.question_id == data["draft_id"]


@pytest.mark.parametrize("value", ["oneline", "oneline_edited", "accepted"])
def test_new_assist_values_are_logged(anya, value):
    anya.post("markets", create_payload(assist=value))
    assert Event.objects.get(name="market_create").props["assist"] == value


def test_announce_is_not_set_without_a_draft(anya):
    group = chat()
    join(group, ANYA)
    with telegram(count=28):
        anya.post("markets", create_payload(chat=group.code))
    question = Question.objects.get()
    assert question.announce is False and "draft" not in Event.objects.get(name="market_create").props


# --- POST /api/assist: options and mode -------------------------------------------------------

def test_options_for_a_who_question(anya):
    options = ["ПИ-21", "ПИ-22", " ПИ-23  "]
    with model(reply(answer(question="Какая группа выиграет кубок факультета 30 ноября?", options=options))):
        code, data = anya.post("assist", {"title": "Кто выиграет кубок факультета?", "mode": "oneline"})
    assert code == 200 and data["options"] == ["ПИ-21", "ПИ-22", "ПИ-23"]
    assert events() == [{"result": "ok", "mode": "oneline", "category": "study", "refined": True}]


@pytest.mark.parametrize("options", [None, [], ""])
def test_no_options_is_null(anya, options):
    with model(reply(answer(options=options))):
        _, data = anya.post("assist", {"title": "Сдам ли я сессию без троек?"})
    assert data["options"] is None


@pytest.mark.parametrize("options", [
    ["Одна"], [f"Вариант {i}" for i in range(11)], ["Да", "да"], ["Короткий", "x" * 81],
    "ПИ-21, ПИ-22", [1, 2], ["ПИ-21", None], {"a": "b"},
])
def test_bad_options_are_a_refusal(anya, options):
    with model(reply(answer(options=options))):
        code, _ = anya.post("assist", {"title": "Кто выиграет кубок факультета?"})
    assert code == 503 and events() == [{"result": "error", "reason": "schema"}]


def test_unknown_mode_is_not_logged(anya):
    with model(reply(answer())):
        anya.post("assist", {"title": "Сдам ли я сессию без троек?", "mode": "loud"})
    assert "mode" not in events()[0]


def test_prompt_asks_for_options_and_questions_from_messages():
    assert '"options": null' in assist.SYSTEM_PROMPT and "кто из" in assist.SYSTEM_PROMPT
    assert "сообщение из чата" in assist.SYSTEM_PROMPT


def test_check_needs_a_worded_question_for_a_long_message():
    now = timezone.now()
    long_text = "спорим, что " + "завтра снег и пары отменят, " * 6
    with pytest.raises(assist.Failed) as failed:
        assist.check(answer(question=None), long_text, None, now)
    assert failed.value.reason == "schema"
    checked = assist.check(answer(question="Отменят ли пары из-за снега 6 ноября?"), long_text, None, now)
    assert checked["question"] == "Отменят ли пары из-за снега 6 ноября?" and checked["options"] is None


# --- POST /api/events: oneline_wizard ---------------------------------------------------------

@pytest.mark.parametrize("reason", ["manual", "unavailable"])
def test_oneline_wizard_event(anya, reason):
    assert anya.post("events", {"name": "oneline_wizard", "props": {"reason": reason, "title": "секрет"}}) == \
        (200, {"ok": True})
    assert Event.objects.get(name="oneline_wizard").props == {"reason": reason}


@pytest.mark.parametrize("props", [{}, {"reason": "bored"}, None])
def test_oneline_wizard_needs_a_known_reason(anya, props):
    code, _ = anya.post("events", {"name": "oneline_wizard", "props": props})
    assert code == 400 and not Event.objects.filter(name="oneline_wizard").exists()
