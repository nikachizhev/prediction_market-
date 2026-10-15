import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from market.models import Trader


@pytest.mark.django_db
def test_profile_requires_login(client):
    assert client.get(reverse("market:profile")).status_code == 302


@pytest.mark.django_db
def test_profile_updates_only_own_data(client):
    user = User.objects.create_user("owner", "owner@example.com", "password")
    trader = Trader.objects.create(user=user, name="Old", pseudonym="Old")
    other = Trader.objects.create(name="Other", pseudonym="Other")
    client.force_login(user)
    response = client.post(reverse("market:profile"), {
        "name": "New", "pseudonym": "Forecast", "github_login": "octocat",
        "show_real_name": "on", "email": "changed@example.com", "trader_id": other.pk,
    })
    assert response.status_code == 302
    trader.refresh_from_db(); user.refresh_from_db(); other.refresh_from_db()
    assert trader.name == user.first_name == "New"
    assert trader.github_login == "octocat"
    assert user.email == "owner@example.com"
    assert other.name == "Other"
    page = client.get(reverse("market:profile"))
    assert page.status_code == 200
    assert "account-menu" in page.content.decode()


@pytest.mark.django_db
def test_invalid_github_login_is_rejected(client):
    user = User.objects.create_user("owner", password="password")
    client.force_login(user)
    response = client.post(reverse("market:profile"), {
        "name": "Name", "pseudonym": "Alias", "github_login": "https://github.com/octocat",
    })
    assert response.status_code == 200
    assert response.context["form"].errors.get("github_login")
