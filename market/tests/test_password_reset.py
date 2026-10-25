import re
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.test import Client, override_settings
from django.urls import reverse


@pytest.mark.django_db
@override_settings(SITE_URL="https://forecast.example")
def test_password_reset_flow_and_replay(client, mailoutbox):
    user = User.objects.create_user("reset_user", "reset@example.com", "OldPassword-8492!")
    response = client.post(reverse("market:password_reset"), {"email": user.email})
    assert response.url == reverse("market:password_reset_done")
    assert len(mailoutbox) == 1
    link = re.search(r"https://forecast.example(/password/reset/\S+)", mailoutbox[0].body).group(1)
    response = client.get(link)
    assert response.status_code == 302
    form_url = response.url
    assert client.get(form_url).context["validlink"]
    response = client.post(form_url, {
        "new_password1": "NewPassword-7264!",
        "new_password2": "NewPassword-7264!",
    })
    assert response.url == reverse("market:password_reset_complete")
    user.refresh_from_db()
    assert user.check_password("NewPassword-7264!")
    assert not user.check_password("OldPassword-8492!")
    assert not Client().get(link).context["validlink"]


@pytest.mark.django_db
def test_unknown_inactive_and_passwordless_accounts_do_not_get_mail(client, mailoutbox):
    User.objects.create_user("inactive", "inactive@example.com", "OldPassword-8492!", is_active=False)
    User.objects.create_user("telegram", "telegram@example.com", password=None)
    for email in ["unknown@example.com", "inactive@example.com", "telegram@example.com"]:
        response = client.post(reverse("market:password_reset"), {"email": email})
        assert response.url == reverse("market:password_reset_done")
    assert not mailoutbox


@pytest.mark.django_db
def test_reset_link_expires(client, mailoutbox):
    user = User.objects.create_user("expired", "expired@example.com", "OldPassword-8492!")
    client.post(reverse("market:password_reset"), {"email": user.email})
    link = re.search(r"https?://[^/]+(/password/reset/\S+)", mailoutbox[0].body).group(1)
    with patch.object(default_token_generator, "_now", return_value=default_token_generator._now() + timedelta(hours=2)):
        assert not client.get(link).context["validlink"]


@pytest.mark.django_db
def test_login_links_and_reset_csrf(client):
    page = client.get(reverse("market:login")).content.decode()
    assert "Не помню пароль" in page
    assert reverse("market:email_pending") not in page
    assert Client(enforce_csrf_checks=True).post(
        reverse("market:password_reset"), {"email": "reset@example.com"}
    ).status_code == 403
