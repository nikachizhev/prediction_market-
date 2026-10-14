import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from market.models import Trader, Question
from market import services
from market.mascots import progression
from market.statistics import statistics


@pytest.mark.django_db
def test_choose_change_and_welcome(client):
    user = User.objects.create_user(username="player", password="Test-password-775!")
    client.force_login(user)
    assert client.get(reverse("market:welcome")).url == reverse("market:mascot")
    assert client.get(reverse("market:mascot")).status_code == 200
    client.post(reverse("market:mascot"), {"mascot": "invalid"})
    trader = Trader.objects.get(user=user)
    assert trader.mascot == ""
    client.post(reverse("market:mascot"), {"mascot": "vesta"})
    trader.refresh_from_db()
    before = progression(trader, statistics(trader))
    assert before["character"]["name"] == "Веста"
    client.post(reverse("market:mascot"), {"mascot": "fox"})
    trader.refresh_from_db()
    after = progression(trader, statistics(trader))
    assert after["character"]["name"] == "Фокс" and before["xp"] == after["xp"]
    assert client.get(reverse("market:welcome")).url == reverse("market:markets")
    assert "Фокс" in client.get(reverse("market:personal_rating")).content.decode()


@pytest.mark.django_db
def test_unique_questions_money_bonus_and_annulment():
    trader = Trader.objects.create(name="Player", pseudonym="Player")
    q = Question.objects.create(title="Question")
    services.buy(trader, q, "yes", 100)
    services.buy(trader, q, "yes", 100)
    p = progression(trader, statistics(trader))
    assert p["participations"] == 1 and p["money_xp"] == 14
    assert p["xp"] == 24
    services.resolve(q, "yes")
    assert progression(trader, statistics(trader))["xp"] == 44
    # A large amount cannot unlock a higher tier without correct predictions.
    assert progression(trader, {"correct": 0})["level"] == 1
    other = Question.objects.create(title="Annulled")
    services.buy(trader, other, "yes", 20)
    services.annul(other)
    assert progression(trader, statistics(trader))["participations"] == 1


@pytest.mark.django_db
def test_unlock_requires_experience_and_correct_predictions():
    trader = Trader.objects.create(name="Player", pseudonym="Player")
    for i in range(6):
        q = Question.objects.create(title=str(i))
        services.buy(trader, q, "yes", 1)
        services.resolve(q, "yes" if i < 2 else "no")
    p = progression(trader, statistics(trader))
    assert p["level"] == 2
    assert p["missing_correct"] == 5


@pytest.mark.django_db
def test_floating_companion_visibility_persists_and_is_private(client):
    user = User.objects.create_user(username="owner")
    trader = Trader.objects.create(user=user, name="Owner", pseudonym="Owner", mascot="fox")
    other = Trader.objects.create(name="Other", pseudonym="Other", mascot="vesta")
    client.force_login(user)
    for route in ("market:markets", "market:me", "market:personal_rating", "market:mascot"):
        page = client.get(reverse(route)).content.decode()
        assert 'id="floating-companion"' in page and 'mascot-fox' in page
    assert client.get(reverse("market:mascot_visibility")).status_code == 405
    response = client.post(reverse("market:mascot_visibility"), {"visible": "0", "trader": other.pk})
    assert response.json() == {"visible": False}
    trader.refresh_from_db()
    other.refresh_from_db()
    assert not trader.show_companion and other.show_companion
    page = client.get(reverse("market:markets")).content.decode()
    assert 'id="companion-expanded" hidden' in page
    client.post(reverse("market:mascot_visibility"), {"visible": "1"})
    trader.refresh_from_db()
    assert trader.show_companion
    client.logout()
    assert 'id="floating-companion"' not in client.get(reverse("market:markets")).content.decode()
