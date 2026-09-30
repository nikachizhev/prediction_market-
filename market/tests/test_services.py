from decimal import Decimal

import pytest
from django.conf import settings

from market import services
from market.models import Payout, Position, Question, Trader
from market.services import MarketError

D = Decimal


@pytest.fixture
def q(db):
    return services.create_question(title="T", resolution_criteria="C", initial_prob=0.5)


@pytest.fixture
def anya(db):
    return Trader.objects.create(name="Аня", pseudonym="A")


@pytest.fixture
def boris(db):
    return Trader.objects.create(name="Борис", pseudonym="B")


def test_create_question_sets_initial_price(db):
    q = services.create_question(title="t", resolution_criteria="c", initial_prob=0.25)
    assert q.prob == pytest.approx(0.25)


def test_quote_does_not_mutate(q):
    r = services.quote(q, "yes", 100)
    assert r["new_prob"] > 0.5 and r["shares"] > 100 and r["cost"] <= 100
    q.refresh_from_db()
    assert q.prob == pytest.approx(0.5)


def test_buy_updates_balance_position_trade(q, anya):
    t = services.buy(anya, q, "yes", 100)
    anya.refresh_from_db()
    q.refresh_from_db()
    assert anya.balance == D(1000) - t.cost
    assert D(99) <= t.cost <= D(100)
    assert t.prob_before == pytest.approx(0.5) and t.prob_after == pytest.approx(q.prob)
    assert q.prob == pytest.approx(0.70, abs=0.02)
    pos = Position.objects.get(trader=anya, question=q)
    assert pos.yes_shares == t.shares and pos.spent == t.cost


def test_cost_rounded_up_never_below_real_cost(q, anya):
    from market import lmsr

    t = services.buy(anya, q, "no", D("33.33"))
    real = lmsr.trade_cost("no", float(t.shares), 0.0, 0.0, q.b)
    assert float(t.cost) >= real - 1e-9


def test_balance_never_negative(q, anya):
    anya.balance = D(50)
    anya.save()
    with pytest.raises(MarketError):
        services.buy(anya, q, "yes", 60)
    with pytest.raises(MarketError):
        services.buy(anya, q, "yes", 0)
    services.buy(anya, q, "yes", 50)
    anya.refresh_from_db()
    assert anya.balance >= 0


def test_max_bet(q, anya):
    with pytest.raises(MarketError):
        services.buy(anya, q, "yes", settings.MAX_BET + 1)


def test_conflicted_trader_blocked(anya):
    q = services.create_question(title="t", resolution_criteria="c", conflicted_traders=[anya])
    with pytest.raises(MarketError):
        services.buy(anya, q, "yes", 10)


def test_cannot_bet_when_closed_or_resolved(q, anya):
    services.close_betting(q)
    with pytest.raises(MarketError):
        services.buy(anya, q, "yes", 10)
    services.resolve(q, "yes")
    with pytest.raises(MarketError):
        services.buy(anya, q, "yes", 10)
    with pytest.raises(MarketError):
        services.resolve(q, "no")


def test_payouts_equal_winning_shares(q, anya, boris):
    services.buy(anya, q, "yes", 100)
    services.buy(boris, q, "no", 80)
    services.buy(anya, q, "yes", 33)
    yes_shares = sum(p.yes_shares for p in Position.objects.filter(question=q))
    services.resolve(q, "yes")
    total = sum(p.amount for p in Payout.objects.filter(question=q))
    # each holder's payout is rounded down to 0.01: tolerance 0.01 per holder
    assert yes_shares - D("0.02") <= total <= yes_shares
    anya.refresh_from_db()
    boris.refresh_from_db()
    assert boris.balance < D(1000)
    assert Payout.objects.filter(trader=boris).count() == 0
    q.refresh_from_db()
    assert q.status == "resolved" and q.outcome == "yes"


def test_annul_refunds_spent(q, anya, boris):
    services.buy(anya, q, "yes", 100)
    services.buy(boris, q, "no", 50)
    services.annul(q)
    anya.refresh_from_db()
    boris.refresh_from_db()
    assert anya.balance == D(1000) and boris.balance == D(1000)
    assert Payout.objects.filter(kind="refund").count() == 2


def test_sell_roundtrip_not_profitable(q, anya):
    t = services.buy(anya, q, "yes", 100)
    s = services.sell(anya, q, "yes", t.shares)
    anya.refresh_from_db()
    assert anya.balance <= D(1000)
    assert D(1000) - anya.balance <= D("0.02")
    assert Position.objects.get(trader=anya, question=q).yes_shares == 0
    with pytest.raises(MarketError):
        services.sell(anya, q, "yes", 1)
    assert s.shares == -t.shares


def test_profit_marks_to_market(q, anya, boris):
    services.buy(anya, q, "yes", 100)
    p1 = services.profit(anya)  # marked at post-trade price > average fill price
    assert p1 > 0
    services.buy(boris, q, "yes", 100)  # price rises -> anya's position gains more
    assert services.profit(anya) > p1
