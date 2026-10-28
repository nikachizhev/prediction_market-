from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch
import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from market.models import Trader, DailyReward
from market.services import claim_daily_reward, profit
from market.statistics import statistics, chart_data
from market.mascots import progression


@pytest.mark.django_db
def test_daily_bonus_is_once_and_excluded_from_profit():
    trader = Trader.objects.create(name="Daily", pseudonym="Daily")
    assert claim_daily_reward(trader)
    assert claim_daily_reward(trader) is None
    trader.refresh_from_db()
    assert trader.balance == 1020
    assert profit(trader) == 0
    assert progression(trader, statistics(trader))["xp"] == 2
    assert DailyReward.objects.count() == 1


@pytest.mark.django_db
def test_day_changes_at_moscow_midnight():
    trader = Trader.objects.create(name="Daily", pseudonym="Daily")
    with patch("django.utils.timezone.now", return_value=datetime(2026, 10, 14, 20, 59, tzinfo=dt_timezone.utc)):
        assert claim_daily_reward(trader)
    with patch("django.utils.timezone.now", return_value=datetime(2026, 10, 14, 21, 0, tzinfo=dt_timezone.utc)):
        assert claim_daily_reward(trader)
        assert claim_daily_reward(trader) is None
    trader.refresh_from_db()
    assert trader.balance == 1040


@pytest.mark.django_db
def test_site_visit_and_charts(client):
    user = User.objects.create_user("daily", password="password")
    trader = Trader.objects.create(user=user, name="Daily", pseudonym="Daily")
    client.get(reverse("market:index"))
    assert not DailyReward.objects.exists()
    client.force_login(user)
    page = client.get(reverse("market:personal_rating"))
    assert page.status_code == 200
    assert "rating-chart-data" in page.content.decode()
    client.get(reverse("market:leaderboard"))
    assert DailyReward.objects.count() == 1
    assert len(chart_data(trader)["activity"]) == 30
