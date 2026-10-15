from unittest.mock import patch
from datetime import timedelta
from urllib.parse import urlparse, parse_qs
import pytest
from django.test import override_settings
from django.utils import timezone
from market.models import Trader, TelegramLink
from market.telegram import create_link, consume_link, handle_update


@pytest.mark.django_db
@override_settings(TELEGRAM_BOT_USERNAME="test_bot")
def test_link_is_expiring_and_single_use():
    trader = Trader.objects.create(name="Test", pseudonym="Test")
    token = parse_qs(urlparse(create_link(trader)).query)["start"][0]
    assert TelegramLink.objects.get().token_hash != token
    assert consume_link(token, 123)
    assert not consume_link(token, 456)
    trader.refresh_from_db()
    assert trader.telegram_chat_id == 123
    token = parse_qs(urlparse(create_link(trader)).query)["start"][0]
    TelegramLink.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
    assert not consume_link(token, 456)


@pytest.mark.django_db
def test_group_cannot_link_account():
    with patch("market.telegram.send") as send:
        handle_update({"message": {"chat": {"id": -123, "type": "group"}, "text": "/start secret"}})
        send.assert_not_called()


@pytest.mark.django_db
def test_balance_and_disconnect():
    trader = Trader.objects.create(name="Test", pseudonym="Test", telegram_chat_id=123)
    with patch("market.telegram.send") as send:
        handle_update({"message": {"chat": {"id": 123, "type": "private"}, "text": "/balance"}})
        assert "1000" in send.call_args.args[1]
        handle_update({"message": {"chat": {"id": 123, "type": "private"}, "text": "/unlink"}})
    trader.refresh_from_db()
    assert trader.telegram_chat_id is None
