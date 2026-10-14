import re
from datetime import timedelta
from unittest.mock import patch
from smtplib import SMTPException
import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from market.models import EmailVerification
from market.email_verification import send_verification, mark_pending


@pytest.mark.django_db
def test_confirmation_preserves_same_origin_and_requires_csrf(mailoutbox):
    user = User.objects.create_user(username="csrf_pending", email="csrf@example.com", is_active=False)
    mark_pending(user)
    assert send_verification(user)
    token = re.search(r'/email/verify/([^/]+)/', mailoutbox[0].body).group(1)
    url = reverse("market:verify_email", args=[token])
    browser = Client(enforce_csrf_checks=True)
    page = browser.get(url)
    assert page["Referrer-Policy"] == "same-origin"
    assert browser.post(url, HTTP_ORIGIN="http://testserver").status_code == 403
    csrf = browser.cookies["csrftoken"].value
    assert browser.post(url, {"csrfmiddlewaretoken": csrf}, HTTP_ORIGIN="null").status_code == 403
    user.refresh_from_db()
    assert not user.is_active
    response = browser.post(url, {"csrfmiddlewaretoken": csrf}, HTTP_ORIGIN="http://testserver")
    assert response.status_code == 302
    user.refresh_from_db()
    assert user.is_active


@pytest.mark.django_db
def test_confirmation_is_single_use_and_get_does_not_activate(client, mailoutbox):
    user = User.objects.create_user(username="pending", email="test@example.com", password="pass", is_active=False)
    mark_pending(user)
    assert send_verification(user)
    assert mailoutbox[0].to == [user.email]
    token = re.search(r'/email/verify/([^/]+)/', mailoutbox[0].body).group(1)
    assert EmailVerification.objects.get().token_hash != token
    url = reverse("market:verify_email", args=[token])
    assert client.get(url).status_code == 200
    user.refresh_from_db()
    assert not user.is_active
    client.post(url)
    user.refresh_from_db()
    assert user.is_active
    assert EmailVerification.objects.get().verified_at
    # A replay cannot reactivate an account later disabled by an administrator.
    user.is_active = False
    user.save()
    client.post(url)
    user.refresh_from_db()
    assert not user.is_active


@pytest.mark.django_db
def test_expiry_resend_and_changed_address(client, mailoutbox):
    user = User.objects.create_user(username="pending", email="test@example.com", is_active=False)
    mark_pending(user)
    send_verification(user)
    first = re.search(r'/email/verify/([^/]+)/', mailoutbox[0].body).group(1)
    send_verification(user)
    assert len(mailoutbox) == 1
    EmailVerification.objects.update(sent_at=timezone.now()-timedelta(minutes=2), expires_at=timezone.now()-timedelta(seconds=1))
    client.post(reverse("market:verify_email", args=[first]))
    user.refresh_from_db()
    assert not user.is_active
    send_verification(user)
    assert len(mailoutbox) == 2
    second = re.search(r'/email/verify/([^/]+)/', mailoutbox[-1].body).group(1)
    assert first != second
    user.email = "changed@example.com"
    user.save()
    client.post(reverse("market:verify_email", args=[second]))
    user.refresh_from_db()
    assert not user.is_active


@pytest.mark.django_db
def test_delivery_failure_can_be_retried():
    user = User.objects.create_user(username="pending", email="test@example.com", is_active=False)
    mark_pending(user)
    with patch("market.email_verification.send_mail", side_effect=SMTPException("failure")):
        assert not send_verification(user)
    assert EmailVerification.objects.get().sent_at is None
    assert send_verification(user)


@pytest.mark.django_db
def test_disabled_existing_account_cannot_be_reactivated(mailoutbox):
    user = User.objects.create_user(username="disabled", email="disabled@example.com", is_active=False)
    assert not send_verification(user)
    assert not mailoutbox
