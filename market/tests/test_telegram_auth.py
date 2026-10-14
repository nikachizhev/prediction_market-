from urllib.parse import urlparse, parse_qs
from datetime import timedelta
import pytest
from django.test import override_settings
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.models import User
from market.models import TelegramAuth, Trader
from market.telegram_auth import confirm
from market.forms import RegistrationForm


def begin(client):
    response = client.post(reverse("market:telegram_start"))
    assert response.status_code == 302
    token = parse_qs(urlparse(client.session["telegram_auth_url"]).query)["start"][0][5:]
    return token


@pytest.mark.django_db
@override_settings(TELEGRAM_BOT_TOKEN="test", TELEGRAM_BOT_USERNAME="test_bot")
def test_telegram_registration_and_repeat_login(client, mailoutbox):
    token = begin(client)
    assert confirm(token, 12345, "Катя")
    assert not confirm(token, 99999, "Other")
    # Knowing a bot link does not grant a different browser a website session.
    other = Client()
    other.post(reverse("market:telegram_complete"), {"email": "other@example.com"})
    assert "_auth_user_id" not in other.session
    response = client.post(reverse("market:telegram_complete"), {"email": "katya@example.com"})
    assert response.status_code == 302
    user = User.objects.get(email="katya@example.com")
    assert user.trader.telegram_chat_id == 12345
    assert user.trader.balance == 1000 and not user.has_usable_password()
    assert not user.is_active and "_auth_user_id" not in client.session
    import re
    token = re.search(r'/email/verify/([^/]+)/', mailoutbox[0].body).group(1)
    client.post(reverse("market:verify_email", args=[token]))
    client.logout()
    token = begin(client)
    assert confirm(token, 12345, "Катя")
    client.post(reverse("market:telegram_complete"))
    assert int(client.session["_auth_user_id"]) == user.pk
    assert User.objects.count() == 1


@pytest.mark.django_db
@override_settings(TELEGRAM_BOT_TOKEN="test", TELEGRAM_BOT_USERNAME="test_bot")
def test_unconfirmed_or_expired_cannot_login(client):
    token = begin(client)
    client.post(reverse("market:telegram_complete"), {"email": "a@example.com"})
    assert not User.objects.exists()
    TelegramAuth.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
    assert not confirm(token, 456, "Name")
    client.post(reverse("market:telegram_complete"), {"email": "a@example.com"})
    assert not User.objects.exists()


@pytest.mark.django_db
def test_email_required_and_duplicates_case_insensitive():
    User.objects.create_user(username="existing", email="person@example.com")
    data = {"username": "new", "password1": "Secure-pass-527!", "password2": "Secure-pass-527!"}
    form = RegistrationForm(data)
    assert not form.is_valid() and "email" in form.errors
    form = RegistrationForm({**data, "email": "PERSON@example.com"})
    assert not form.is_valid() and "email" in form.errors
