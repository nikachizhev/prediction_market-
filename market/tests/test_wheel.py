"""«Колесо дня» (spec E12.2): one free spin a day after a prediction; the prize is a bonus."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal as D
from random import Random
from threading import Barrier

import pytest
from django.db import close_old_connections, connection
from django.test import override_settings
from django.utils import timezone

from market import rating, services
from market.models import LedgerEntry, Trader, WheelSpin
from market.services import MarketError
from market.tests.test_bot_resolution_dialog import make, people  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def wheel_on(settings):
    # The wheel is off by default (10.11.2026); these tests are about how it works when on.
    settings.WHEEL_ENABLED = True


@pytest.fixture
def market(people):
    creator = people[0]
    now = timezone.now()
    return services.create_market(creator, title="Выйдет ли патч до конца недели?", criteria="ДА, если выйдет официально",
                                  category="tech", closes_at=now + timedelta(days=2), resolves_at=now + timedelta(days=3))


def balance(trader):
    return Trader.objects.get(pk=trader.pk).balance


def test_locked_until_a_prediction_of_ten_coins(people, market):
    anya = people[1]
    assert services.wheel_state(anya) == {"spun": None, "unlocked": False}
    with pytest.raises(MarketError, match="после прогноза"):
        services.spin_wheel(anya)
    services.buy(anya, market, "yes", D("9.99"))
    assert not services.wheel_state(anya)["unlocked"]
    services.buy(anya, market, "yes", D(10))
    assert services.wheel_state(anya)["unlocked"]


def test_yesterdays_prediction_does_not_count(people, market):
    from market.models import Trade
    anya = people[1]
    services.buy(anya, market, "yes", D(20))
    Trade.objects.filter(trader=anya).update(created_at=timezone.now() - timedelta(days=1, hours=1))
    assert not services.wheel_state(anya)["unlocked"]


def test_one_spin_a_day_credits_once(people, market):
    anya = people[1]
    services.buy(anya, market, "yes", D(20))
    before = balance(anya)
    spin = services.spin_wheel(anya, rng=Random(1))
    again = services.spin_wheel(anya, rng=Random(2))
    assert again.pk == spin.pk and balance(anya) == before + spin.prize
    assert list(LedgerEntry.objects.filter(trader=anya, kind=LedgerEntry.Kind.WHEEL).values_list("amount", flat=True)) == [spin.prize]
    assert services.wheel_state(anya) == {"spun": spin.prize, "unlocked": True}


def test_prize_follows_the_table(people):
    rng = Random(7)
    counts = Counter(services._pick_prize(rng) for _ in range(20000))
    assert set(counts) == {D(5), D(10), D(25), D(50), D(100)}
    assert 0.57 < counts[D(5)] / 20000 < 0.63
    assert 0.22 < counts[D(10)] / 20000 < 0.28
    assert counts[D(100)] < counts[D(50)] < counts[D(25)]
    average = sum(prize * n for prize, n in counts.items()) / 20000
    assert D("10") < average < D("12")


@override_settings(WHEEL_PRIZES=((7, 1),))
def test_prizes_come_from_settings(people, market):
    anya = people[1]
    services.buy(anya, market, "yes", D(20))
    assert services.spin_wheel(anya).prize == 7


def test_prize_is_a_bonus_not_profit(people, market):
    anya = people[1]
    services.buy(anya, market, "yes", D(20))
    profit_before = services.profit(anya)
    services.spin_wheel(anya)
    assert services.profit(anya) == profit_before
    assert LedgerEntry.Kind.WHEEL in LedgerEntry.BONUS_KINDS
    assert rating.realized_profit(anya, None) == 0


def test_new_day_new_spin(people, market):
    anya = people[1]
    services.buy(anya, market, "yes", D(20))
    first = services.spin_wheel(anya)
    WheelSpin.objects.filter(pk=first.pk).update(day=first.day - timedelta(days=1))
    assert services.spin_wheel(anya).pk != first.pk
    assert WheelSpin.objects.filter(trader=anya).count() == 2


# --- API ---------------------------------------------------------------------------------------------

@pytest.fixture
def api(client, settings):
    from market.tests.test_api import Api
    from market.tests.test_miniapp_auth import TOKEN
    settings.TELEGRAM_BOT_TOKEN = TOKEN
    person = Api(client, 93001)
    person.post("session")
    return person


def test_no_way_in_through_the_api_even_when_switched_on(api, client, people, market):
    # 10.11.2026: the wheel is out of the product, the API has no routes for it at all.
    trader = Trader.objects.get(telegram_chat_id=93001)
    services.buy(trader, market, "no", D(15))
    before = balance(trader)
    assert client.get("/api/wheel").status_code == 404
    assert client.post("/api/wheel/spin").status_code == 404
    assert balance(trader) == before and not WheelSpin.objects.exists()


def test_off_by_default_the_service_credits_nothing(people, market, settings):
    settings.WHEEL_ENABLED = False
    anya = people[1]
    services.buy(anya, market, "yes", D(20))
    before = balance(anya)
    with pytest.raises(MarketError):
        services.spin_wheel(anya)
    assert balance(anya) == before and not WheelSpin.objects.exists()


# --- PostgreSQL --------------------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_two_spins_at_once_credit_once():
    if connection.vendor != "postgresql":
        pytest.skip("needs real row locks")
    author = Trader.objects.create(name="Автор", pseudonym="a1", balance=D(1000))
    trader = Trader.objects.create(name="Игрок", pseudonym="p1", balance=D(1000))
    now = timezone.now()
    q = services.create_market(author, title="Выйдет ли патч до конца недели?", criteria="ДА, если выйдет официально",
                               category="tech", closes_at=now + timedelta(days=2), resolves_at=now + timedelta(days=3))
    services.buy(trader, q, "yes", D(20))
    start = Trader.objects.get(pk=trader.pk).balance
    barrier = Barrier(2)

    def spin():
        close_old_connections()
        barrier.wait()
        try:
            return services.spin_wheel(trader).pk
        finally:
            connection.close()

    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(lambda _: spin(), range(2)))
    assert ids[0] == ids[1]
    prize = WheelSpin.objects.get().prize
    assert Trader.objects.get(pk=trader.pk).balance == start + prize
    assert LedgerEntry.objects.filter(kind=LedgerEntry.Kind.WHEEL).count() == 1
