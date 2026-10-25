"""Spec E5: fees, limits, market creation and settlement. No coins may be printed by rounding."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db.models import Sum
from django.utils import timezone

from market import services
from market.models import LedgerEntry, Position, Question, Trader
from market.services import MarketError

D = Decimal


def make(name, balance=1000):
    return Trader.objects.create(name=name, pseudonym=name, balance=D(balance))


def new_market(creator, **kw):
    now = timezone.now()
    args = dict(title="Сдадим матан?", criteria="По ведомости", category="study",
                closes_at=now + timedelta(days=3), resolves_at=now + timedelta(days=4))
    args.update(kw)
    return services.create_market(creator, **args)


def total_coins():
    return Trader.objects.aggregate(t=Sum("balance"))["t"]


@pytest.fixture
def creator(db):
    return make("Автор")


@pytest.fixture
def market(creator):
    return new_market(creator)


def test_create_market_costs_subsidy_and_starts_at_half(creator, market):
    creator.refresh_from_db()
    assert creator.balance == D(900)
    assert market.prob == pytest.approx(0.5) and market.b == 144 and market.subsidy == D(100)
    assert market.creator == creator and market.category == "study"
    entry = creator.ledger.get()
    assert entry.kind == LedgerEntry.Kind.CREATION and entry.amount == D(-100) and entry.question == market


@pytest.mark.parametrize("kw, message", [
    (dict(title=""), "Напишите вопрос"),
    (dict(title="я" * 121), "120"),
    (dict(criteria=" "), "как определяем"),
    (dict(category="politics"), "категорию"),
    (dict(closes_at=timezone.now() + timedelta(minutes=30)), "час"),
    (dict(closes_at=timezone.now() + timedelta(days=366), resolves_at=timezone.now() + timedelta(days=367)), "года"),
    (dict(resolves_at=timezone.now() + timedelta(days=1)), "раньше"),
])
def test_create_market_validation(creator, kw, message):
    with pytest.raises(MarketError, match=message):
        new_market(creator, **kw)
    creator.refresh_from_db()
    assert creator.balance == D(1000) and not Question.objects.exists()


def test_create_market_daily_limit_and_balance(creator):
    for _ in range(3):
        new_market(creator)
    with pytest.raises(MarketError, match="3"):
        new_market(creator)
    poor = make("Бедный", 99)
    with pytest.raises(MarketError, match="100"):
        new_market(poor)


def test_creator_cannot_trade_own_market(creator, market):
    with pytest.raises(MarketError, match="Создатель"):
        services.buy(creator, market, "yes", 10)


def test_buy_withholds_fee(market):
    anya = make("Аня")
    trade = services.buy(anya, market, "yes", 100)
    assert trade.fee == D(2) and D(99) <= trade.cost <= D(100)
    market.refresh_from_db()
    anya.refresh_from_db()
    assert market.fee_pool == D(2) and anya.balance == D(1000) - trade.cost
    # b = 144: 98 coins move YES from 50% to about 75% (spec E5).
    assert market.prob == pytest.approx(0.75, abs=0.01)
    entry = anya.ledger.get()
    assert entry.kind == LedgerEntry.Kind.BUY and entry.amount == -trade.cost and entry.balance_after == anya.balance


def test_quote_matches_buy(market):
    anya = make("Аня")
    q = services.quote(market, "no", D(50))
    trade = services.buy(anya, market, "no", 50)
    assert (q["cost"], q["fee"], q["shares"]) == (trade.cost, trade.fee, trade.shares)
    assert q["payout_if_win"] == services._floor_cent(trade.shares)


def test_sell_withholds_fee(market):
    anya = make("Аня")
    bought = services.buy(anya, market, "yes", 100)
    sold = services.sell(anya, market, "yes", bought.shares)
    assert sold.fee > 0 and sold.cost < 0
    market.refresh_from_db()
    assert market.fee_pool == bought.fee + sold.fee
    assert anya.ledger.filter(kind=LedgerEntry.Kind.SELL).get().amount == -sold.cost


def test_trade_limit_is_share_of_balance_capped(market):
    anya = make("Аня", 400)
    with pytest.raises(MarketError, match="Максимум за одну сделку — 80"):
        services.buy(anya, market, "yes", D("80.01"))
    services.buy(anya, market, "yes", 80)
    rich = make("Богач", 5000)
    with pytest.raises(MarketError, match="200"):
        services.buy(rich, market, "yes", D("200.01"))
    assert services.trade_limit(rich) == D(200)


def test_market_cap_500(market):
    rich = make("Богач", 10000)
    for _ in range(2):
        services.buy(rich, market, "yes", 200)
    spent = Position.objects.get(trader=rich, question=market).spent
    assert services.max_buy_amount(rich, market) == D(500) - spent
    with pytest.raises(MarketError, match="500"):
        services.buy(rich, market, "no", D(500) - spent + D("0.01"))
    services.buy(rich, market, "no", D(500) - spent)


def test_hidden_market_blocks_trading(market):
    Question.objects.filter(pk=market.pk).update(hidden_at=timezone.now())
    market.refresh_from_db()
    with pytest.raises(MarketError, match="скрыт"):
        services.buy(make("Аня"), market, "yes", 10)


def test_resolution_pays_winners_creator_fee_and_returns_unused_subsidy(creator, market):
    anya, boris = make("Аня"), make("Борис")
    before = total_coins() + market.subsidy
    a = services.buy(anya, market, "yes", 150)
    b = services.buy(boris, market, "no", 100)
    services.buy(boris, market, "no", 50)
    market.refresh_from_db()
    fees = market.fee_pool
    services.resolve(market, "yes")
    anya.refresh_from_db(); boris.refresh_from_db(); creator.refresh_from_db()
    assert anya.balance == D(1000) - a.cost + services._floor_cent(a.shares)
    creator_fee = services._floor_cent(fees / 2)
    kinds = dict(creator.ledger.exclude(kind=LedgerEntry.Kind.CREATION).values_list("kind", "amount"))
    assert kinds[LedgerEntry.Kind.CREATOR_FEE] == creator_fee
    assert D(0) <= kinds.get(LedgerEntry.Kind.SUBSIDY_RETURN, D(0)) <= D(100)
    # Coins only burn: half the fee at least, nothing is printed.
    after = total_coins()
    assert after <= before - (fees - creator_fee)


@pytest.mark.parametrize("seq", [
    [("yes", 200), ("yes", 200), ("no", 30)],
    [("no", 13.37), ("yes", 0.5), ("no", 199.99)],
    [("yes", 1)],
    [],
])
@pytest.mark.parametrize("outcome", ["yes", "no"])
def test_settlement_never_prints_coins(creator, seq, outcome):
    market = new_market(creator)
    traders = [make(f"T{i}", 3000) for i in range(len(seq))]
    before = total_coins() + market.subsidy
    for trader, (side, amount) in zip(traders, seq):
        services.buy(trader, market, side, D(str(amount)))
    if traders:
        pos = Position.objects.get(trader=traders[0], question=market)
        services.sell(traders[0], market, seq[0][0], (pos.yes_shares or pos.no_shares) / 3)
    services.resolve(market, outcome)
    assert total_coins() <= before


def test_annul_refunds_everything_including_fees(creator, market):
    anya = make("Аня")
    services.buy(anya, market, "yes", 100)
    services.annul(market)
    anya.refresh_from_db(); creator.refresh_from_db()
    assert anya.balance == D(1000) and creator.balance == D(1000)
    assert anya.ledger.filter(kind=LedgerEntry.Kind.REFUND).get().amount == D(100)


def test_annul_without_subsidy_refund_when_creator_at_fault(creator, market):
    services.annul(market, refund_subsidy=False)
    creator.refresh_from_db()
    assert creator.balance == D(900)


def test_daily_bonus_is_20_and_logged(db):
    anya = make("Аня")
    assert services.claim_daily_reward(anya).coins == D(20)
    assert services.claim_daily_reward(anya) is None
    anya.refresh_from_db()
    assert anya.balance == D(1020) and anya.ledger.get().kind == LedgerEntry.Kind.DAILY
    assert services.profit(anya) == 0


def test_starter_pack_once(db):
    anya = make("Аня", 0)
    assert services.grant_starter_pack(anya)
    assert services.grant_starter_pack(anya) is None
    anya.refresh_from_db()
    assert anya.balance == D(1000) and services.profit(anya) == 0


def test_insurance_weekly_when_broke_without_positions(market):
    anya = make("Аня", 49)
    services.buy(anya, market, "yes", 9)
    assert services.claim_insurance(anya) is None  # has an open position
    pos = Position.objects.get(trader=anya, question=market)
    services.sell(anya, market, "yes", pos.yes_shares)
    entry = services.claim_insurance(anya)
    assert entry and entry.amount == D(200)
    anya.refresh_from_db()
    anya.balance = D(10)
    anya.save()
    assert services.claim_insurance(anya) is None  # once a week
    LedgerEntry.objects.filter(pk=entry.pk).update(created_at=timezone.now() - timedelta(days=8))
    assert services.claim_insurance(anya)
    assert services.claim_insurance(make("Богач", 50)) is None


def test_profit_excludes_bonuses_and_keeps_locked_subsidy(creator, market):
    assert services.profit(creator) == 0
    services.claim_daily_reward(creator)
    assert services.profit(creator) == 0
