from datetime import timedelta
from decimal import Decimal
import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from market.models import Trader, Group, GroupMembership, Question, MarketSubscription, Notification, Trade, PurchaseConfirmation
from market import services
from market.group_analytics import analytics


@pytest.fixture
def members(db):
    people = [Trader.objects.create(user=User.objects.create_user(f"analyst{i}"), name=f"User{i}", pseudonym=f"Alias{i}") for i in range(3)]
    group = Group.objects.create(name="Analytics team", owner=people[0], kind="work")
    for trader in people[:2]:
        GroupMembership.objects.create(group=group, trader=trader, status="active")
    return group, people


@pytest.mark.django_db
def test_risk_panel_only_deadline_questions(members):
    group, (owner, member, _) = members
    now = timezone.now()
    risk = Question.objects.create(title="Release", group=group, tracks_deadline=True, deadline=now+timedelta(days=2))
    services.buy(member, risk, "no", 200)
    Question.objects.create(title="Fun binary", group=group, deadline=now+timedelta(days=1))
    Question.objects.create(title="Poll", group=group, kind="poll", tracks_deadline=True)
    data = analytics(group)
    assert len(data["risks"]) == 1
    assert data["risk_count"] == 1 and data["thin_count"] == 1
    assert data["risks"][0]["question"].pk == risk.pk
    assert data["risks"][0]["falling"]


@pytest.mark.django_db
def test_analytics_pages_private_and_render(client, members):
    group, (owner, member, outsider) = members
    for trader, expected in [(member, 200), (outsider, 404)]:
        client.force_login(trader.user)
        for name in ("market:group_risks", "market:group_report"):
            assert client.get(reverse(name, args=[group.pk])).status_code == expected
    client.logout()
    assert client.get(reverse("market:group_risks", args=[group.pk])).status_code == 302


@pytest.mark.django_db
def test_report_uses_historical_forecast_not_final_probability(members):
    group, (_, member, _) = members
    now = timezone.now()
    question = Question.objects.create(title="Historical", group=group, tracks_deadline=True, deadline=now+timedelta(days=2))
    Question.objects.filter(pk=question.pk).update(created_at=now-timedelta(days=7))
    first = services.buy(member, question, "yes", 100)
    Trade.objects.filter(pk=first.pk).update(created_at=now-timedelta(days=3))
    # A last-minute trade must not replace the forecast from the evaluation horizon.
    services.buy(member, question, "no", 200)
    services.resolve(question, "yes")
    data = analytics(group)
    row = data["completed"][0]
    assert row["prediction"] == round(first.prob_after*100)
    assert data["brier"] == round((first.prob_after-1)**2, 3)
    assert data["deadline_yes"] == 1 and data["evaluated_count"] == 1


@pytest.mark.django_db
def test_report_excludes_old_and_unobserved_forecasts(members):
    group, (_, member, _) = members
    q = Question.objects.create(title="Old", group=group, status="resolved", outcome="yes", deadline=timezone.now())
    data = analytics(group)
    assert data["done_count"] == 1 and data["brier"] is None
    assert data["completed"][0]["prediction"] is None


@pytest.mark.django_db
def test_subscription_idempotent_private_and_no_debit(client, members):
    group, (owner, member, outsider) = members
    question = Question.objects.create(title="Follow me", group=group)
    url = reverse("market:subscribe", args=[question.pk])
    client.force_login(outsider.user)
    assert client.post(url).status_code == 404
    client.force_login(member.user)
    for _ in range(2):
        assert client.post(url).status_code == 302
    assert MarketSubscription.objects.filter(question=question, trader=member).count() == 1
    member.refresh_from_db()
    assert member.balance == 1000
    services.resolve(question, "yes")
    assert Notification.objects.filter(recipient=member, question=question, kind="market_result").count() == 1
    assert client.get(reverse("market:subscriptions")).status_code == 200
    client.post(url, {"action": "unsubscribe"})
    assert not MarketSubscription.objects.exists()


@pytest.mark.django_db
def test_subscription_change_threshold_and_cooldown(client, members):
    group, (owner, member, _) = members
    question = Question.objects.create(title="Moving", group=group)
    client.force_login(member.user)
    client.post(reverse("market:subscribe", args=[question.pk]))
    services.buy(owner, question, "yes", 1)
    assert not Notification.objects.filter(kind="market_change").exists()
    services.buy(owner, question, "yes", 200)
    assert Notification.objects.filter(kind="market_change").count() == 1
    services.buy(owner, question, "no", 200)
    assert Notification.objects.filter(kind="market_change").count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["lmsr", "ls_lmsr", "cpmm"])
@pytest.mark.parametrize("side", ["yes", "no"])
def test_review_matches_purchase_and_duplicate_is_safe(members, kind, side):
    group, (_, member, _) = members
    q = Question.objects.create(title="Buy", group=group, kind=kind)
    quote, token = services.prepare_purchase(member, q, side, Decimal("10.25"))
    member.refresh_from_db()
    assert member.balance == 1000 and not Trade.objects.exists()
    trade = services.confirm_purchase(member, q, token)
    assert trade.cost == quote["cost"] and trade.shares == quote["shares"]
    assert services.confirm_purchase(member, q, token).pk == trade.pk
    assert Trade.objects.count() == 1
    member.refresh_from_db()
    assert member.balance == Decimal(1000)-quote["cost"]


@pytest.mark.django_db
def test_confirmation_rejects_changed_price_expiry_and_other_user(members):
    group, (owner, member, _) = members
    question = Question.objects.create(title="Snapshot", group=group)
    _, token = services.prepare_purchase(member, question, "yes", 10)
    with pytest.raises(services.MarketError):
        services.confirm_purchase(owner, question, token)
    services.buy(owner, question, "no", 20)
    with pytest.raises(services.MarketError, match="Цена изменилась"):
        services.confirm_purchase(member, question, token)
    question.refresh_from_db()
    _, next_token = services.prepare_purchase(member, question, "yes", 10)
    PurchaseConfirmation.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
    with pytest.raises(services.MarketError, match="устарел"):
        services.confirm_purchase(member, question, next_token)
    member.refresh_from_db()
    assert member.balance == 1000


@pytest.mark.django_db
def test_purchase_help_is_inline_and_buy_takes_one_post(client, members):
    group, (_, member, _) = members
    q = Question.objects.create(title="Review", group=group)
    client.force_login(member.user)
    page = client.get(reverse("market:question", args=[q.pk]))
    assert page.status_code == 200
    html = page.content.decode()
    assert '<dialog id="purchase-help"' in html
    assert 'aria-label="Объяснение покупки"' in html
    assert "Купить акции" in html and "Максимальная потеря" in html
    assert "На следующем шаге" not in html
    assert not Trade.objects.exists()
    assert not PurchaseConfirmation.objects.exists()
    member.refresh_from_db()
    before = member.balance
    response = client.post(reverse("market:buy", args=[q.pk]), {"amount": "20", "side": "yes", "purchase_token": page.context["purchase_token"]})
    assert response.status_code == 302
    assert response.url == reverse("market:question", args=[q.pk])
    assert Trade.objects.count() == 1
    member.refresh_from_db()
    assert member.balance == before - Decimal("20")
