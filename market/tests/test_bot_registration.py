from urllib.parse import urlparse
from datetime import timedelta
import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from market.models import Trader, TelegramSiteLink
from market.bot_registration import site_link


@pytest.mark.django_db
def test_expired_and_revoked_login(client):
    user = User.objects.create_user(username="test")
    trader = Trader.objects.create(user=user, name="Name", pseudonym="Name", telegram_chat_id=456)
    first = urlparse(site_link(trader)).fragment
    second = urlparse(site_link(trader)).fragment
    client.post(reverse("market:telegram_site_login"), {"token": first})
    assert "_auth_user_id" not in client.session
    TelegramSiteLink.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
    client.post(reverse("market:telegram_site_login"), {"token": second})
    assert "_auth_user_id" not in client.session
