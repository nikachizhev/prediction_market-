"""With LEGACY_SITE_ENABLED off only the product is served: Mini App, API, admin, media, metrics."""
import importlib

import pytest
from django.test import override_settings

closed = override_settings(ROOT_URLCONF="market.tests.urls_legacy_off")

LEGACY_PATHS = [
    "/register/", "/login/", "/logout/", "/password/reset/", "/email/pending/", "/email/verify/token/",
    "/auth/telegram/link/", "/auth/telegram/start/", "/me/", "/me/telegram/", "/profile/",
    "/markets/", "/markets/create/", "/api/markets/", "/q/1/", "/q/1/quote", "/q/1/buy/", "/q/1/sell/",
    "/leaderboard/", "/rating/", "/groups/", "/friends/", "/notifications/", "/switch-user/",
    "/abmock/", "/sources/github/", "/sources/sync/",
]


@closed
@pytest.mark.django_db
@pytest.mark.parametrize("path", LEGACY_PATHS)
def test_old_routes_are_gone(client, path):
    assert client.get(path).status_code == 404
    assert client.post(path, {"username": "x", "email": "x@example.com"}).status_code == 404


@closed
@pytest.mark.django_db
def test_registration_by_email_creates_nobody(client):
    from django.contrib.auth.models import User
    from market.models import Trader

    client.post("/register/", {"username": "farm", "first_name": "Farm", "email": "farm@example.com",
                               "password1": "Long-enough-pass-1", "password2": "Long-enough-pass-1"})
    assert not User.objects.exists() and not Trader.objects.exists()


@closed
@pytest.mark.django_db
def test_product_routes_still_work(client, tmp_path):
    (tmp_path / "index.html").write_text("<div id=root></div>")
    with override_settings(MINIAPP_DIST=tmp_path):
        assert client.get("/app/").status_code == 200
        assert client.get("/app/market/5").status_code == 200
    assert client.post("/api/session").status_code == 401  # reached the API: no signed initData
    assert client.get("/api/markets").status_code == 401
    assert client.get("/admin/login/").status_code == 200
    from django.urls import resolve
    assert resolve("/metrics").url_name == "metrics"  # the view itself answers 404 to outsiders


@closed
def test_root_leads_to_the_bot(client):
    with override_settings(TELEGRAM_BOT_USERNAME="oracle_bot"):
        response = client.get("/")
        assert response.status_code == 302 and response["Location"] == "https://t.me/oracle_bot"
    with override_settings(TELEGRAM_BOT_USERNAME=""):
        assert client.get("/")["Location"] == "/app/"


def _reload_settings(monkeypatch, **env):
    from config import settings as module

    for name in ("DJANGO_DEBUG", "LEGACY_SITE_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return importlib.reload(module).LEGACY_SITE_ENABLED


def test_flag_follows_debug_unless_set(monkeypatch):
    from config import settings as module

    try:
        assert _reload_settings(monkeypatch, DJANGO_DEBUG="0") is False
        assert _reload_settings(monkeypatch, DJANGO_DEBUG="1") is True
        assert _reload_settings(monkeypatch, DJANGO_DEBUG="0", LEGACY_SITE_ENABLED="1") is True
        assert _reload_settings(monkeypatch, DJANGO_DEBUG="1", LEGACY_SITE_ENABLED="0") is False
    finally:
        monkeypatch.undo()
        importlib.reload(module)
