import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from market.models import Question, Trader
from market.services import buy


@pytest.mark.django_db
@pytest.mark.parametrize("kind,value", [("lmsr", "yes"), ("cpmm", "no"), ("ls_lmsr", "yes"), ("number", "42"), ("multiple", ["0", "1"]), ("poll", None)])
def test_admin_can_resolve_every_kind(client, kind, value):
    admin = User.objects.create_superuser(username="admin", password="test-password")
    client.force_login(admin)
    q = Question.objects.create(title="Test", kind=kind, options=["A", "B"])
    response = client.post(reverse("admin:market_question_result", args=[q.pk]), {"value": value} if value is not None else {})
    assert response.status_code == 302
    q.refresh_from_db()
    assert q.status == ("closed" if kind == "poll" else "resolved")


@pytest.mark.django_db
def test_admin_payout_is_not_repeated(client):
    user = User.objects.create_superuser(username="admin", password="test-password")
    client.force_login(user)
    q = Question.objects.create(title="Test")
    trader = Trader.objects.create(name="Player", pseudonym="Player")
    buy(trader, q, "yes", 10)
    url = reverse("admin:market_question_result", args=[q.pk])
    client.post(url, {"value": "yes"})
    client.post(url, {"value": "yes"})
    assert trader.payouts.count() == 1


@pytest.mark.django_db
def test_regular_user_cannot_resolve(client):
    user = User.objects.create_user(username="player", password="test-password")
    client.force_login(user)
    q = Question.objects.create(title="Test")
    assert client.post(reverse("admin:market_question_result", args=[q.pk]), {"value": "yes"}).status_code == 302
    q.refresh_from_db()
    assert q.status == "open"
