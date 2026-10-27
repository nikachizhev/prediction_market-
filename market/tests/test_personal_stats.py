"""Phase 2 personal statistics (spec E7): referral block, categories, best predictions, history, top moves."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from market import services
from market.models import (Group, GroupMembership, LedgerEntry, Payout, Question, ReferralReward, Trade,
                           Trader)
from market.statistics import HISTORY_POINTS, balance_profit_history
from market.tests.test_api import Api, anya, boris, bot_token, market_by, trader  # noqa: F401
from market.tests.test_profile_rating_api import fake_buy

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("bot_token")]


def market(title="Рынок", category="weather", **kw):
    now = timezone.now()
    return services.create_question(title=title, resolution_criteria="Итог", category=category,
                                    deadline=now + timedelta(days=2), resolves_at=now + timedelta(days=3), **kw)


def private_market(title="Приватный"):
    owner = Trader.objects.create(name="Хозяин", pseudonym="Хозяин")
    group = Group.objects.create(name="Клуб", owner=owner)
    return market(title, group_id=group.pk), group


def result(user_id, question):
    """What a market brought the trader: payouts minus net trade cost."""
    t = trader(user_id)
    paid = Payout.objects.filter(trader=t, question=question).aggregate(s=Sum("amount"))["s"] or Decimal(0)
    cost = Trade.objects.filter(trader=t, question=question).aggregate(s=Sum("cost"))["s"] or Decimal(0)
    return paid - cost


# --- referral ------------------------------------------------------------------------------

def test_profile_referral_summary(anya, boris):
    me = trader(1001)
    Trader.objects.filter(telegram_chat_id=1002).update(referred_by=me)
    vika = Trader.objects.create(name="Вика", pseudonym="Вика", referred_by=me)
    ReferralReward.objects.create(friend=trader(1002), referrer=me, granted=True)
    ReferralReward.objects.create(friend=vika, referrer=me, granted=False)  # monthly cap: nobody was paid
    LedgerEntry.objects.create(trader=me, kind=LedgerEntry.Kind.REFERRAL, amount=Decimal(300),
                               balance_after=me.balance + 300)
    _, profile = anya.get("profile")
    assert profile["referral"] == {"invited": 2, "rewarded": 1, "earned": "300.00",
                                   "reward": f"{Decimal(settings.REFERRAL_REWARD):.2f}",
                                   "predictions_needed": settings.REFERRAL_PREDICTIONS}
    assert profile["invited"] == 2
    _, other = boris.get("profile")
    assert other["referral"]["invited"] == 0 and other["referral"]["rewarded"] == 0
    assert other["referral"]["earned"] == "0.00"


# --- categories ----------------------------------------------------------------------------

def test_profile_categories(anya):
    won, lost = market("Снег?"), market("Дождь?")
    sport = market("Матч?", category="sport")
    open_tech = market("Релиз?", category="tech")
    services.buy(trader(1001), won, "yes", Decimal(20))
    services.buy(trader(1001), lost, "no", Decimal(30))
    services.buy(trader(1001), sport, "yes", Decimal(10))
    services.buy(trader(1001), open_tech, "yes", Decimal(10))
    services.resolve(won, "yes")
    services.resolve(lost, "yes")
    services.resolve(sport, "yes")
    # A bonus that somehow points at a market still must not count as profit.
    me = trader(1001)
    LedgerEntry.objects.create(trader=me, kind=LedgerEntry.Kind.INSURANCE, amount=Decimal(50),
                               balance_after=me.balance, question=won)
    _, profile = anya.get("profile")
    weather_profit = result(1001, won) + result(1001, lost)
    assert profile["categories"] == [
        {"slug": "weather", "label": "Погода", "predictions": 2, "correct": 1, "accuracy": 0.5,
         "profit": f"{weather_profit:.2f}"},
        {"slug": "sport", "label": "Спорт", "predictions": 1, "correct": 1, "accuracy": 1.0,
         "profit": f"{result(1001, sport):.2f}"},
    ]
    assert result(1001, lost) < 0 < result(1001, won)


def test_categories_skip_hidden_private_markets_and_neutral_positions(anya):
    hidden, group = private_market()
    GroupMembership.objects.create(group=group, trader=trader(1001), status="active")
    services.buy(trader(1001), hidden, "yes", Decimal(10))
    services.resolve(hidden, "yes")
    GroupMembership.objects.filter(group=group).delete()  # left the group: the market is not visible now
    neutral = market("Ровно")
    services.buy(trader(1001), neutral, "yes", Decimal(10))
    pos = trader(1001).positions.get(question=neutral)
    pos.no_shares = pos.yes_shares
    pos.save()
    services.resolve(neutral, "yes")
    _, profile = anya.get("profile")
    assert profile["categories"] == []


# --- best predictions ----------------------------------------------------------------------

def test_best_predictions_order_and_visibility(anya):
    markets = [market(f"Рынок {i}") for i in range(4)]
    for m, price in zip(markets, (0.30, 0.12, 0.50, 0.20)):
        fake_buy(1001, m, "yes", price, shares="100", cost=str(int(price * 100) + 1))
    fake_buy(1001, markets[0], "no", 0.99)  # NO at 1%, but lost
    hidden, _ = private_market()
    fake_buy(1001, hidden, "yes", 0.05, shares="100", cost="6")  # cheapest win, but not visible to me
    for m in markets + [hidden]:
        services.resolve(m, "yes")
    _, profile = anya.get("profile")
    best = profile["best_predictions"]
    assert [b["price"] for b in best] == [0.12, 0.2, 0.3]
    assert [b["market"]["id"] for b in best] == [markets[1].pk, markets[3].pk, markets[0].pk]
    assert best[0] == profile["best_prediction"]
    assert best[0] == {"market": {"id": markets[1].pk, "title": "Рынок 1"}, "side": "yes", "price": 0.12,
                       "gain": "87.00"}


def test_best_predictions_empty(anya):
    _, profile = anya.get("profile")
    assert profile["best_predictions"] == [] and profile["best_prediction"] is None
    assert profile["categories"] == []


# --- balance and profit history ------------------------------------------------------------

def ledger_line(t, kind, amount, balance, at, question=None):
    entry = LedgerEntry.objects.create(trader=t, kind=kind, amount=Decimal(amount), balance_after=Decimal(balance),
                                       question=question)
    LedgerEntry.objects.filter(pk=entry.pk).update(created_at=at)


@pytest.fixture
def story():
    """Starter 1000; a 100 stake that pays 250 when the market resolves; a creation fee in a market
    that is still open; a daily bonus. Profit becomes 150 only when the first market resolves."""
    now = timezone.now()
    t = Trader.objects.create(name="Гоша", pseudonym="Гоша", balance=Decimal(1070))
    Trader.objects.filter(pk=t.pk).update(created_at=now - timedelta(days=10))
    t.refresh_from_db()
    settled, still_open = market("Итог"), market("Идёт")
    Question.objects.filter(pk=settled.pk).update(status=Question.Status.RESOLVED, outcome="yes",
                                                  resolved_at=now - timedelta(days=2))
    K = LedgerEntry.Kind
    ledger_line(t, K.STARTER, 1000, 1000, now - timedelta(days=10))
    ledger_line(t, K.BUY, -100, 900, now - timedelta(days=5), settled)
    ledger_line(t, K.PAYOUT, 250, 1150, now - timedelta(days=2), settled)
    ledger_line(t, K.CREATION, -100, 1050, now - timedelta(hours=12), still_open)
    ledger_line(t, K.DAILY, 20, 1070, now - timedelta(hours=6))
    return t, now


def rows(points):
    return [(p["t"], p["balance"], p["profit"]) for p in points]


def test_history_all_and_week(story):
    t, now = story
    d = timedelta(days=1)
    expected = [(now - 10 * d, 1000, 0), (now - 5 * d, 900, 0), (now - 2 * d, 1150, 150),
                (now - d / 2, 1050, 150), (now - d / 4, 1070, 150), (now, 1070, 150)]
    assert rows(balance_profit_history(t, "all", now=now)) == expected
    week = rows(balance_profit_history(t, "w", now=now))
    assert week == [(now - 7 * d, 1000, 0)] + expected[1:]


def test_history_day_starts_with_the_state_before_the_period(story):
    t, now = story
    d = timedelta(days=1)
    assert rows(balance_profit_history(t, "d", now=now)) == [
        (now - d, 1150, 150), (now - d / 2, 1050, 150), (now - d / 4, 1070, 150), (now, 1070, 150)]


def test_history_counts_market_lines_when_settled_including_annulled(story):
    t, now = story
    annulled = market("Отменён")
    Question.objects.filter(pk=annulled.pk).update(status=Question.Status.ANNULLED,
                                                   resolved_at=now - timedelta(hours=3))
    ledger_line(t, LedgerEntry.Kind.BUY, -40, 1030, now - timedelta(hours=5), annulled)
    ledger_line(t, LedgerEntry.Kind.REFUND, 30, 1060, now - timedelta(hours=3), annulled)
    points = balance_profit_history(t, "d", now=now)
    assert [p["profit"] for p in points] == [150, 150, 150, 150, 140, 140]
    assert [p["balance"] for p in points][-2] == 1060


def test_history_is_thinned_to_200_points(story):
    t, now = story
    for i in range(300):
        ledger_line(t, LedgerEntry.Kind.DAILY, 1, 1070 + i, now - timedelta(hours=5, minutes=300 - i))
    points = balance_profit_history(t, "all", now=now)
    assert len(points) == HISTORY_POINTS
    assert points[0]["t"] == now - timedelta(days=10) and points[-1]["t"] == now


def test_history_endpoint(anya):
    m = market("Снег?")
    services.buy(trader(1001), m, "yes", Decimal(20))
    code, data = anya.get("profile/history", period="m")
    assert code == 200
    points = data["points"]
    assert set(points[0]) == {"t", "balance", "profit"}
    # The stake in an unsettled market is not a loss yet.
    assert {p["profit"] for p in points} == {"0.00"}
    assert points[-1]["balance"] == f"{trader(1001).balance:.2f}"
    services.resolve(m, "yes")
    _, data = anya.get("profile/history", period="d")
    assert data["points"][-1]["profit"] == f"{result(1001, m):.2f}"
    assert data["points"][-1]["profit"] != "0.00"


# --- top moves -----------------------------------------------------------------------------

def test_market_top_moves(anya, boris):
    m = market_by(1002)
    now = timezone.now()
    moves = [(0.5, 0.5625, 5), (0.75, 0.5, 4), (0.5, 0.5, 3), (0.5, 0.625, 2), (0.25, 0.5, 1)]
    for before, after, hours_ago in moves:
        t = Trade.objects.create(trader=trader(1001), question=m, side="yes", shares=Decimal(1), cost=Decimal(1),
                                 prob_before=before, prob_after=after)
        Trade.objects.filter(pk=t.pk).update(created_at=now - timedelta(hours=hours_ago))
    _, detail = boris.get(f"markets/{m.pk}")
    top = detail["top_moves"]
    # 0.25 twice (newest first), then 0.125; the zero move never shows.
    assert [(x["from"], x["to"]) for x in top] == [(0.25, 0.5), (0.75, 0.5), (0.5, 0.625)]
    assert all(set(x) == {"at", "from", "to"} for x in top)
    assert top[0]["at"] == (now - timedelta(hours=1)).isoformat()


def test_market_top_moves_empty(anya, boris):
    m = market_by(1002)
    _, detail = anya.get(f"markets/{m.pk}")
    assert detail["top_moves"] == []
