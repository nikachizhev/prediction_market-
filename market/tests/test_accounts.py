import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from market.models import Trader


@pytest.mark.django_db
def test_registration_creates_profile_and_session(client, mailoutbox):
    response = client.post(reverse("market:register"), {
        "username": "newtrader", "first_name": "Катя",
        "email": "katya@example.com",
        "password1": "A-strong-pass-864!", "password2": "A-strong-pass-864!",
    })
    assert response.status_code == 302
    user = User.objects.get(username="newtrader")
    assert user.check_password("A-strong-pass-864!")
    assert user.trader.balance == 1000
    assert user.email == "katya@example.com"
    assert not user.is_active and "_auth_user_id" not in client.session
    assert len(mailoutbox) == 1
    client.post(reverse("market:login"), {"username": "newtrader", "password": "A-strong-pass-864!"})
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_session_cannot_impersonate_trader(client):
    trader = Trader.objects.create(name="Other", pseudonym="Other")
    session = client.session
    session["trader_id"] = trader.pk
    session.save()
    assert client.get(reverse("market:me")).status_code == 302
    client.post(reverse("market:switch_user"), {"trader_id": trader.pk})
    assert "_auth_user_id" not in client.session
