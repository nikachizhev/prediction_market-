"""AI rules helper: POST /api/assist and the analytics fields of POST /api/markets (spec E9.1, docs/api.md «Фаза 3a»).

The model is never called: `requests.post` in market.assist is mocked.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest
import requests
from django.db import close_old_connections, connection
from django.test import override_settings
from django.utils import timezone

from market import assist
from market.models import Event, Question, Trader
from market.tests.test_api import anya, boris, bot_send, bot_token, create_payload, trader  # noqa: F401 (fixtures)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("bot_token")]

MSK = ZoneInfo("Europe/Moscow")
TITLE = "Сдам ли я сессию без троек?"


@pytest.fixture(autouse=True)
def helper_on():
    with override_settings(LLM_API_KEY="test-key", ASSIST_ENABLED=True):
        yield


def day(offset: int, at: str = "23:59") -> str:
    """A Moscow moment `offset` days from today, as the model writes it."""
    return f"{(timezone.now().astimezone(MSK).date() + timedelta(days=offset)).isoformat()}T{at}"


def answer(**kw) -> dict:
    data = {"forbidden": None, "question": None, "why": None,
            "criteria": "ДА — если в зачётке нет троек.\nНЕТ — если есть хотя бы одна тройка.\n"
                        "Чем подтвердить: скриншот зачётки.",
            "category": "study", "closes_at": day(10), "resolves_at": day(12)}
    data.update(kw)
    return data


def reply(content, status=200) -> Mock:
    response = Mock(status_code=status)
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    response.json.return_value = {"choices": [{"message": {"role": "assistant", "content": text}}]}
    return response


def model(*responses):
    """Patch the HTTP call to the model; each item is a response or an exception to raise."""
    return patch("market.assist.requests.post", side_effect=list(responses))


def events():
    return list(Event.objects.filter(name="assist_request").order_by("id").values_list("props", flat=True))


def test_success_fills_the_contract(anya):
    with model(reply(answer())) as post:
        code, data = anya.post("assist", {"title": TITLE, "category": "study"})
    assert code == 200 and post.call_count == 1
    assert set(data) == {"question", "why", "criteria", "category", "closes_at", "resolves_at", "left"}
    assert data["question"] is None and data["why"] is None
    assert data["criteria"].startswith("ДА — если") and data["category"] == "study"
    closes = datetime.fromisoformat(data["closes_at"])
    assert closes.utcoffset() == timedelta(hours=3) and closes.strftime("%Y-%m-%dT%H:%M") == day(10)
    assert datetime.fromisoformat(data["resolves_at"]).strftime("%Y-%m-%dT%H:%M") == day(12)
    assert data["left"] == 19 and anya.get("me")[1]["assist"]["left"] == 19
    assert events() == [{"result": "ok", "category": "study", "refined": False}]


def test_answer_dates_pass_create_market(anya):
    """What the helper suggests is accepted by POST /api/markets as is."""
    with model(reply(answer())):
        _, data = anya.post("assist", {"title": TITLE})
    code, created = anya.post("markets", {"title": TITLE, "criteria": data["criteria"], "category": data["category"],
                                          "closes_at": data["closes_at"], "resolves_at": data["resolves_at"]})
    assert code == 200 and created["market"]["closes_at"]


def test_refined_question(anya):
    refined = "Закрою ли я зимнюю сессию без троек до 31 января?"
    why = "Добавили, какая сессия и к какому сроку: иначе спорно, что считать итогом."
    with model(reply(answer(question=refined, why=why))):
        code, data = anya.post("assist", {"title": TITLE})
    assert code == 200 and data["question"] == refined and data["why"] == why
    assert events() == [{"result": "ok", "category": "study", "refined": True}]


@pytest.mark.parametrize("same", [TITLE, f"  {TITLE.upper()} ", TITLE.rstrip("?")])
def test_same_question_is_null(anya, same):
    with model(reply(answer(question=same, why="Ничего не меняли."))):
        _, data = anya.post("assist", {"title": TITLE})
    assert data["question"] is None and data["why"] is None
    assert events()[0]["refined"] is False


def test_long_why_is_cut(anya):
    with model(reply(answer(question="Закрою ли я сессию без троек до 31 января?", why="Очень " * 100))):
        _, data = anya.post("assist", {"title": TITLE})
    assert len(data["why"]) <= assist.WHY_MAX and data["why"].endswith("…")


def test_fenced_json_and_label_category_accepted(anya):
    content = "```json\n" + json.dumps(answer(category="Учёба"), ensure_ascii=False) + "\n```"
    with model(reply(content)):
        code, data = anya.post("assist", {"title": TITLE})
    assert code == 200 and data["category"] == "study"


def test_unknown_category_falls_back_to_the_requested_one(anya):
    with model(reply(answer(category="weather"))):
        code, data = anya.post("assist", {"title": TITLE, "category": "fun"})
    assert code == 200 and data["category"] == "fun"


def test_offset_dates_are_kept(anya):
    closes = (timezone.now() + timedelta(days=3)).astimezone(ZoneInfo("UTC")).replace(second=0, microsecond=0)
    with model(reply(answer(closes_at=closes.isoformat()))):
        code, data = anya.post("assist", {"title": TITLE})
    assert code == 200 and datetime.fromisoformat(data["closes_at"]) == closes


@pytest.mark.parametrize("content,reason", [
    ("Конечно! Вот правила: ДА — если сдам.", "schema"),
    ("[1, 2, 3]", "schema"),
    (json.dumps({"criteria": "ДА — если сдам все экзамены."}), "schema"),
    (answer(criteria=5), "schema"),
    (answer(criteria="ДА — если да."), "schema"),
    (answer(question=12345), "schema"),
    (answer(question="Сдам?"), "schema"),
    (answer(question="Сдам ли я " + "очень " * 30 + "сессию?"), "schema"),
    (answer(category="weather"), "schema"),
    (answer(category=None), "schema"),
    (answer(forbidden="drugs"), "schema"),
    (answer(forbidden=["health"]), "schema"),
    (answer(closes_at="завтра"), "schema"),
    (answer(closes_at=day(10)[:10]), "schema"),
    (answer(resolves_at=None), "schema"),
    (answer(closes_at="2026-13-45T23:59"), "schema"),
    (answer(closes_at=day(-1), resolves_at=day(1)), "dates"),
    (answer(closes_at=day(0, at="00:00"), resolves_at=day(1)), "dates"),
    (answer(closes_at=day(400), resolves_at=day(401)), "dates"),
    (answer(closes_at=day(10), resolves_at=day(9)), "dates"),
    (answer(closes_at=day(10), resolves_at=day(400)), "dates"),
])
def test_garbage_from_the_model_is_a_refusal(anya, content, reason):
    with model(reply(content)):
        code, data = anya.post("assist", {"title": TITLE})
    assert code == 503 and data["error"] == "Помощник сейчас не отвечает — заполни правила сам."
    assert events() == [{"result": "error", "reason": reason}]
    assert anya.get("me")[1]["assist"]["left"] == 19


def test_broken_envelope_is_a_refusal(anya):
    response = Mock(status_code=200)
    response.json.return_value = {"error": {"message": "overloaded"}}
    with model(response):
        code, _ = anya.post("assist", {"title": TITLE})
    assert code == 503 and events() == [{"result": "error", "reason": "schema"}]


def test_non_json_body_is_a_refusal(anya):
    response = Mock(status_code=200)
    response.json.side_effect = requests.JSONDecodeError("Expecting value", "<html>", 0)
    with model(response):
        code, _ = anya.post("assist", {"title": TITLE})
    assert code == 503 and events() == [{"result": "error", "reason": "schema"}]


@pytest.mark.parametrize("failure,reason", [
    (requests.Timeout("read timed out"), "timeout"),
    (requests.ConnectTimeout("connect timed out"), "timeout"),
    (requests.ConnectionError("refused"), "http"),
    (reply(answer(), status=500), "http"),
    (reply(answer(), status=429), "http"),
    (reply(answer(), status=401), "http"),
])
def test_model_unreachable_is_a_refusal(anya, failure, reason):
    with model(failure):
        code, data = anya.post("assist", {"title": TITLE})
    assert code == 503 and "заполни правила сам" in data["error"]
    assert events() == [{"result": "error", "reason": reason}]


def test_unexpected_crash_does_not_leave_a_pending_request(anya):
    with model(reply(answer())), patch("market.assist.check", side_effect=RuntimeError("bug")):
        with pytest.raises(RuntimeError):
            anya.post("assist", {"title": TITLE})
    assert events() == [{"result": "error", "reason": "internal"}]


@pytest.mark.parametrize("topic,text", [
    ("health", "Похоже, это рынок о здоровье конкретного человека"),
    ("violence", "Похоже, это рынок о насилии"),
    ("politics", "Похоже, это рынок о политике"),
    ("private", "Похоже, это рынок о личной жизни другого человека"),
])
def test_forbidden_topic_is_a_warning(anya, topic, text):
    content = {"forbidden": topic, "question": None, "why": None, "criteria": None, "category": None,
               "closes_at": None, "resolves_at": None}
    with model(reply(content)):
        code, data = anya.post("assist", {"title": "Заболеет ли Петя до сессии?", "category": "study"})
    assert code == 200 and data == {"warning": "forbidden", "text": text, "left": 19}
    assert events() == [{"result": "warning", "category": "study", "topic": topic}]


def test_request_is_reserved_before_the_model_answers(anya):
    seen = []

    def fake_post(*args, **kwargs):
        seen.append(events())
        return reply(answer())

    with patch("market.assist.requests.post", side_effect=fake_post):
        anya.post("assist", {"title": TITLE})
    assert seen == [[{"result": "pending"}]]


def test_daily_limit(anya, boris):
    with model(*[reply(answer())] * 19, requests.Timeout()) as post:
        for _ in range(20):
            anya.post("assist", {"title": TITLE})
        assert post.call_count == 20
        code, data = anya.post("assist", {"title": TITLE})
        assert post.call_count == 20
    assert code == 429 and data["error"] == "Подсказки на сегодня закончились — их 20 в день."
    assert events()[-1] == {"result": "limit"}
    assert anya.get("me")[1]["assist"]["left"] == 0
    assert anya.post("assist", {"title": TITLE})[0] == 429
    # The limit is per person.
    with model(reply(answer())):
        assert boris.post("assist", {"title": TITLE})[0] == 200


@override_settings(ASSIST_DAILY_LIMIT=1)
def test_limit_message_uses_the_setting(anya):
    with model(reply(answer())):
        anya.post("assist", {"title": TITLE})
    code, data = anya.post("assist", {"title": TITLE})
    assert code == 429 and data["error"] == "Подсказки на сегодня закончились — их 1 в день."


@pytest.mark.parametrize("switch", [dict(ASSIST_ENABLED=False), dict(LLM_API_KEY="")])
def test_switched_off_or_no_key(anya, switch):
    with override_settings(**switch), model(reply(answer())) as post:
        code, data = anya.post("assist", {"title": TITLE})
    assert code == 503 and data["error"]
    assert post.call_count == 0 and events() == []


@override_settings(LLM_BASE_URL="https://llm.example/v1", LLM_MODEL="test/model", ASSIST_TIMEOUT=7)
def test_only_question_category_and_date_reach_the_model(anya):
    with model(reply(answer())) as post:
        anya.post("assist", {"title": f"  {TITLE}  ", "category": "study"})
    (url,), kwargs = post.call_args
    payload = kwargs["json"]
    assert url == "https://llm.example/v1/chat/completions"
    assert kwargs["headers"] == {"Authorization": "Bearer test-key"} and kwargs["timeout"] == 7
    assert payload["model"] == "test/model" and payload["reasoning"] == {"effort": "none"}
    assert payload["response_format"] == {"type": "json_object"} and payload["max_tokens"] <= 1000
    system, user = payload["messages"]
    assert system == {"role": "system", "content": assist.SYSTEM_PROMPT} and user["role"] == "user"
    sent = json.loads(user["content"])
    assert set(sent) == {"question", "category", "today"}
    assert sent["question"] == TITLE and sent["category"] == "study"
    assert sent["today"].startswith(timezone.now().astimezone(MSK).date().isoformat())
    t = trader(1001)
    whole = json.dumps(payload, ensure_ascii=False)
    for private in (str(t.telegram_chat_id), t.name, t.pseudonym, "anya", str(t.balance)):
        assert private not in whole


def test_unknown_category_is_sent_as_null(anya):
    with model(reply(answer())) as post:
        anya.post("assist", {"title": TITLE, "category": "weather"})
    assert json.loads(post.call_args.kwargs["json"]["messages"][1]["content"])["category"] is None


def test_events_never_hold_the_question(anya):
    with model(reply(answer(question="Закрою ли я сессию без троек до 31 января?", why="Добавили срок."))):
        anya.post("assist", {"title": TITLE})
    stored = json.dumps(events(), ensure_ascii=False)
    assert "сесси" not in stored and set(events()[0]) <= {"result", "category", "topic", "reason", "refined"}


@pytest.mark.parametrize("title", ["Сдам ли?", " " * 20 + "Коротко" + " " * 20, "Сдам ли я " + "x" * 120, 12345, None])
def test_title_length_is_checked(anya, title):
    with model(reply(answer())) as post:
        code, data = anya.post("assist", {"title": title})
    assert code == 400 and data["error"] == "Вопрос — от 10 до 120 символов."
    assert post.call_count == 0 and events() == []


def test_get_is_not_allowed(anya):
    assert anya.get("assist")[0] == 405


@pytest.mark.django_db(transaction=True)
@override_settings(ASSIST_DAILY_LIMIT=1)
def test_parallel_requests_cannot_exceed_the_limit():
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row locks")
    from market.api.assist_views import reserve
    from market.api.views import LimitReached
    t = Trader.objects.create(name="Parallel", pseudonym="Parallel")
    barrier = Barrier(2)

    def ask(_):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
            barrier.wait(timeout=10)
            try:
                return reserve(t, None).props["result"]
            except LimitReached:
                return "refused"
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(ask, range(2)))
    assert results == ["pending", "refused"]
    assert sorted(events(), key=lambda p: p["result"]) == [{"result": "limit"}, {"result": "pending"}]


# --- POST /api/markets: assist and template go to market_create -----------------------

def created_props():
    return Event.objects.get(name="market_create").props


@pytest.mark.parametrize("extra,expected", [
    ({"assist": "accepted", "template": "exam"}, {"assist": "accepted", "template": "exam"}),
    ({"assist": "edited"}, {"assist": "edited"}),
    ({"assist": "undone", "template": "rank_2-b"}, {"assist": "undone", "template": "rank_2-b"}),
    ({}, {}),
])
def test_create_market_logs_assist_and_template(anya, extra, expected):
    code, data = anya.post("markets", create_payload(**extra))
    assert code == 200
    props = created_props()
    assert props == {"market": data["market"]["id"], "category": "study", **expected}


@pytest.mark.parametrize("extra", [
    {"assist": "yes"}, {"assist": ["accepted"]}, {"assist": {"x": 1}}, {"assist": 1},
    {"template": "Exam"}, {"template": "a" * 41}, {"template": ""}, {"template": "exam\n"},
    {"template": "../etc"}, {"template": ["exam"]}, {"template": 5},
])
def test_create_market_drops_unknown_assist_and_template(anya, extra):
    code, data = anya.post("markets", create_payload(**extra))
    assert code == 200 and Question.objects.filter(pk=data["market"]["id"]).exists()
    assert set(created_props()) == {"market", "category"}
