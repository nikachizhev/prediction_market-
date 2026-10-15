import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from market.models import Trader, Friendship, MarketInvitation, Question


@pytest.fixture
def people(db):
    return [Trader.objects.create(user=User.objects.create_user(f"friend{i}", password="pass"), name=f"Name{i}", pseudonym=f"Alias{i}") for i in range(3)]


@pytest.mark.django_db
def test_mutual_friendship_permissions(client, people):
    first, second, outsider = people
    url = reverse("market:friends")
    client.force_login(first.user)
    client.post(url, {"action": "request", "username": second.user.username})
    pair = Friendship.objects.get()
    client.post(url, {"action": "accept", "pair_id": pair.pk})
    pair.refresh_from_db()
    assert not pair.accepted
    client.force_login(outsider.user)
    assert client.post(url, {"action": "accept", "pair_id": pair.pk}).status_code == 404
    client.force_login(second.user)
    client.post(url, {"action": "accept", "pair_id": pair.pk})
    pair.refresh_from_db()
    assert pair.accepted
    assert client.get(url).status_code == 200
    client.post(url, {"action": "remove", "pair_id": pair.pk})
    assert not Friendship.objects.exists()


@pytest.mark.django_db
def test_invitation_requires_friend_and_is_unique(client, people):
    first, second, outsider = people
    question = Question.objects.create(title="Try this poll", kind="poll")
    url = reverse("market:invite_friend", args=[question.pk])
    client.force_login(first.user)
    assert client.post(url, {"friend_id": second.pk}).status_code == 404
    Friendship.objects.create(first=first, second=second, requested_by=first, accepted=True)
    for _ in range(2):
        assert client.post(url, {"friend_id": second.pk}).status_code == 302
    assert MarketInvitation.objects.count() == 1
    client.force_login(second.user)
    assert "Try this poll" in client.get(reverse("market:friends")).content.decode()
    client.force_login(outsider.user)
    assert "Try this poll" not in client.get(reverse("market:friends")).content.decode()
    question.status = "closed"
    question.save()
    client.force_login(first.user)
    client.post(url, {"friend_id": second.pk})
    assert MarketInvitation.objects.count() == 1


@pytest.mark.django_db
def test_profile_optional_fields(client, people):
    first = people[0]
    client.force_login(first.user)
    url = reverse("market:profile")
    data = {"name": "Name", "pseudonym": "Alias", "birth_date": "2000-01-15", "city": "Москва", "workplace": "Компания", "education": "Вуз", "bio": "Прогнозирую"}
    assert client.post(url, data).status_code == 302
    first.refresh_from_db()
    assert first.city == "Москва" and first.birth_date.year == 2000
    data["birth_date"] = "2999-01-01"
    assert client.post(url, data).context["form"].errors.get("birth_date")
    page = client.get(url).content.decode()
    assert "profile-column" in page and "Текущий:" not in page
