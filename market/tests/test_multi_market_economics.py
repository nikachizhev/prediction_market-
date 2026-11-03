"""Economics of multi-option markets: no minted coins, no trading on the root, one cap per market."""
import math
import random
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from market import services
from market.api import serializers
from market.models import Payout, Question, Trade, Trader

D = Decimal

pytestmark = pytest.mark.django_db


def make_trader(name: str, balance: int = 1000) -> Trader:
    return Trader.objects.create(name=name, pseudonym=name, balance=D(balance))


def make_multi(creator: Trader, options: list[str]) -> tuple[Question, list[Question]]:
    now = timezone.now()
    root = services.create_market(
        creator, title="Кто выиграет хакатон?", criteria="По протоколу жюри", category="study",
        closes_at=now + timedelta(hours=4), resolves_at=now + timedelta(hours=5), options=options,
    )
    return root, list(root.submarkets.order_by("id"))


def total_coins() -> Decimal:
    return Trader.objects.aggregate(total=Sum("balance"))["total"]


def finish(root: Question, subs: list[Question], outcomes: dict[int, str]) -> None:
    root = Question.objects.get(pk=root.pk)
    subs = [Question.objects.get(pk=s.pk) for s in subs]
    services.close_betting(root)
    services._resolve_family(root, subs, outcomes)


@pytest.mark.parametrize("n", [2, 5, 10])
def test_option_depth_fits_the_subsidy(n):
    creator = make_trader("Автор")
    root, subs = make_multi(creator, [f"Вариант {i}" for i in range(n)])
    assert len(subs) == n
    worst = sum(s.b * math.log(2) for s in subs)
    assert worst <= float(root.subsidy) == settings.MARKET_CREATION_COST
    assert all(s.subsidy == 0 for s in subs)


@pytest.mark.parametrize("n", [2, 5, 10])
def test_informed_traders_cannot_mint_coins(n):
    """Traders who know every option's outcome buy the right side until the limits stop them."""
    rng = random.Random(n)
    creator = make_trader("Автор")
    traders = [make_trader(f"Знаток {i}") for i in range(40)]
    before = total_coins()
    root, subs = make_multi(creator, [f"Вариант {i}" for i in range(n)])
    # Several options may resolve ДА; make sure both outcomes occur.
    outcomes = {s.pk: ("yes" if i % 2 == 0 else "no") for i, s in enumerate(subs)}
    for _ in range(4):
        for trader in traders:
            for sub in rng.sample(subs, len(subs)):
                amount = min(services.max_buy_amount(trader, sub), D(60))
                if amount < 1:
                    continue
                try:
                    services.buy(trader, sub, outcomes[sub.pk], amount.quantize(D("0.01")))
                except services.MarketError:
                    continue
    # Prices really went to the known outcomes: the market maker lost close to its worst case.
    for sub in subs:
        sub.refresh_from_db()
        assert (sub.prob > 0.9) if outcomes[sub.pk] == "yes" else (sub.prob < 0.1)
    finish(root, subs, outcomes)
    family = [root.pk, *(s.pk for s in subs)]
    trades = Trade.objects.filter(question_id__in=family).aggregate(cost=Sum("cost"), fee=Sum("fee"))
    paid = Payout.objects.filter(question_id__in=family).aggregate(total=Sum("amount"))["total"]
    # The market maker stays solvent on its own money: subsidy plus net stakes (fees aside) cover every payout.
    assert root.subsidy + trades["cost"] - trades["fee"] - paid >= 0
    after = total_coins()
    # Nothing is minted: the subsidy covers the payouts, half the fees burn.
    assert after <= before
    root.refresh_from_db()
    assert root.status == Question.Status.RESOLVED


def test_root_trading_is_blocked():
    creator = make_trader("Автор")
    anya = make_trader("Аня")
    root, subs = make_multi(creator, ["А", "Б", "В"])
    for call in (
        lambda: services.buy(anya, root, "yes", 10),
        lambda: services.buy(anya, root, "yes", 10, request_key=uuid.uuid4()),
        lambda: services.sell(anya, root, "yes", 1),
        lambda: services.quote(root, "yes", 10),
        lambda: services.prepare_purchase(anya, root, "yes", 10),
    ):
        with pytest.raises(services.MarketError, match="на конкретный вариант"):
            call()
    assert serializers.trade_block(root, anya) == services.OPTION_ROOT_BLOCK
    # The creator still sees why they cannot trade at all.
    assert serializers.trade_block(root, creator).startswith("Создатель рынка")
    assert serializers.trade_block(subs[0], anya) is None
    root.refresh_from_db()
    assert root.q_yes == root.q_no == 0.0 and root.fee_pool == 0
    # Options still trade.
    services.buy(anya, subs[0], "yes", 10)


def test_family_cap_counts_all_options_together():
    creator = make_trader("Автор")
    rich = make_trader("Богач", balance=10_000)
    root, subs = make_multi(creator, ["А", "Б", "В"])
    services.buy(rich, subs[0], "yes", 200)
    services.buy(rich, subs[1], "no", 200)
    assert services.invested(rich, root) == services.invested(rich, subs[2]) == D(400)
    assert services.max_buy_amount(rich, subs[2]) == D(100)
    with pytest.raises(services.MarketError, match="на все варианты вместе.*Осталось 100"):
        services.buy(rich, subs[2], "yes", D("100.01"))
    with pytest.raises(services.MarketError, match="превышена максимальная ставка"):
        services.prepare_purchase(rich, subs[2], "yes", D("100.01"))
    services.buy(rich, subs[2], "yes", 100)
    assert services.max_buy_amount(rich, subs[0]) == 0
    assert serializers.trade_block(subs[0], rich) == (
        f"В один рынок (на все варианты вместе) можно вложить не больше {settings.MARKET_CAP}.")
    # Another market is not affected.
    _, others = make_multi(creator, ["Да", "Нет"])
    assert services.max_buy_amount(rich, others[0]) == D(settings.MAX_TRADE)


def test_binary_market_keeps_its_depth_and_cap():
    creator = make_trader("Автор")
    now = timezone.now()
    q = services.create_market(creator, title="Сдадим проект?", criteria="По ведомости", category="study",
                               closes_at=now + timedelta(hours=4), resolves_at=now + timedelta(hours=5))
    assert q.b == settings.LMSR_DEFAULT_B
    assert not services.is_option_root(q)
    anya = make_trader("Аня", balance=10_000)
    services.buy(anya, q, "yes", 200)
    services.buy(anya, q, "yes", 200)
    with pytest.raises(services.MarketError, match=r"^В один рынок можно вложить не больше 500\. Осталось 100"):
        services.buy(anya, q, "yes", 101)
