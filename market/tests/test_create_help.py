"""Market templates and similar markets for the creation wizard (spec E9.2, E9.3; docs/api.md «Фаза 3a»)."""
import re
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from market import services
from market.market_templates import market_templates, template_ids
from market.models import CATEGORIES, Group, GroupMembership, Question
from market.similar import similar_markets, similarity, stems
from market.tests.test_api import anya, boris, bot_send, bot_token, market_by, trader  # noqa: F401 (fixtures)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("bot_token")]

PLACEHOLDER = re.compile(r"\{(\w+)\}")
TARGET = "Закроет ли больше половины потока сессию без троек?"
QUERY = "Сдам ли я сессию без троек?"


def open_market(title, **kw):
    kw.setdefault("deadline", timezone.now() + timedelta(days=2))
    kw.setdefault("category", "study")
    return services.create_question(title=title, resolution_criteria="Ведомость", **kw)


def similar_ids(api, q):
    status, data = api.get("markets/similar", q=q)
    assert status == 200
    return [item["id"] for item in data["items"]]


# --- templates -------------------------------------------------------------------------

def test_templates_two_per_category_in_category_order(anya):
    status, data = anya.get("templates")
    assert status == 200
    assert [t["category"] for t in data] == [slug for slug, _ in CATEGORIES for _ in range(2)]
    assert [t["id"] for t in data] == ["release", "rank", "exam", "classes", "show", "update",
                                      "win", "goal", "views", "finish"]
    assert template_ids() == {t["id"] for t in data}


def test_template_fields_follow_contract(anya):
    for t in anya.get("templates")[1]:
        assert set(t) == {"id", "category", "text", "criteria", "blanks"}
        assert all(isinstance(t[k], str) and t[k] for k in ("id", "category", "text", "criteria"))
        for b in t["blanks"]:
            assert set(b) == {"key", "label", "type", "placeholder", "hint"}
            assert b["type"] in ("text", "date", "number")
            assert all(isinstance(v, str) for v in b.values()) and b["label"] and b["hint"].startswith("[")


def test_every_placeholder_is_described_and_every_template_has_a_date():
    for t in market_templates():
        keys = [b["key"] for b in t["blanks"]]
        assert len(keys) == len(set(keys)), t["id"]
        assert set(PLACEHOLDER.findall(t["text"] + t["criteria"])) == set(keys), t["id"]
        assert set(PLACEHOLDER.findall(t["text"])) == set(keys), t["id"]
        assert next(b for b in t["blanks"] if b["key"] == "date")["type"] == "date", t["id"]


def test_template_texts_come_from_the_mockup():
    by_id = {t["id"]: t for t in market_templates()}
    exam = by_id["exam"]
    assert exam["category"] == "study" and exam["text"] == "Сдам ли {subject} на 4 или 5 до {date}?"
    assert exam["criteria"].startswith("ДА — если до {date} в зачётке по предмету «{subject}» стоит 4 или 5.\n")
    assert exam["blanks"][0] == {"key": "subject", "label": "Предмет", "type": "text",
                                 "placeholder": "матанализ", "hint": "[предмет]"}
    assert [b["key"] for b in by_id["rank"]["blanks"]] == ["rank", "game", "date"]
    assert [b["key"] for b in by_id["views"]["blanks"]] == ["video", "num", "date"]
    assert by_id["views"]["blanks"][1]["type"] == "number"


def test_catalog_cannot_be_changed_by_a_caller():
    first = market_templates()
    first[0]["text"] = "испорчено"
    first[0]["blanks"].clear()
    again = market_templates()[0]
    assert again["text"] != "испорчено" and again["blanks"]


# --- word matching ---------------------------------------------------------------------

def test_stems_normalise_case_yo_punctuation_and_endings():
    assert stems("СЕССИЮ, сессия!") == {"сесси"}
    assert stems("Наберёт «ёлка»") == stems("наберет елка")
    assert stems("Сдам ли я сессию без троек до 20 декабря?") == {"сдам", "сесси", "троек"}


def test_only_stop_words_give_no_stems():
    assert stems("Будет ли это уже так, или всё же нет?") == frozenset()


def test_one_shared_word_is_not_enough_for_long_questions():
    a = stems("Выиграет ли сборная вуза турнир по баскетболу этой весной?")
    b = stems("Выиграет ли Спартак чемпионат России в этом сезоне?")
    assert len(a & b) == 1 and similarity(a, b) < 0.35


# --- similar markets -------------------------------------------------------------------

def test_finds_a_similar_market(anya, boris):
    target = market_by(1002, title=TARGET, category="study")
    market_by(1002, title="Выйдет ли новая часть GTA до конца года?", category="games")
    status, data = anya.get("markets/similar", q=QUERY)
    assert status == 200 and [m["id"] for m in data["items"]] == [target.pk]
    card = data["items"][0]
    assert card["title"] == TARGET and card["status"] == "open" and len(card["spark"]) == 7
    assert card["starred"] is False and card["my_side"] is None


def test_cards_show_own_star_and_side(anya, boris):
    m = market_by(1002, title=TARGET, category="study")
    anya.post(f"markets/{m.pk}/buy", {"side": "yes", "amount": 20, "request_key": str(uuid.uuid4())})
    anya.post(f"markets/{m.pk}/star", {"starred": True})
    card = anya.get("markets/similar", q=QUERY)[1]["items"][0]
    assert card["starred"] is True and card["my_side"] == "yes"
    card = boris.get("markets/similar", q=QUERY)[1]["items"][0]
    assert card["starred"] is False and card["my_side"] is None


def test_sorted_by_similarity_then_newest_and_at_most_three(anya):
    now = timezone.now()
    weaker_old = open_market("Сдаст ли весь поток сессию без троек и долгов к зиме?")
    weaker_new = open_market("Сдаст ли наша группа сессию без троек и пересдач в срок?")
    exact = open_market("Сдам ли я сессию без троек?")
    closer = open_market(TARGET)
    weakest = open_market("Закроет ли кто-нибудь из группы сессию зимой без троек, долгов и пересдач?")
    for i, q in enumerate([weaker_old, exact, closer, weaker_new, weakest]):
        Question.objects.filter(pk=q.pk).update(created_at=now - timedelta(hours=10 - i))
    q = stems(QUERY)
    assert similarity(q, stems(weaker_old.title)) == similarity(q, stems(weaker_new.title))
    assert similarity(q, stems(closer.title)) > similarity(q, stems(weaker_new.title)) > similarity(q, stems(weakest.title))
    assert similar_ids(anya, QUERY) == [exact.pk, closer.pk, weaker_new.pk]
    assert [m.pk for m in similar_markets(trader(1001), QUERY, limit=5)] == [
        exact.pk, closer.pk, weaker_new.pk, weaker_old.pk, weakest.pk]


def test_unrelated_markets_are_not_listed(anya):
    open_market("Выиграет ли Спартак чемпионат России в этом сезоне?", category="sport")
    open_market("Сдаст ли Петя курсовую по программированию вовремя?")
    assert similar_ids(anya, "Выиграет ли сборная вуза турнир по баскетболу этой весной?") == []


def test_private_closed_finished_and_hidden_markets_are_never_listed(anya, boris):
    now = timezone.now()
    group = Group.objects.create(name="Закрытая", owner=trader(1002))
    GroupMembership.objects.create(group=group, trader=trader(1002), status="active")
    private = open_market(TARGET, group_id=group.pk)
    open_market(TARGET, deadline=now - timedelta(hours=1))
    services.close_betting(open_market(TARGET))
    services.resolve(open_market(TARGET), "yes")
    services.annul(open_market(TARGET))
    hidden = open_market(TARGET)
    Question.objects.filter(pk=hidden.pk).update(hidden_at=now)
    open_market(TARGET, kind=Question.Kind.POLL)
    assert similar_ids(anya, QUERY) == []
    # The group member sees the private market: it is filtered by visibility, not dropped for everyone.
    assert similar_ids(boris, QUERY) == [private.pk]


def test_market_without_deadline_is_still_open(anya):
    m = open_market(TARGET, deadline=None)
    assert similar_ids(anya, QUERY) == [m.pk]


@pytest.mark.parametrize("q", ["", "сессия", "   сессию     ", "без троек"])
def test_short_query_gives_nothing(anya, q):
    open_market("сессия")
    open_market(TARGET)
    assert similar_ids(anya, q) == []


def test_query_of_stop_words_gives_nothing(anya):
    open_market("Будет ли это уже так, или всё же нет?")
    assert similar_ids(anya, "Будет ли это уже так, или всё же нет?") == []


def test_missing_query_gives_nothing(anya):
    open_market(TARGET)
    status, data = anya.get("markets/similar")
    assert status == 200 and data == {"items": []}


@pytest.mark.parametrize("path", ["templates", "markets/similar"])
def test_endpoints_need_auth(client, path):
    assert client.get(f"/api/{path}", {"q": QUERY}).status_code == 401
    assert client.get(f"/api/{path}", HTTP_AUTHORIZATION="tma user=%7B%22id%22%3A1001%7D").status_code == 401


@pytest.mark.parametrize("path", ["templates", "markets/similar"])
def test_endpoints_are_read_only(anya, path):
    assert anya.post(path, {"q": QUERY})[0] == 405
