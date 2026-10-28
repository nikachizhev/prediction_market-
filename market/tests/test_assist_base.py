"""Phase 3a groundwork: Me.assist, the helper's daily limit counter, new routes (docs/api.md «Фаза 3a»)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.test import override_settings
from django.utils import timezone

from market import assist
from market.models import Event
from market.tests.test_api import anya, boris, bot_send, bot_token, trader  # noqa: F401 (fixtures)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("bot_token")]

MSK = ZoneInfo("Europe/Moscow")


def log(user_id, result, at=None):
    event = Event.objects.create(trader=trader(user_id), name="assist_request", props={"result": result})
    if at:
        Event.objects.filter(pk=event.pk).update(created_at=at)


def test_tests_never_see_a_real_key():
    from django.conf import settings
    assert settings.LLM_API_KEY == ""


@pytest.mark.parametrize("enabled,key,expected", [(True, "k", True), (True, "", False), (False, "k", False)])
def test_me_assist_enabled_needs_key_and_switch(anya, enabled, key, expected):
    with override_settings(ASSIST_ENABLED=enabled, LLM_API_KEY=key):
        status, me = anya.get("me")
    assert status == 200
    assert me["assist"] == {"enabled": expected, "left": 20}


def test_left_counts_todays_requests_but_not_limit_refusals(anya, boris):
    for result in ("ok", "warning", "error", "limit", "limit"):
        log(1001, result)
    log(1002, "ok")
    assert assist.used_today(trader(1001)) == 3
    assert anya.get("me")[1]["assist"]["left"] == 17


def test_left_resets_at_moscow_midnight(anya):
    now = timezone.now().astimezone(MSK)
    midnight = datetime.combine(now.date(), datetime.min.time(), tzinfo=MSK)
    log(1001, "ok", at=midnight - timedelta(minutes=1))
    log(1001, "ok", at=midnight + timedelta(minutes=1))
    assert assist.left(trader(1001)) == 19


def test_left_never_negative(anya):
    with override_settings(ASSIST_DAILY_LIMIT=2):
        for _ in range(3):
            log(1001, "ok")
        assert assist.left(trader(1001)) == 0


@pytest.mark.parametrize("method,path", [("post", "assist"), ("get", "templates"), ("get", "markets/similar")])
def test_new_routes_need_auth(client, method, path):
    response = getattr(client, method)(f"/api/{path}")
    assert response.status_code == 401


def test_similar_route_does_not_clash_with_market_ids(anya):
    status, _ = anya.get("markets/similar", q="Сдам ли я сессию")
    assert status != 404
