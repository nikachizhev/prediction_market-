"""Spec E12.1: the daily bonus grows with the run of consecutive Moscow days; a missed day starts it over."""
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from market import services
from market.models import DailyReward, LedgerEntry, Trader
from market.tests.test_api import Api, bot_token, trader  # noqa: F401 - fixture

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("bot_token")]

NOON_UTC = datetime(2026, 11, 10, 9, 0, tzinfo=dt_timezone.utc)  # 12:00 Moscow


def at(day_offset: int):
    return patch("django.utils.timezone.now", return_value=NOON_UTC + timedelta(days=day_offset))


def person():
    return Trader.objects.create(name="Аня", pseudonym="Аня")


def test_bonus_grows_by_the_table_and_stays_at_the_top():
    anya = person()
    expected = [20, 25, 30, 35, 40, 45, 50, 50, 50]
    for offset, coins in enumerate(expected):
        with at(offset):
            reward = services.claim_daily_reward(anya)
            assert reward and reward.coins == coins
            assert services.claim_daily_reward(anya) is None
            assert services.daily_streak_state(anya) == {"days": offset + 1, "tomorrow": Decimal(min(50, coins + 5))}
    anya.refresh_from_db()
    assert anya.balance == 1000 + sum(expected)
    assert services.profit(anya) == 0  # bonus coins never count as profit
    assert anya.ledger.filter(kind=LedgerEntry.Kind.DAILY).count() == len(expected)


def test_missed_day_starts_the_run_over():
    anya = person()
    with at(0):
        assert services.claim_daily_reward(anya).coins == 20
    with at(1):
        assert services.claim_daily_reward(anya).coins == 25
    with at(3):  # day 2 missed
        assert services.claim_daily_reward(anya).coins == 20
        assert services.daily_streak_state(anya) == {"days": 1, "tomorrow": Decimal(25)}
    with at(4):
        assert services.claim_daily_reward(anya).coins == 25


def test_starter_pack_day_is_the_first_day_of_the_run():
    anya = person()
    with at(0):
        services.skip_daily_reward_today(anya)
        assert services.claim_daily_reward(anya) is None
        assert services.daily_streak_state(anya) == {"days": 1, "tomorrow": Decimal(25)}
    with at(1):
        assert services.claim_daily_reward(anya).coins == 25
        assert services.daily_streak_state(anya) == {"days": 2, "tomorrow": Decimal(30)}


def test_state_before_todays_claim_shows_the_live_run():
    anya = person()
    with at(0):
        services.claim_daily_reward(anya)
    with at(1):
        services.claim_daily_reward(anya)
    with at(2):
        # Not claimed yet today: the run of two days is still alive, the next bonus is the third day's.
        assert services.daily_streak_state(anya) == {"days": 2, "tomorrow": Decimal(30)}
    with at(3):
        # Yesterday missed: nothing to continue.
        assert services.daily_streak_state(anya) == {"days": 0, "tomorrow": Decimal(20)}


def test_bonus_table_is_read_from_settings(settings):
    settings.DAILY_BONUS_STREAK = (5, 7)
    assert services.daily_bonus_for(1) == 5
    assert services.daily_bonus_for(2) == services.daily_bonus_for(9) == 7
    assert services.daily_bonus_for(0) == 5


def test_session_reports_the_run_and_tomorrow(client):
    anya = Api(client, 1001, first_name="Аня")
    with at(0):
        _, data = anya.post("session")
        assert data["me"]["streak"] == {"days": 1, "tomorrow": "25.00"}  # the starter day
        assert [g["kind"] for g in data["granted"]] == ["starter"]
    with at(1):
        _, data = anya.post("session")
        assert data["granted"] == [{"kind": "daily", "amount": "25.00", "streak": 2, "tomorrow": "30.00"}]
        assert data["me"]["streak"] == {"days": 2, "tomorrow": "30.00"}
        _, me = anya.get("me")
        assert me["streak"] == {"days": 2, "tomorrow": "30.00"}
    assert DailyReward.objects.filter(trader=trader(1001)).count() == 2
