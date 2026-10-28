from datetime import timedelta
import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from market.models import Question, Trader
from market.selectors import market_list


@pytest.mark.django_db
def test_expired_open_market_waits_for_result(client):
    question = Question.objects.create(title="Expired", deadline=timezone.now()-timedelta(minutes=1))
    assert question.display_state == "closed"
    assert question.pk not in market_list(status="open").values_list("pk", flat=True)
    assert question.pk in market_list(status="closed").values_list("pk", flat=True)
    user = User.objects.create_user("visitor", password="password")
    client.force_login(user)
    page = client.get(reverse("market:question", args=[question.pk]))
    assert "Ожидает результата" in page.content.decode()
    assert 'id="betform"' not in page.content.decode()


@pytest.mark.django_db
def test_closed_poll_is_completed_and_resolved_market_shows_outcome(client):
    poll = Question.objects.create(title="Completed poll", kind="poll", status="closed")
    assert poll.display_state == "resolved"
    assert poll.pk in market_list(status="resolved").values_list("pk", flat=True)
    assert poll.pk not in market_list(status="closed").values_list("pk", flat=True)
    Question.objects.create(title="Finished", status="resolved", outcome="no")
    page = client.get(reverse("market:markets")).content.decode()
    assert "Итог: НЕТ" in page and "Завершён" in page


@pytest.mark.django_db
def test_daily_gift_modal_only_on_first_visit(client):
    user = User.objects.create_user("gift", password="password")
    Trader.objects.create(user=user, name="Gift", pseudonym="Gift")
    client.force_login(user)
    url = reverse("market:markets")
    first = client.get(url).content.decode()
    assert 'id="daily-gift"' in first and "+2 XP" in first
    assert 'id="daily-gift"' not in client.get(url).content.decode()
