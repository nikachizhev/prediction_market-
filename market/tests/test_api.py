import json
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone

from market import services
from market.telegram import TelegramError
from market.models import Event, Group, GroupMembership, LedgerEntry, Question, Trader
from market.tests.test_miniapp_auth import TOKEN, init_data

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("bot_token")]


@pytest.fixture
def bot_token():
    with override_settings(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_BOT_USERNAME="predbot"):
        yield


@pytest.fixture(autouse=True)
def bot_send():
    """Bot messages the API sends on its own (the note after creating a market) never reach Telegram."""
    with patch("market.telegram.send", return_value={"message_id": 1}) as send:
        yield send


class Api:
    def __init__(self, client, user_id, **user):
        self.client, self.header = client, f"tma {init_data(user_id, **user)}"

    def _call(self, method, path, data=None, **params):
        kwargs = {"HTTP_AUTHORIZATION": self.header}
        fn = getattr(self.client, method)
        if method == "get":
            response = fn(f"/api/{path}", params, **kwargs)
        else:
            response = fn(f"/api/{path}", json.dumps(data or {}), content_type="application/json", **kwargs)
        return response.status_code, response.json()

    def get(self, path, **params):
        return self._call("get", path, **params)

    def post(self, path, data=None):
        return self._call("post", path, data)

    def patch(self, path, data=None):
        return self._call("patch", path, data)


@pytest.fixture
def anya(client):
    api = Api(client, 1001, first_name="Аня", username="anya")
    api.post("session")
    return api


@pytest.fixture
def boris(client):
    api = Api(client, 1002, first_name="Борис")
    api.post("session")
    return api


def trader(user_id):
    return Trader.objects.get(telegram_chat_id=user_id)


def market_by(creator_id, **kw):
    now = timezone.now()
    args = dict(title="Будет снег?", criteria="Росгидромет", category="weather",
                closes_at=now + timedelta(days=2), resolves_at=now + timedelta(days=3))
    args.update(kw)
    return services.create_market(trader(creator_id), **args)


def test_only_session_creates_accounts(client):
    api = Api(client, 4242)
    code, data = api.get("me")
    assert code == 401 and not Trader.objects.filter(telegram_chat_id=4242).exists()
    assert api.post("session")[0] == 200 and api.get("me")[0] == 200


def test_trade_block_reason_and_my_side(anya, boris):
    m = market_by(1002)
    _, mine = boris.get(f"markets/{m.pk}")
    assert not mine["can_trade"] and "Создатель" in mine["trade_block"]
    anya.post(f"markets/{m.pk}/buy", {"side": "no", "amount": 20, "request_key": str(uuid.uuid4())})
    _, feed = anya.get("markets")
    assert feed["total"] == 1 and feed["items"][0]["my_side"] == "no"
    _, detail = anya.get(f"markets/{m.pk}")
    assert detail["can_trade"] and detail["trade_block"] is None and detail["my_side"] == "no"


def test_unsigned_requests_rejected(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", HTTP_AUTHORIZATION="tma user=%7B%22id%22%3A1%7D").status_code == 401


def test_session_creates_account_once(client):
    api = Api(client, 7, first_name="Саша")
    code, data = api.post("session", {"start_param": ""})
    assert code == 200 and data["new_account"] and data["me"]["balance"] == "1000.00"
    assert data["granted"] == [{"kind": "starter", "amount": "1000.00"}]
    assert data["me"]["referral_link"].startswith("https://t.me/predbot/app?startapp=ref_")
    code, data = api.post("session")
    assert not data["new_account"] and data["granted"] == [] and data["me"]["balance"] == "1000.00"
    assert Event.objects.filter(name="app_open").count() == 2


def test_daily_bonus_on_next_day(anya):
    t = trader(1001)
    t.daily_rewards.update(day=timezone.localdate() - timedelta(days=1))
    _, data = anya.post("session")
    assert data["granted"] == [{"kind": "daily", "amount": "20.00"}]


def test_referral_via_start_param(client, anya):
    code = trader(1001).referral_code
    friend = Api(client, 1003, start_param=f"ref_{code}")
    _, data = friend.post("session")
    assert data["me"]["referred_by"] == trader(1001).display_name


def test_patch_me(anya):
    code, data = anya.patch("me", {"pseudonym": "Капибара", "show_real_name": True, "onboarded": True})
    assert code == 200 and data["display_name"] == "Аня" and data["onboarded"]
    assert anya.patch("me", {"pseudonym": "x"})[0] == 400


def test_feed_filters_and_hides_private(anya, boris):
    market_by(1002)
    market_by(1002, title="Выиграет Спартак?", category="sport")
    group = Group.objects.create(name="Закрытая", owner=trader(1002))
    GroupMembership.objects.create(group=group, trader=trader(1002), status="active")
    private = services.create_question(title="Секрет", resolution_criteria="c", group_id=group.pk)
    _, data = anya.get("markets")
    titles = {m["title"] for m in data["items"]}
    assert titles == {"Будет снег?", "Выиграет Спартак?"}
    assert [m["title"] for m in anya.get("markets", category="sport")[1]["items"]] == ["Выиграет Спартак?"]
    assert [m["title"] for m in anya.get("markets", q="снег")[1]["items"]] == ["Будет снег?"]
    card = data["items"][0]
    assert len(card["spark"]) == 7 and card["prob"] == 0.5 and card["status"] == "open"
    assert anya.get(f"markets/{private.pk}")[0] == 404
    assert anya.post(f"markets/{private.pk}/buy", {"side": "yes", "amount": 10, "request_key": str(uuid.uuid4())})[0] == 404


def test_quote_buy_idempotent_and_anonymous_feed(anya, boris):
    m = market_by(1002)
    code, q = anya.post(f"markets/{m.pk}/quote", {"side": "yes", "amount": 100})
    assert code == 200 and q["fee"] == "2.00" and q["prob_after"] > 0.7
    key = str(uuid.uuid4())
    code, r1 = anya.post(f"markets/{m.pk}/buy", {"side": "yes", "amount": 100, "request_key": key})
    code2, r2 = anya.post(f"markets/{m.pk}/buy", {"side": "yes", "amount": 100, "request_key": key})
    assert code == code2 == 200 and r1["balance"] == r2["balance"]
    assert trader(1001).trades.count() == 1
    assert r1["market"]["my_position"]["spent"] == q["cost"]
    # Others see the trade in the feed but not who made it.
    _, seen = boris.get(f"markets/{m.pk}")
    assert seen["my_position"] is None and seen["participants"] == 1
    assert set(seen["trades"][0]) == {"side", "amount", "prob_before", "prob_after", "at", "sell"}
    assert "Аня" not in json.dumps(seen, ensure_ascii=False)


def test_creator_cannot_buy_and_limits_are_400(anya, boris):
    m = market_by(1002)
    code, data = boris.post(f"markets/{m.pk}/buy", {"side": "yes", "amount": 10, "request_key": str(uuid.uuid4())})
    assert code == 400 and "Создатель" in data["error"]
    code, data = anya.post(f"markets/{m.pk}/buy", {"side": "yes", "amount": 201, "request_key": str(uuid.uuid4())})
    assert code == 400 and "Максимум" in data["error"]
    assert anya.post(f"markets/{m.pk}/buy", {"side": "maybe", "amount": 1, "request_key": str(uuid.uuid4())})[0] == 400
    assert anya.post(f"markets/{m.pk}/buy", {"side": "yes", "amount": "abc", "request_key": str(uuid.uuid4())})[0] == 400


def test_sell(anya, boris):
    m = market_by(1002)
    _, r = anya.post(f"markets/{m.pk}/buy", {"side": "no", "amount": 50, "request_key": str(uuid.uuid4())})
    shares = r["market"]["my_position"]["no_shares"]
    code, s = anya.post(f"markets/{m.pk}/sell", {"side": "no", "shares": shares})
    assert code == 200 and Decimal(s["balance"]) < Decimal(1000) and s["market"]["my_position"] is None


def test_create_market(anya):
    now = timezone.now()
    code, data = anya.post("markets", {"title": "Сдадим матан?", "criteria": "Ведомость", "category": "study",
                                       "closes_at": (now + timedelta(days=5)).isoformat(),
                                       "resolves_at": (now + timedelta(days=6)).isoformat()})
    assert code == 200 and data["balance"] == "900.00" and data["market"]["is_creator"]
    assert not data["market"]["can_trade"]
    code, data = anya.post("markets", {"title": "Без даты", "criteria": "c", "category": "study"})
    assert code == 400


def create_payload(**kw):
    now = timezone.now()
    data = {"title": "Сдадим матан?", "criteria": "Ведомость", "category": "study",
            "closes_at": (now + timedelta(days=5)).isoformat(), "resolves_at": (now + timedelta(days=6)).isoformat()}
    data.update(kw)
    return data


def test_create_market_idempotent(anya):
    key = str(uuid.uuid4())
    code, first = anya.post("markets", create_payload(request_key=key))
    assert code == 200 and first["balance"] == "900.00"
    code, again = anya.post("markets", create_payload(request_key=key))
    assert code == 200 and again["market"]["id"] == first["market"]["id"] and again["balance"] == "900.00"
    t = trader(1001)
    assert Question.objects.filter(creator=t).count() == 1
    assert t.ledger.filter(kind=LedgerEntry.Kind.CREATION).count() == 1
    assert Event.objects.filter(name="market_create").count() == 1
    # Same key with another question is a client bug, not a second market.
    code, data = anya.post("markets", create_payload(request_key=key, title="Другой вопрос?"))
    assert code == 400 and Question.objects.filter(creator=t).count() == 1


@override_settings(MARKETS_PER_DAY=1)
def test_create_market_repeat_ignores_daily_limit(anya):
    key = str(uuid.uuid4())
    assert anya.post("markets", create_payload(request_key=key))[0] == 200
    code, data = anya.post("markets", create_payload(request_key=key))
    assert code == 200 and data["balance"] == "900.00"
    code, data = anya.post("markets", create_payload(request_key=str(uuid.uuid4())))
    assert code == 400 and "в день" in data["error"]


def test_create_market_key_of_another_trader_rejected(anya, boris):
    key = str(uuid.uuid4())
    _, first = anya.post("markets", create_payload(request_key=key))
    code, data = boris.post("markets", create_payload(request_key=key))
    assert code == 400 and "market" not in data and first["market"]["title"] not in data["error"]
    b = trader(1002)
    assert b.balance == Decimal("1000.00") and not Question.objects.filter(creator=b).exists()


def test_create_market_tells_the_creator_in_the_bot(anya, bot_send):
    resolves = timezone.make_aware(timezone.datetime(2026, 11, 16, 12, 0))
    key = str(uuid.uuid4())
    code, data = anya.post("markets", create_payload(request_key=key, title="Сдам сессию без троек?",
                                                     closes_at=(resolves - timedelta(days=1)).isoformat(),
                                                     resolves_at=resolves.isoformat()))
    assert code == 200 and data["bot_notified"] is True
    chat_id, text = bot_send.call_args.args
    assert chat_id == 1001
    assert "«Сдам сессию без троек?»" in text and "16 ноября напишу тебе здесь" in text and "фото или скриншот" in text
    button = bot_send.call_args.kwargs["reply_markup"]["inline_keyboard"][0][0]
    assert button["text"] == "Открыть рынок" and button["web_app"]["url"].endswith(f"/app/market/{data['market']['id']}")
    # The repeat of a lost response does not write again.
    code, again = anya.post("markets", create_payload(request_key=key, title="Сдам сессию без троек?",
                                                      closes_at=(resolves - timedelta(days=1)).isoformat(),
                                                      resolves_at=resolves.isoformat()))
    assert code == 200 and again["bot_notified"] is None and bot_send.call_count == 1


def test_create_market_when_the_bot_cannot_write(anya, bot_send):
    bot_send.side_effect = TelegramError("Telegram отклонил запрос.")
    code, data = anya.post("markets", create_payload())
    assert code == 200 and data["bot_notified"] is False and data["balance"] == "900.00"
    assert Question.objects.filter(creator=trader(1001)).count() == 1


def test_fun_category(anya):
    code, data = anya.get("categories")
    assert code == 200 and {"slug": "fun", "label": "Фан"} in data
    code, data = anya.post("markets", create_payload(category="fun"))
    assert code == 200 and data["market"]["category"] == "fun"


def test_create_market_bad_key(anya):
    code, _ = anya.post("markets", create_payload(request_key="not-a-uuid"))
    assert code == 400 and not Question.objects.exists()


def test_interests(anya, client):
    _, data = anya.get("me")
    assert data["interests"] == []
    code, data = anya.patch("me", {"interests": ["sport", "games", "sport"], "onboarded": True})
    assert code == 200 and data["interests"] == ["games", "sport"] and data["onboarded"]
    assert trader(1001).interests == ["games", "sport"]
    assert anya.get("me")[1]["interests"] == ["games", "sport"]
    assert anya.post("session")[1]["me"]["interests"] == ["games", "sport"]
    for bad in (["politics"], "games", [1], None):
        assert anya.patch("me", {"interests": bad})[0] == 400
    assert trader(1001).interests == ["games", "sport"]
    assert anya.patch("me", {"interests": []})[1]["interests"] == []


def test_interests_need_signed_init_data(client, anya):
    response = client.patch("/api/me", json.dumps({"interests": ["games"]}), content_type="application/json")
    assert response.status_code == 401
    response = client.patch("/api/me", json.dumps({"interests": ["games"]}), content_type="application/json",
                            HTTP_AUTHORIZATION="tma user=%7B%22id%22%3A1001%7D")
    assert response.status_code == 401 and trader(1001).interests == []


def test_star_and_mine(anya, boris):
    m = market_by(1002)
    other = market_by(1002, title="Другой")
    anya.post(f"markets/{other.pk}/star", {"starred": True})
    anya.post(f"markets/{m.pk}/buy", {"side": "yes", "amount": 30, "request_key": str(uuid.uuid4())})
    _, mine = anya.get("mine")
    assert [p["id"] for p in mine["positions"]] == [m.pk] and mine["positions"][0]["position"]
    assert {w["id"] for w in mine["watched"]} == {m.pk, other.pk}
    _, theirs = boris.get("mine")
    assert {c["id"] for c in theirs["created"]} == {m.pk, other.pk}
    created = next(c for c in theirs["created"] if c["id"] == m.pk)
    assert Decimal(created["fee_expected"]) > 0 and created["resolve_link"].endswith(f"resolve_{m.pk}")
    anya.post(f"markets/{other.pk}/star", {"starred": False})
    assert {w["id"] for w in anya.get("mine")[1]["watched"]} == {m.pk}


def test_three_complaints_hide_market(client, boris):
    m = market_by(1002)
    for uid in (2001, 2002, 2002, 2003):
        reporter = Api(client, uid)
        reporter.post("session")
        reporter.post(f"markets/{m.pk}/report", {"reason": "спам"})
    m.refresh_from_db()
    assert m.hidden_at and m.complaints.count() == 3
    assert boris.get("markets")[1]["items"] == []


def test_rating_and_profile(anya, boris):
    m = market_by(1002)
    anya.post(f"markets/{m.pk}/buy", {"side": "yes", "amount": 100, "request_key": str(uuid.uuid4())})
    services.resolve(m, "yes")
    _, rating = anya.get("rating")
    assert rating["items"][0]["display_name"] == trader(1001).display_name and rating["me"]["rank"] == 1
    _, acc = anya.get("rating", by="accuracy", period="week")
    assert acc["items"] == [] and acc["me"] is None  # one resolved prediction is below the threshold
    with override_settings(RATING_ACCURACY_MIN_RESOLVED=1):
        _, acc = anya.get("rating", by="accuracy", period="week")
    assert acc["items"][0]["value"] == "1.0000"
    _, friends = anya.get("rating", scope="friends")
    assert [i["is_me"] for i in friends["items"]] == [True]
    _, profile = anya.get("profile")
    kinds = [e["kind"] for e in profile["ledger"]]
    assert kinds[-1] == "starter" and "buy" in kinds and "payout" in kinds
    assert profile["stats"]["predictions"] == 1 and profile["stats"]["accuracy"] == 1.0
    assert Decimal(profile["stats"]["profit"]) > 0


def test_client_events(anya):
    assert anya.post("events", {"name": "share", "props": {"market": 1}})[0] == 200
    assert anya.post("events", {"name": "drop_tables"})[0] == 400
    assert Event.objects.filter(name="share").exists()
