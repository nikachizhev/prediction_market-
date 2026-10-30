"""Closed chats in the Mini App API (spec E8.2, E8.3; docs/api.md «Фаза 3b»). Telegram is always mocked."""
import uuid
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone

from market import chats, services
from market.models import Event, Group, GroupMembership, Question, Trader
from market.tests.test_api import (  # noqa: F401 (fixtures)
    Api, anya, boris, bot_send, bot_token, create_payload, market_by, trader,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("bot_token")]

D = Decimal
ANYA, BORIS, VERA = 1001, 1002, 1003


@pytest.fixture
def vera(client):
    api = Api(client, VERA, first_name="Вера")
    api.post("session")
    return api


@pytest.fixture(autouse=True)
def helper_on():
    # MARKETS_PER_DAY: fixtures create many markets by one author; the limit itself is tested elsewhere.
    with override_settings(LLM_API_KEY="test-key", ASSIST_ENABLED=True, TELEGRAM_APP_NAME="app",
                           MARKETS_PER_DAY=100):
        yield


def rich(user_id, balance=10000):
    Trader.objects.filter(telegram_chat_id=user_id).update(balance=D(balance))


def chat(members=28, **kw):
    fields = dict(name="Группа ПИ-24", kind="chat", telegram_chat_id=-100123, code=chats.new_code(),
                  bot_active=True, member_count=members)
    fields.update(kw)
    return Group.objects.create(**fields)


def join(group, user_id, checked=True, status="active"):
    return GroupMembership.objects.create(group=group, trader=trader(user_id), status=status,
                                          checked_at=timezone.now() if checked else None)


@contextmanager
def telegram(statuses=None, count=None):
    """Fake Bot API: getChatMember answers by user ID (default «left»), getChatMemberCount by `count`."""
    statuses = statuses or {}

    def api(method, **kw):
        if method == "getChatMember":
            return {"status": statuses.get(kw["user_id"], "left")}
        if method == "getChatMemberCount":
            return count
        raise AssertionError(f"unexpected Telegram call {method}")

    with patch("market.chats.telegram.api", side_effect=api) as mocked:
        yield mocked


def calls(mocked, method):
    return [c for c in mocked.call_args_list if c.args and c.args[0] == method]


def chat_market(group, creator_id, **kw):
    return market_by(creator_id, group=group, **kw)


def ids(items):
    return [item["id"] for item in items]


# --- GET /api/chats/{code} -------------------------------------------------------------

def test_chat_space_for_member(anya):
    group = chat(members=28)
    join(group, ANYA)
    with telegram() as api:
        code, data = anya.get(f"chats/{group.code}")
    assert code == 200 and api.call_count == 0
    assert data == {
        "code": group.code, "title": "Группа ПИ-24", "members": 28, "frozen": False, "markets": 0,
        "volume": "0.00", "my_rank": None, "creation_cost": "70.00", "b": 100, "assist": True,
        "link": f"https://t.me/predbot/app?startapp=chat_{group.code}",
    }


@pytest.mark.parametrize("members, own, cost, b", [(12, None, "40.00", 57), (50, None, "70.00", 100),
                                                   (51, None, "100.00", 144), (12, 150, "150.00", 216),
                                                   (300, 30, "30.00", 43)])
def test_chat_space_price_by_size_or_own(anya, members, own, cost, b):
    group = chat(members=members, creation_cost=own)
    join(group, ANYA)
    data = anya.get(f"chats/{group.code}")[1]
    assert data["creation_cost"] == cost and data["b"] == b


def test_chat_space_frozen_and_assist(anya):
    group = chat(bot_active=False, assist_enabled=False)
    join(group, ANYA, checked=False)
    with telegram() as api:
        code, data = anya.get(f"chats/{group.code}")
    # A frozen chat keeps its last known members and is not asked.
    assert code == 200 and api.call_count == 0
    assert data["frozen"] is True and data["assist"] is False


def test_chat_space_assist_off_globally(anya):
    group = chat()
    join(group, ANYA)
    with override_settings(ASSIST_ENABLED=False):
        assert anya.get(f"chats/{group.code}")[1]["assist"] is False


def test_chat_space_counts_open_markets_volume_and_rank(anya, boris, vera):
    group = chat()
    for user in (ANYA, BORIS, VERA):
        join(group, user)
    first, second = chat_market(group, BORIS), chat_market(group, BORIS, title="Отменят ли пары в пятницу?")
    hidden = chat_market(group, BORIS, title="Скрытый жалобами рынок?")
    Question.objects.filter(pk=hidden.pk).update(hidden_at=timezone.now())
    market_by(BORIS, title="Публичный рынок?")
    services.buy(trader(ANYA), first, "yes", D(50))
    services.buy(trader(VERA), first, "no", D(30))
    services.buy(trader(ANYA), second, "yes", D(20))
    data = anya.get(f"chats/{group.code}")[1]
    assert data["markets"] == 2 and D(data["volume"]) == D(100)
    assert data["my_rank"] is None
    services.resolve(first, "yes")
    me = anya.get(f"chats/{group.code}")[1]
    assert me["markets"] == 1 and me["my_rank"] == 1
    assert vera.get(f"chats/{group.code}")[1]["my_rank"] == 3


def test_chat_rank_matches_the_chat_leaderboard(anya, boris, vera, django_assert_max_num_queries):
    from market.rating import chat_rank, leaderboard
    group = chat()
    extra = [Trader.objects.create(name=f"Участник {i}", pseudonym=f"Участник {i}", balance=D(10000),
                                   telegram_chat_id=2000 + i) for i in range(5)]
    for user in (ANYA, BORIS, VERA, *(t.telegram_chat_id for t in extra)):
        join(group, user)
    rich(ANYA), rich(VERA)
    left = extra[4]
    won, lost, open_ = chat_market(group, BORIS), chat_market(group, BORIS, title="Отменят ли пары?"), \
        chat_market(group, BORIS, title="Будет ли зачёт автоматом?")
    # Two equal markets, one buyer each: the same profit, the tie goes to the earlier account.
    twin_a, twin_b = chat_market(group, BORIS, title="Пойдёт ли дождь?"), \
        chat_market(group, BORIS, title="Пойдёт ли снег?")
    public = market_by(BORIS, title="Публичный рынок?")
    services.buy(trader(ANYA), won, "yes", D(100))
    services.buy(trader(VERA), won, "no", D(60))
    services.buy(trader(ANYA), lost, "no", D(40))
    services.buy(extra[0], twin_a, "yes", D(50))
    services.buy(extra[1], twin_b, "yes", D(50))
    services.buy(extra[2], open_, "yes", D(80))
    services.buy(left, won, "yes", D(100))
    GroupMembership.objects.filter(group=group, trader=left).update(status="left")
    services.buy(extra[3], public, "yes", D(100))
    for market, outcome in ((won, "yes"), (lost, "yes"), (twin_a, "yes"), (twin_b, "yes"), (public, "yes")):
        services.resolve(market, outcome)

    def expected(t):
        mine = leaderboard(t, group=group)["me"]
        ranked = mine is not None and (mine["predictions"] > 0 or D(mine["profit"]) != 0)
        return mine["rank"] if ranked else None

    people = [trader(ANYA), trader(BORIS), trader(VERA), *extra]
    ranks = {t.pk: expected(t) for t in people}
    assert ranks[extra[0].pk] + 1 == ranks[extra[1].pk]
    assert ranks[extra[2].pk] is None and ranks[extra[3].pk] is None and ranks[left.pk] is None
    # Members without a result still hold their zero-profit places, so the losers come after them.
    assert sorted(r for r in ranks.values() if r) == [1, 2, 3, 6, 7]
    for t in people:
        with django_assert_max_num_queries(3):
            assert chat_rank(t, group) == ranks[t.pk], t.display_name
    assert anya.get("chats")[1]["items"][0]["my_rank"] == ranks[trader(ANYA).pk]


def test_unknown_code_is_404(anya):
    with telegram() as api:
        code, data = anya.get("chats/nosuchcode")
    assert code == 404 and data == {"error": "Чат не найден."} and api.call_count == 0


def test_stranger_gets_404_like_unknown_code(anya):
    group = chat()
    with telegram({ANYA: "left"}):
        code, data = anya.get(f"chats/{group.code}")
    assert code == 404 and data == {"error": "Чат не найден."}
    assert not GroupMembership.objects.filter(group=group).exists()
    for path in ("markets", "rating"):
        with telegram():
            assert anya.get(f"chats/{group.code}/{path}")[0] == 404


def test_first_visit_by_link_creates_membership_and_is_cached(anya):
    group = chat()
    with telegram({ANYA: "member"}) as api:
        assert anya.get(f"chats/{group.code}")[0] == 200
        assert anya.get(f"chats/{group.code}/markets")[0] == 200
        assert anya.get(f"chats/{group.code}/rating")[0] == 200
    assert len(calls(api, "getChatMember")) == 1
    assert GroupMembership.objects.get(group=group, trader=trader(ANYA)).status == "active"


def test_left_member_gets_404(anya):
    group = chat()
    join(group, ANYA, status="left")
    with telegram() as api:
        assert anya.get(f"chats/{group.code}")[0] == 404
    # `chat_member` already said they left; a fresh answer is trusted for ten minutes.
    assert api.call_count == 0


def test_stale_check_asks_telegram_again(anya):
    group = chat()
    membership = join(group, ANYA)
    GroupMembership.objects.filter(pk=membership.pk).update(checked_at=timezone.now() - timedelta(minutes=11))
    with telegram({ANYA: "kicked"}) as api:
        assert anya.get(f"chats/{group.code}")[0] == 404
    assert len(calls(api, "getChatMember")) == 1
    assert GroupMembership.objects.get(pk=membership.pk).status == "left"


def test_old_site_group_is_not_a_chat(anya):
    group = Group.objects.create(name="Старая группа", kind="social", code="oldgroup1")
    join(group, ANYA)
    assert anya.get("chats/oldgroup1")[0] == 404


# --- GET /api/chats --------------------------------------------------------------------

def test_chat_list_without_telegram_calls(anya, boris):
    quiet = chat(name="Беседа без рынков", telegram_chat_id=-1001)
    busy = chat(name="Яблоко раздора", telegram_chat_id=-1002)
    gone = chat(name="Ушёл отсюда", telegram_chat_id=-1003)
    foreign = chat(name="Чужой чат", telegram_chat_id=-1004)
    join(quiet, ANYA, checked=False)
    join(busy, ANYA, checked=False)
    join(busy, BORIS)
    join(gone, ANYA, status="left")
    join(foreign, BORIS)
    chat_market(busy, BORIS)
    with telegram() as api:
        code, data = anya.get("chats")
    assert code == 200 and api.call_count == 0
    assert [item["title"] for item in data["items"]] == ["Яблоко раздора", "Беседа без рынков"]
    assert data["items"][0]["markets"] == 1 and data["items"][0]["code"] == busy.code


# --- GET /api/chats/{code}/markets -----------------------------------------------------

def test_chat_markets_order_pages_and_cards(anya, boris):
    group = chat()
    join(group, ANYA)
    join(group, BORIS)
    rich(BORIS)
    now = timezone.now()
    old_done = chat_market(group, BORIS, title="Давно решённый рынок?")
    done = chat_market(group, BORIS, title="Недавно решённый рынок?")
    services.resolve(old_done, "no")
    Question.objects.filter(pk=old_done.pk).update(resolved_at=now - timedelta(days=2))
    services.resolve(done, "yes")
    hidden = chat_market(group, BORIS, title="Скрытый жалобами рынок?")
    Question.objects.filter(pk=hidden.pk).update(hidden_at=now)
    opened = [chat_market(group, BORIS, title=f"Открытый рынок номер {i}?") for i in range(21)]
    for i, q in enumerate(opened):
        Question.objects.filter(pk=q.pk).update(created_at=now - timedelta(hours=30 - i))
    market_by(BORIS, title="Публичный рынок?")
    services.buy(trader(ANYA), opened[-1], "no", D(10))
    anya.post(f"markets/{opened[-1].pk}/star", {"starred": True})
    with telegram() as api:
        code, first = anya.get(f"chats/{group.code}/markets")
        _, second = anya.get(f"chats/{group.code}/markets", page=2)
    assert code == 200 and api.call_count == 0
    assert first["total"] == 23 and first["next_page"] == 2 and second["next_page"] is None
    assert ids(first["items"]) == [q.pk for q in reversed(opened[1:])]
    assert ids(second["items"]) == [opened[0].pk, done.pk, old_done.pk]
    top = first["items"][0]
    assert top["chat"] == {"code": group.code, "title": "Группа ПИ-24"}
    assert top["my_side"] == "no" and top["starred"] is True
    assert top["created_at"] == Question.objects.get(pk=opened[-1].pk).created_at.isoformat()
    assert second["items"][1]["status"] == "resolved"


# --- GET /api/chats/{code}/rating ------------------------------------------------------

def test_chat_rating_counts_only_chat_markets_and_members(anya, boris, vera):
    group = chat()
    join(group, ANYA)
    join(group, BORIS)
    in_chat = chat_market(group, BORIS)
    public = market_by(BORIS, title="Публичный рынок?")
    services.buy(trader(ANYA), in_chat, "yes", D(20))
    services.buy(trader(ANYA), public, "yes", D(100))
    services.buy(trader(VERA), public, "no", D(100))
    services.resolve(in_chat, "yes")
    services.resolve(public, "yes")
    code, board = anya.get(f"chats/{group.code}/rating")
    assert code == 200 and board["min_resolved"] == 1
    names = [row["display_name"] for row in board["items"]]
    assert sorted(names) == sorted([trader(ANYA).display_name, trader(BORIS).display_name])
    from market.rating import realized_profit
    assert D(board["me"]["profit"]) == realized_profit(trader(ANYA), None, group) > 0
    assert D(board["me"]["profit"]) < D(anya.get("rating")[1]["me"]["profit"])
    code, by_accuracy = anya.get(f"chats/{group.code}/rating", by="accuracy", period="week")
    assert code == 200 and [r["is_me"] for r in by_accuracy["items"]] == [True]
    assert by_accuracy["me"]["accuracy"] == 1.0


# --- POST /api/markets with a chat -----------------------------------------------------

def test_create_market_for_chat(anya):
    group = chat(members=40)
    join(group, ANYA)
    with telegram(count=12) as api:
        code, data = anya.post("markets", create_payload(chat=group.code))
    assert code == 200 and data["balance"] == "960.00"
    assert len(calls(api, "getChatMemberCount")) == 1
    market = Question.objects.get(pk=data["market"]["id"])
    assert market.group_id == group.pk and market.b == 57 and market.subsidy == D(40)
    assert data["market"]["b"] == 57 and data["market"]["chat"] == {"code": group.code, "title": "Группа ПИ-24"}
    group.refresh_from_db()
    assert group.member_count == 12
    props = Event.objects.get(name="market_create").props
    assert props["chat"] is True and group.code not in str(props) and str(group.telegram_chat_id) not in str(props)


def test_create_market_for_chat_is_idempotent(anya):
    group, other = chat(members=12), chat(members=12, telegram_chat_id=-100999)
    join(group, ANYA)
    join(other, ANYA)
    key = str(uuid.uuid4())
    with telegram(count=12):
        first = anya.post("markets", create_payload(chat=group.code, request_key=key))[1]
        code, again = anya.post("markets", create_payload(chat=group.code, request_key=key))
        assert code == 200 and again["market"]["id"] == first["market"]["id"] and again["balance"] == "960.00"
        code, data = anya.post("markets", create_payload(chat=other.code, request_key=key))
        assert code == 400
        code, data = anya.post("markets", create_payload(request_key=key))
        assert code == 400
    assert Question.objects.filter(creator=trader(ANYA)).count() == 1
    assert Event.objects.filter(name="market_create").count() == 1


def test_create_market_in_frozen_chat_is_400(anya):
    group = chat(bot_active=False)
    join(group, ANYA)
    with telegram() as api:
        code, data = anya.post("markets", create_payload(chat=group.code))
    assert code == 400 and "Бот больше не админ" in data["error"] and api.call_count == 0
    assert trader(ANYA).balance == D(1000) and not Question.objects.exists()


def test_create_market_for_foreign_or_unknown_chat_is_404(anya):
    group = chat()
    with telegram({ANYA: "left"}, count=5):
        assert anya.post("markets", create_payload(chat=group.code)) == (404, {"error": "Чат не найден."})
        assert anya.post("markets", create_payload(chat="nosuchcode"))[0] == 404
    join(group, ANYA, status="left")
    with telegram() as api:
        assert anya.post("markets", create_payload(chat=group.code))[0] == 404
    assert api.call_count == 0 and not Question.objects.exists() and trader(ANYA).balance == D(1000)


@pytest.mark.parametrize("value", [123, ["code"], {"code": "x"}, True])
def test_create_market_chat_must_be_a_string(anya, value):
    assert anya.post("markets", create_payload(chat=value))[0] == 400
    assert not Question.objects.exists()


def test_create_market_without_chat_is_public(anya):
    code, data = anya.post("markets", create_payload(chat=None))
    assert code == 200 and data["balance"] == "900.00" and data["market"]["chat"] is None
    assert "chat" not in Event.objects.get(name="market_create").props


# --- the feed, search, recommendations and similar -------------------------------------

def test_chat_markets_stay_out_of_feed_search_and_similar(anya, boris):
    group = chat()
    join(group, ANYA)
    join(group, BORIS)
    title = "Закроет ли больше половины потока сессию без троек?"
    in_chat = chat_market(group, BORIS, title=title)
    public = market_by(BORIS, title="Выпадет ли снег до декабря?")
    for sort in ("hot", "new", "soon", "recommended"):
        code, feed = anya.get("markets", sort=sort)
        assert code == 200 and ids(feed["items"]) == [public.pk] and feed["total"] == 1
    assert anya.get("markets", q="сессию")[1]["items"] == []
    assert anya.get("markets/similar", q="Сдам ли я сессию без троек?")[1]["items"] == []
    # Still the member's own: in the chat and in «Мои».
    assert ids(anya.get(f"chats/{group.code}/markets")[1]["items"]) == [in_chat.pk]
    created = boris.get("mine")[1]["created"]
    assert {c["id"]: c["chat"] for c in created} == {
        in_chat.pk: {"code": group.code, "title": "Группа ПИ-24"}, public.pk: None}


def test_old_group_markets_keep_their_place(anya, boris):
    group = Group.objects.create(name="Старая группа", kind="social")
    join(group, ANYA)
    join(group, BORIS)
    market = market_by(BORIS, title="Рынок старой группы?")
    Question.objects.filter(pk=market.pk).update(group=group)
    feed = anya.get("markets")[1]["items"]
    assert ids(feed) == [market.pk] and feed[0]["chat"] is None
    assert anya.get(f"markets/{market.pk}")[0] == 200


# --- market card and actions of a chat market ------------------------------------------

def test_chat_market_card_for_member_and_stranger(anya, boris, vera):
    group = chat()
    join(group, ANYA)
    join(group, BORIS)
    market = chat_market(group, BORIS)
    with telegram({VERA: "left"}) as api:
        code, card = anya.get(f"markets/{market.pk}")
        assert code == 200 and card["chat"] == {"code": group.code, "title": "Группа ПИ-24"}
        assert card["created_at"] == market.created_at.isoformat()
        assert card["b"] == market.b != 144  # chat markets have their own depth (sell estimate)
        assert api.call_count == 0
        stranger = vera.get(f"markets/{market.pk}")
        assert stranger == (404, {"error": "Рынок не найден."}) == vera.get("markets/999999")
        assert vera.post(f"markets/{market.pk}/buy", {"side": "yes", "amount": 10,
                                                      "request_key": str(uuid.uuid4())})[0] == 404
    assert trader(VERA).balance == D(1000)


def test_member_opens_chat_market_link_first(vera, boris):
    """A market link shared in the chat lets a member in without opening the chat first."""
    group = chat()
    join(group, BORIS)
    market = chat_market(group, BORIS)
    with telegram({VERA: "member"}):
        assert vera.get(f"markets/{market.pk}")[0] == 200
    assert GroupMembership.objects.get(group=group, trader=trader(VERA)).status == "active"


def test_left_member_loses_market_and_actions(anya, boris):
    group = chat()
    join(group, ANYA)
    join(group, BORIS)
    market = chat_market(group, BORIS)
    key = str(uuid.uuid4())
    assert anya.post(f"markets/{market.pk}/buy", {"side": "yes", "amount": 20, "request_key": key})[0] == 200
    chats.set_membership(group, trader(ANYA), False)
    with telegram() as api:
        for path in ("", "/history"):
            assert anya.get(f"markets/{market.pk}{path}")[0] == 404
        for path, payload in (("buy", {"side": "yes", "amount": 20, "request_key": str(uuid.uuid4())}),
                              ("sell", {"side": "yes", "shares": "1"}), ("quote", {"side": "yes", "amount": 10}),
                              ("star", {"starred": True}), ("report", {"reason": "спам"}),
                              ("dismiss", {"dismissed": True})):
            assert anya.post(f"markets/{market.pk}/{path}", payload)[0] == 404, path
    assert api.call_count == 0
    position = trader(ANYA).positions.get(question=market)
    assert position.yes_shares > 0
    # The open position lives on to the outcome and is paid as usual.
    services.resolve(market, "yes")
    assert trader(ANYA).balance > D(980)


def test_member_who_left_in_telegram_loses_access_after_cache(anya, boris):
    group = chat()
    membership = join(group, ANYA)
    join(group, BORIS)
    market = chat_market(group, BORIS)
    GroupMembership.objects.filter(pk=membership.pk).update(checked_at=timezone.now() - timedelta(minutes=11))
    with telegram({ANYA: "left"}):
        assert anya.post(f"markets/{market.pk}/quote", {"side": "yes", "amount": 10})[0] == 404


# --- POST /api/assist with a chat ------------------------------------------------------

def assist_events():
    return Event.objects.filter(name="assist_request").count()


def test_assist_off_in_chat_is_503_without_event(anya):
    group = chat(assist_enabled=False)
    join(group, ANYA)
    with patch("market.assist.requests.post") as model:
        code, data = anya.post("assist", {"title": "Сдам ли я сессию без троек?", "chat": group.code})
    assert code == 503 and data["error"] == "Помощник сейчас не отвечает — заполни правила сам."
    model.assert_not_called()
    assert assist_events() == 0


def test_assist_for_foreign_chat_is_404(anya):
    group = chat()
    with telegram(), patch("market.assist.requests.post") as model:
        assert anya.post("assist", {"title": "Сдам ли я сессию без троек?", "chat": group.code})[0] == 404
        assert anya.post("assist", {"title": "Сдам ли я сессию без троек?", "chat": 5})[0] == 400
    model.assert_not_called()
    assert assist_events() == 0


def test_assist_on_in_chat_asks_the_model(anya):
    from market.tests.test_assist import answer, model, reply
    group = chat()
    join(group, ANYA)
    with model(reply(answer())) as post:
        code, data = anya.post("assist", {"title": "Сдам ли я сессию без троек?", "chat": group.code})
    assert code == 200 and post.call_count == 1 and data["category"] == "study"
    assert assist_events() == 1
