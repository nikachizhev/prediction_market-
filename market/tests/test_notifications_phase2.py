"""Spec E6.3 (bot notifications, inbox) and E5 (referral rewards). Telegram is always mocked."""
import math
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.test import override_settings
from django.utils import timezone

from market import bot_notifications as notices
from market import bot_resolution, services
from market.models import (BotMessage, Group, GroupMembership, LedgerEntry, MarketSubscription, Notification,
                           Question, ReferralReward, Trade, Trader)
from market.tests.test_api import Api, bot_token  # noqa: F401 - fixture
from market.tests.test_api import trader as api_trader

D = Decimal
MSK = ZoneInfo("Europe/Moscow")
NOON = datetime(2026, 11, 3, 12, 0, tzinfo=MSK)

pytestmark = pytest.mark.django_db


def make(name, chat=None, balance=1000, **kw):
    return Trader.objects.create(name=name, pseudonym=name, balance=D(balance), telegram_chat_id=chat, **kw)


def market(title="Снег до 1 декабря?", creator=None, **kw):
    return Question.objects.create(title=title, resolution_criteria="c", kind=Question.Kind.LMSR, b=144,
                                   creator=creator, **kw)


@pytest.fixture
def send():
    with patch("market.telegram.send") as mock:
        yield mock


# --- outbox ------------------------------------------------------------------------------------

def test_enqueue_is_idempotent_by_key():
    anya = make("Аня", chat=1)
    for _ in range(2):
        notices.enqueue(anya, "moves", "k1", "Заголовок:", "текст", now=NOON)
    assert Notification.objects.filter(key="k1").count() == 1
    assert BotMessage.objects.get(key="k1").text == "Заголовок: текст"


def test_without_telegram_only_inbox():
    anya = make("Аня")
    notices.enqueue(anya, "results", "k1", "Ты угадал! +5", "текст", now=NOON)
    assert Notification.objects.filter(recipient=anya).exists() and not BotMessage.objects.exists()


def test_switched_off_kind_skipped_in_bot_but_in_inbox(send):
    anya = make("Аня", chat=1, notify_prefs={"moves": False})
    notices.enqueue(anya, "moves", "k1", "А:", "б", now=NOON)
    assert Notification.objects.filter(key="k1").exists() and not BotMessage.objects.exists()
    # Switched off after it was queued: skipped at send time.
    notices.enqueue(anya, "closing", "k2", "А:", "б", now=NOON)
    Trader.objects.filter(pk=anya.pk).update(notify_prefs={"moves": False, "closing": False})
    assert notices.flush(NOON)["skipped"] == 1
    assert BotMessage.objects.get(key="k2").status == "skipped" and not send.called


def test_quiet_hours_postpone_to_nine(send):
    anya = make("Аня", chat=1)
    late = datetime(2026, 11, 3, 23, 30, tzinfo=MSK)
    notices.enqueue(anya, "moves", "k1", "А:", "б", now=late)
    assert notices.flush(late)["postponed"] == 1
    message = BotMessage.objects.get(key="k1")
    assert message.status == "queued" and message.send_after == datetime(2026, 11, 4, 9, 0, tzinfo=MSK)
    assert notices.flush(datetime(2026, 11, 4, 8, 59, tzinfo=MSK)) == {"sent": 0, "skipped": 0, "failed": 0, "postponed": 0}
    assert notices.flush(datetime(2026, 11, 4, 9, 0, tzinfo=MSK))["sent"] == 1
    assert send.call_args.args == (1, "А: б")


def test_queued_message_is_claimed_once(send):
    anya = make("Аня", chat=1)
    notices.enqueue(anya, "moves", "k1", "А:", "б", now=NOON)
    first, second = BotMessage.objects.get(key="k1"), BotMessage.objects.get(key="k1")
    # Two worker processes read the same row: only one may send it.
    assert notices._claim(first, NOON) is True
    assert notices._claim(second, NOON) is False
    # A lease that was never released (worker died) expires and the message goes out once.
    assert notices.flush(NOON)["sent"] == 0
    assert notices.flush(NOON + notices.LEASE)["sent"] == 1 and send.call_count == 1


def test_mini_app_account_gets_no_legacy_change_alert():
    from django.contrib.auth.models import User
    from market.subscriptions import price_changed
    q = market()
    app_user = make("Аня", chat=1)
    site_user = make("Борис", user=User.objects.create(username="boris"))
    for who in (app_user, site_user):
        MarketSubscription.objects.create(trader=who, question=q, last_probability=0.3)
    price_changed(q)
    kinds = set(Notification.objects.filter(kind="market_change").values_list("recipient__name", flat=True))
    assert kinds == {"Борис"}


def test_daily_cap_drops_optional_but_not_results(send):
    anya = make("Аня", chat=1)
    for i in range(6):
        notices.enqueue(anya, "moves", f"m{i}", "А:", f"{i}", now=NOON)
    notices.enqueue(anya, "results", "r", "Ты угадал! +5", "итог", now=NOON)
    done = notices.flush(NOON)
    assert done["sent"] == 6 and done["skipped"] == 1
    assert BotMessage.objects.get(key="m5").status == "skipped" and BotMessage.objects.get(key="r").status == "sent"
    # Next Moscow day the counter starts over.
    notices.enqueue(anya, "moves", "next", "А:", "б", now=NOON + timedelta(days=1))
    assert notices.flush(NOON + timedelta(days=1))["sent"] == 1


def test_telegram_errors_retried_then_failed(send):
    send.side_effect = RuntimeError("down")
    anya = make("Аня", chat=1)
    notices.enqueue(anya, "results", "k1", "А:", "б", now=NOON)
    now = NOON
    for attempt in range(1, 6):
        now += timedelta(minutes=10)
        notices.flush(now)
        message = BotMessage.objects.get(key="k1")
        assert message.attempts == attempt
    assert message.status == "failed" and send.call_count == 5
    notices.flush(now + timedelta(hours=1))
    assert send.call_count == 5


# --- detectors ---------------------------------------------------------------------------------

def set_prob(question, p):
    Question.objects.filter(pk=question.pk).update(q_yes=question.b * math.log(p / (1 - p)), q_no=0)


def trade(question, trader, prob_after, at):
    t = Trade.objects.create(trader=trader, question=question, side="yes", shares=D(1), cost=D(1),
                             prob_before=0.5, prob_after=prob_after)
    Trade.objects.filter(pk=t.pk).update(created_at=at)


@pytest.mark.parametrize("prob, expected", [(0.599, 0), (0.6, 1)])
def test_move_threshold(prob, expected):
    anya, boris = make("Аня", chat=1), make("Борис", chat=2)
    q = market(deadline=NOON + timedelta(days=5))
    trade(q, boris, 0.5, NOON - timedelta(hours=30))
    trade(q, boris, prob, NOON - timedelta(hours=1))
    set_prob(q, prob)
    MarketSubscription.objects.create(trader=anya, question=q)
    assert notices.detect_moves(NOON) == expected


def test_move_once_per_day_with_position():
    anya = make("Аня", chat=1)
    q = market(title="Снег до 1 декабря", deadline=NOON + timedelta(days=5))
    services.buy(anya, q, "no", 120)
    Trade.objects.filter(trader=anya).update(created_at=NOON - timedelta(hours=30))
    trade(q, make("Борис"), 0.64, NOON - timedelta(hours=1))
    set_prob(q, 0.64)
    MarketSubscription.objects.create(trader=anya, question=q)
    assert notices.detect_moves(NOON) == 1
    assert notices.detect_moves(NOON + timedelta(hours=2)) == 0
    note = Notification.objects.get(kind="moves")
    assert note.title == "Снег до 1 декабря:"
    assert note.text.startswith("вероятность выросла до 64% (▲ ") and note.text.endswith("У тебя НЕТ на 120.")


def test_closing_once_not_to_creator():
    creator, anya, boris = make("Автор", chat=1), make("Аня", chat=2), make("Борис", chat=3)
    q = market(creator=creator, deadline=NOON + timedelta(hours=23, minutes=30))
    MarketSubscription.objects.create(trader=anya, question=q)
    MarketSubscription.objects.create(trader=creator, question=q)
    far = market(title="Далеко", deadline=NOON + timedelta(days=3))
    MarketSubscription.objects.create(trader=boris, question=far)
    assert notices.detect_closing(NOON) == 1
    assert notices.detect_closing(NOON + timedelta(minutes=5)) == 0
    note = Notification.objects.get(kind="closing")
    assert note.recipient == anya and note.title == "Через 24 часа"
    assert note.text == "закрывается приём прогнозов: «Снег до 1 декабря?»"


# --- results and creator events ----------------------------------------------------------------

def test_results_confetti_only_for_winners(send):
    anya, boris = make("Аня", chat=1), make("Борис", chat=2)
    q = market()
    services.buy(anya, q, "yes", 100)
    services.buy(boris, q, "no", 50)
    services.resolve(q, "yes")
    q.refresh_from_db()
    bot_resolution.send_results(q)
    bot_resolution.send_results(q)
    notices.flush(NOON)
    calls = {c.args[0]: c for c in send.call_args_list}
    assert len(send.call_args_list) == 2
    assert calls[1].args[1].startswith("Ты угадал! +") and calls[1].kwargs["message_effect_id"] == bot_resolution.CONFETTI_EFFECT_ID
    assert "message_effect_id" not in calls[2].kwargs and "мимо" in calls[2].args[1]
    # The plain market_finished notice was upgraded, not duplicated.
    winner = Notification.objects.filter(recipient=anya, question=q)
    assert winner.count() == 1 and winner[0].kind == "results" and winner[0].title.startswith("Ты угадал! +")


def test_dispute_and_moderator_notify_creator(django_capture_on_commit_callbacks):
    creator, anya = make("Автор", chat=1), make("Аня", chat=2)
    q = market(creator=creator)
    services.buy(anya, q, "yes", 100)
    Question.objects.filter(pk=q.pk).update(status=Question.Status.CLOSED, resolution_state="pending",
                                            proposed_outcome="no", dispute_until=timezone.now() + timedelta(hours=1))
    with django_capture_on_commit_callbacks(execute=True):
        services.open_dispute(anya, q)
    note = Notification.objects.get(recipient=creator, kind="creator")
    assert note.title == "Итог оспорили:" and BotMessage.objects.filter(trader=creator, kind="creator").count() == 1
    with django_capture_on_commit_callbacks(execute=True):
        services.moderator_decide(q, "yes")
    assert Notification.objects.get(key=f"moderator:{q.pk}").text.startswith("«Снег до 1 декабря?» — спор признан")


# --- referral rewards --------------------------------------------------------------------------

@pytest.fixture
def pair():
    inviter = make("Ёж", chat=1)
    friend = make("Енот", chat=2, referred_by=inviter)
    return inviter, friend


def buy_in(friend, n, **kw):
    for i in range(n):
        services.buy(friend, market(title=f"Рынок {i}"), "yes", 10, **kw)


def test_reward_after_five_distinct_markets(pair, django_capture_on_commit_callbacks):
    inviter, friend = pair
    buy_in(friend, 4)
    q = Question.objects.get(title="Рынок 0")
    services.buy(friend, q, "yes", 10)  # the same market again does not count
    assert not ReferralReward.objects.exists()
    with django_capture_on_commit_callbacks(execute=True):
        services.buy(friend, market(title="Пятый"), "no", 10)
    reward = ReferralReward.objects.get()
    assert reward.granted and reward.referrer == inviter
    inviter.refresh_from_db()
    assert inviter.balance == D(1300)
    assert LedgerEntry.objects.filter(kind="referral", amount=D(300)).count() == 2
    assert services.profit(inviter) == 0  # bonus coins are not profit
    note = Notification.objects.get(recipient=inviter, kind="referral")
    assert (note.title, note.text) == ("Енот сделал 5 прогнозов —", "вам обоим по +300 монет за приглашение.")
    assert "Ёж" in Notification.objects.get(recipient=friend, kind="referral").text
    # No second reward ever.
    services.buy(friend, market(title="Шестой"), "yes", 10)
    assert LedgerEntry.objects.filter(kind="referral").count() == 2


def test_request_key_replay_grants_nothing_new(pair):
    inviter, friend = pair
    buy_in(friend, 4)
    key = uuid.uuid4()
    q = market(title="Пятый")
    services.buy(friend, q, "yes", 10, request_key=key)
    friend.refresh_from_db()
    balance = friend.balance
    services.buy(friend, q, "yes", 10, request_key=key)
    friend.refresh_from_db()
    assert friend.balance == balance and ReferralReward.objects.count() == 1
    assert LedgerEntry.objects.filter(kind="referral").count() == 2


def test_monthly_cap_pays_nobody(pair):
    inviter, friend = pair
    for i in range(10):
        ReferralReward.objects.create(friend=make(f"Друг {i}"), referrer=inviter, granted=True)
    buy_in(friend, 5)
    reward = ReferralReward.objects.get(friend=friend)
    assert not reward.granted
    inviter.refresh_from_db()
    assert inviter.balance == D(1000) and not LedgerEntry.objects.filter(kind="referral").exists()


def test_last_month_rewards_do_not_count(pair):
    inviter, friend = pair
    for i in range(10):
        r = ReferralReward.objects.create(friend=make(f"Друг {i}"), referrer=inviter, granted=True)
        ReferralReward.objects.filter(pk=r.pk).update(created_at=timezone.now() - timedelta(days=40))
    buy_in(friend, 5)
    assert ReferralReward.objects.get(friend=friend).granted


# --- API ---------------------------------------------------------------------------------------

@pytest.fixture
def anya(client, bot_token):  # noqa: F811
    api = Api(client, 1001, first_name="Аня")
    api.post("session")
    return api


def test_me_notify_patch(anya):
    _, me = anya.get("me")
    assert me["notify"] == {k: True for k in notices.KINDS} and me["unread_notifications"] == 0
    code, me = anya.patch("me", {"notify": {"moves": False}})
    assert code == 200 and me["notify"]["moves"] is False and me["notify"]["results"] is True
    _, me = anya.patch("me", {"notify": {"closing": False}})
    assert me["notify"]["moves"] is False and me["notify"]["closing"] is False
    assert anya.patch("me", {"notify": {"spam": True}})[0] == 400
    assert anya.patch("me", {"notify": {"moves": "нет"}})[0] == 400
    assert anya.patch("me", {"notify": ["moves"]})[0] == 400


def test_notifications_list_filters_and_visibility(anya, bot_token):  # noqa: F811
    me = api_trader(1001)
    other = make("Борис", chat=2)
    q = market(title="Открытый")
    group = Group.objects.create(name="Закрытая", owner=other)
    private = market(title="Секрет", group=group)
    notices.enqueue(me, "results", "r", "Ты угадал! +5", "итог", question=q)
    notices.enqueue(me, "moves", "m", "Открытый:", "вероятность", question=q)
    notices.enqueue(me, "referral", "f", "Енот сделал 5 прогнозов —", "вам обоим")
    Notification.objects.create(recipient=me, kind="resolve", title="Пора указать итог:", text="…", question=q, key="a")
    notices.enqueue(me, "moves", "hidden", "Секрет:", "…", question=private)
    notices.enqueue(other, "moves", "theirs", "Чужое:", "…", question=q)
    code, data = anya.get("notifications")
    assert code == 200 and data["unread"] == 4 and len(data["items"]) == 4
    by_kind = {n["kind"]: n for n in data["items"]}
    assert by_kind["resolve"]["action"] == "resolve" and by_kind["resolve"]["group"] == "mine"
    assert by_kind["moves"]["action"] == "market" and by_kind["moves"]["market"] == {"id": q.pk, "title": "Открытый"}
    assert by_kind["referral"]["action"] is None and by_kind["referral"]["market"] is None
    assert {n["kind"] for n in anya.get("notifications", filter="pay")[1]["items"]} == {"results", "referral"}
    assert [n["kind"] for n in anya.get("notifications", filter="markets")[1]["items"]] == ["moves"]
    assert [n["kind"] for n in anya.get("notifications", filter="mine")[1]["items"]] == ["resolve"]
    assert anya.get("notifications", filter="chats")[0] == 400
    # Joining the group reveals the notice about its market.
    GroupMembership.objects.create(group=group, trader=me, status="active")
    assert anya.get("notifications")[1]["unread"] == 5


def test_notifications_read(anya, bot_token):  # noqa: F811
    me = api_trader(1001)
    other = make("Борис", chat=2)
    first, _ = notices.enqueue(me, "referral", "a", "А", "б")
    notices.enqueue(me, "referral", "b", "А", "б")
    theirs, _ = notices.enqueue(other, "referral", "c", "А", "б")
    assert anya.post("notifications/read", {"ids": [first.pk, theirs.pk]}) == (200, {"unread": 1})
    assert not Notification.objects.get(pk=theirs.pk).read_at
    assert anya.post("notifications/read", {"ids": "все"})[0] == 400
    assert anya.post("notifications/read") == (200, {"unread": 0})
    assert anya.get("me")[1]["unread_notifications"] == 0
