from unittest.mock import patch
from urllib.parse import urlparse
from datetime import timedelta
import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from market.telegram import handle_update
from market.models import Trader, BotRegistration, TelegramSiteLink
from market.bot_registration import site_link


def message(text, chat_id=456):
    return {"message": {"chat": {"id": chat_id, "type": "private"}, "from": {"id": chat_id}, "text": text}}


@pytest.mark.django_db
def test_register_in_bot_then_login_and_replay(client, mailoutbox):
    with patch("market.telegram.send") as send:
        handle_update(message("/start"))
        assert "Зарегистрироваться" in str(send.call_args)
        handle_update(message("Зарегистрироваться"))
        handle_update(message("Катя"))
        handle_update(message("invalid"))
        assert not User.objects.exists()
        handle_update(message("Katya@example.com"))
        trader = Trader.objects.get(telegram_chat_id=456)
        assert trader.name == "Катя" and trader.user.email == "katya@example.com"
        assert trader.balance == 1000 and not trader.user.has_usable_password()
        assert not BotRegistration.objects.exists()
        assert not trader.user.is_active
        assert not TelegramSiteLink.objects.exists()
        import re
        token = re.search(r'/email/verify/([^/]+)/', mailoutbox[0].body).group(1)
        client.post(reverse("market:verify_email", args=[token]))
        handle_update(message("/login"))
        url = send.call_args.kwargs["reply_markup"]["inline_keyboard"][0][0]["url"]
        token = urlparse(url).fragment
        assert TelegramSiteLink.objects.get().token_hash != token
        assert client.get(reverse("market:telegram_site_login")).status_code == 200
        assert "_auth_user_id" not in client.session
        response = client.post(reverse("market:telegram_site_login"), {"token": token})
        assert response.status_code == 302
        assert int(client.session["_auth_user_id"]) == trader.user_id
        client.logout()
        client.post(reverse("market:telegram_site_login"), {"token": token})
        assert "_auth_user_id" not in client.session
        handle_update(message("/register"))
        assert User.objects.count() == 1


@pytest.mark.django_db
def test_duplicate_email_and_cancel():
    User.objects.create_user(username="existing", email="a@example.com")
    with patch("market.telegram.send"):
        handle_update(message("/register"))
        handle_update(message("Name"))
        handle_update(message("A@example.com"))
        assert User.objects.count() == 1
        handle_update(message("/cancel"))
        assert not BotRegistration.objects.exists()


@pytest.mark.django_db
def test_expired_and_revoked_login(client):
    user = User.objects.create_user(username="test")
    trader = Trader.objects.create(user=user, name="Name", pseudonym="Name", telegram_chat_id=456)
    first = urlparse(site_link(trader)).fragment
    second = urlparse(site_link(trader)).fragment
    client.post(reverse("market:telegram_site_login"), {"token": first})
    assert "_auth_user_id" not in client.session
    TelegramSiteLink.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
    client.post(reverse("market:telegram_site_login"), {"token": second})
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_telegram_only_account_cannot_unlink():
    user = User.objects.create_user(username="tg-only")
    trader = Trader.objects.create(user=user, name="Name", pseudonym="Name", telegram_chat_id=456)
    with patch("market.telegram.send"):
        handle_update(message("/unlink"))
    trader.refresh_from_db()
    assert trader.telegram_chat_id == 456
