from unittest.mock import patch
from urllib.parse import urlparse
import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from market.models import Trader, Group, GroupMembership, Question, Notification, Friendship
from market import services
from market.access import visible_questions
from market.bot_registration import site_link
from market.question_forms import CreateQuestionForm


@pytest.fixture
def team(db):
    people = [Trader.objects.create(user=User.objects.create_user(f"team{i}"), name=f"Team{i}", pseudonym=f"Alias{i}", telegram_chat_id=100+i) for i in range(3)]
    group = Group.objects.create(name="Work team", kind="work", owner=people[0])
    GroupMembership.objects.create(group=group, trader=people[0], status="active")
    return group, people


@pytest.mark.django_db
def test_telegram_login_preserves_origin_and_csrf(team):
    _, people = team
    browser = Client(enforce_csrf_checks=True)
    token = urlparse(site_link(people[0])).fragment
    url = reverse("market:telegram_site_login")
    page = browser.get(url)
    assert page["Referrer-Policy"] == "same-origin"
    csrf = browser.cookies["csrftoken"].value
    assert browser.post(url, {"token": token, "csrfmiddlewaretoken": csrf}, HTTP_ORIGIN="null").status_code == 403
    assert browser.post(url, {"token": token}, HTTP_ORIGIN="http://testserver").status_code == 403
    assert browser.post(url, {"token": token, "csrfmiddlewaretoken": csrf}, HTTP_ORIGIN="http://testserver").status_code == 302
    assert int(browser.session["_auth_user_id"]) == people[0].user_id


@pytest.mark.django_db
def test_group_invitation_and_request_permissions(client, team):
    group, (owner, member, stranger) = team
    url = reverse("market:group_detail", args=[group.pk])
    client.force_login(owner.user)
    client.post(url, {"action": "invite", "username": member.user.username})
    membership = GroupMembership.objects.get(group=group, trader=member)
    assert membership.status == "invited"
    assert Notification.objects.filter(recipient=member, kind="group_invite").exists()
    client.force_login(member.user)
    client.post(url, {"action": "accept_invite"})
    membership.refresh_from_db()
    assert membership.status == "active"
    assert client.post(url, {"action": "invite", "username": stranger.user.username}).status_code == 403
    client.force_login(stranger.user)
    client.post(url, {"action": "join"})
    pending = GroupMembership.objects.get(group=group, trader=stranger)
    assert pending.status == "requested"
    client.force_login(member.user)
    assert client.post(url, {"action": "approve", "member_id": pending.pk}).status_code == 403
    client.force_login(owner.user)
    client.post(url, {"action": "approve", "member_id": pending.pk})
    pending.refresh_from_db()
    assert pending.status == "active"
    for name in ("market:groups", "market:group_create"):
        assert client.get(reverse(name)).status_code == 200
    assert client.get(url).status_code == 200


@pytest.mark.django_db
def test_private_markets_hidden_everywhere(client, team):
    group, (owner, member, stranger) = team
    q = services.create_question(title="Secret roadmap", resolution_criteria="Secret", group_id=group.pk)
    client.force_login(stranger.user)
    for url in [reverse("market:question", args=[q.pk]), reverse("market:quote", args=[q.pk]), reverse("market:invite_friend", args=[q.pk])]:
        assert client.get(url).status_code == 404
    for name in ("market:buy", "market:sell", "market:respond", "market:finish"):
        assert client.post(reverse(name, args=[q.pk]), {"side": "yes", "amount": "10"}).status_code == 404
    assert "Secret roadmap" not in client.get(reverse("market:markets")).content.decode()
    assert not client.get(reverse("market:markets_api")).json()["results"]
    with pytest.raises(services.MarketError):
        services.buy(stranger, q, "yes", 10)
    client.force_login(owner.user)
    assert client.get(reverse("market:question", args=[q.pk])).status_code == 200
    assert q.pk in visible_questions(owner).values_list("pk", flat=True)
    client.logout()
    assert client.get(reverse("market:question", args=[q.pk])).status_code == 404


@pytest.mark.django_db
def test_group_form_cannot_publish_into_other_group(team):
    group, (_, _, stranger) = team
    form = CreateQuestionForm({"title": "Secret", "group": group.pk, "kind": "poll", "option_lines": "A\nB", "resolution_criteria": "Done", "b": 200, "number_min": 0, "number_max": 100, "tolerance": 0}, trader=stranger)
    assert not form.is_valid() and "group" in form.errors


@pytest.mark.django_db
def test_notification_delivery_read_permissions_and_results(client, team):
    _, (first, second, stranger) = team
    client.force_login(first.user)
    client.post(reverse("market:friends"), {"action": "request", "username": second.user.username})
    notification = Notification.objects.get(recipient=second, kind="friend_request")
    assert client.post(reverse("market:notification_read", args=[notification.pk])).status_code == 404
    client.force_login(second.user)
    assert client.get(reverse("market:notification_feed")).json()["unread"] == 1
    assert client.get(reverse("market:notifications")).status_code == 200
    client.post(reverse("market:notification_read", args=[notification.pk]))
    assert client.get(reverse("market:notification_feed")).json()["unread"] == 0
    q = services.create_question(title="Public result", resolution_criteria="Done")
    services.buy(second, q, "no", 10)
    services.resolve(q, "yes")
    assert Notification.objects.filter(recipient=second, question=q, kind="market_result").count() == 1
    with pytest.raises(services.MarketError):
        services.resolve(q, "yes")
    assert Notification.objects.filter(recipient=second, question=q).count() == 1
    assert not Notification.objects.filter(recipient=stranger).exists()


@pytest.mark.django_db
def test_github_group_sync_scoped_and_idempotent(team):
    from sources.models import GitHubRepo
    from sources.github import GitHubSource
    from sources.sync import sync_all
    group, (owner, _, _) = team
    another = Group.objects.create(name="Other", owner=owner, kind="work")
    GitHubRepo.objects.create(owner="acme", name="app", group=group)
    GitHubRepo.objects.create(owner="acme", name="app", group=another)
    def getter(path, params):
        if path.endswith("milestones"):
            return [{"number": 1, "title": "Ship", "due_on": "2099-12-01T00:00:00Z", "state": "open"}]
        return []
    for _ in range(2):
        sync_all([GitHubSource(getter=getter, group_id=group.pk)])
    assert Question.objects.count() == 1
    assert Question.objects.get().group_id == group.pk
    sync_all([GitHubSource(getter=getter, group_id=another.pk)])
    assert Question.objects.count() == 2


@pytest.mark.django_db
def test_removed_member_loses_private_notifications(client, team):
    from market.notifications import notify
    group, (owner, member, _) = team
    membership = GroupMembership.objects.create(group=group, trader=member, status="active")
    question = services.create_question(title="Private release", resolution_criteria="Done", group_id=group.pk)
    services.buy(member, question, "yes", 10)
    notify(member, "Private release", question=question)
    membership.delete()
    client.force_login(member.user)
    assert not client.get(reverse("market:notification_feed")).json()["items"]
    assert "Private release" not in client.get(reverse("market:me")).content.decode()
    services.resolve(question, "yes")
    member.refresh_from_db()
    assert member.balance > 1000


@pytest.mark.django_db
def test_invite_private_question_only_to_member_friends(client, team):
    group, (owner, friend, _) = team
    Friendship.objects.create(first=owner, second=friend, requested_by=owner, accepted=True)
    question = services.create_question(title="Private vote", resolution_criteria="Done", group_id=group.pk)
    client.force_login(owner.user)
    url = reverse("market:invite_friend", args=[question.pk])
    assert client.post(url, {"friend_id": friend.pk}).status_code == 404
    GroupMembership.objects.create(group=group, trader=friend, status="active")
    client.post(url, {"friend_id": friend.pk})
    assert Notification.objects.filter(recipient=friend, question=question).count() == 1


@pytest.mark.django_db
def test_group_creator_and_search_ui(client, team):
    _, (owner, member, _) = team
    client.force_login(owner.user)
    response = client.post(reverse("market:group_create"), {"name": "Friends Club", "description": "Our forecasts", "kind": "social"})
    assert response.status_code == 302
    group = Group.objects.get(name="Friends Club")
    assert GroupMembership.objects.get(group=group, trader=owner).status == "active"
    client.force_login(member.user)
    page = client.get(reverse("market:groups"), {"q": "Friends"})
    assert "Friends Club" in page.content.decode()
    detail = client.get(reverse("market:group_detail", args=[group.pk])).content.decode()
    assert "Подать заявку" in detail and "Наши" not in detail


@pytest.mark.django_db
def test_group_repo_does_not_use_platform_token_without_approval(team):
    from sources.models import GitHubRepo
    from sources.github import GitHubSource
    group, _ = team
    GitHubRepo.objects.create(owner="acme", name="private", group=group)
    with patch("sources.github.http_get", return_value=[]) as getter:
        GitHubSource(group_id=group.pk).discover()
        assert getter.call_count == 2
        assert all(call.kwargs.get("use_token") is False for call in getter.call_args_list)
