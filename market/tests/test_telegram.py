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


def private(text, chat_id=123):
    return {"message": {"chat": {"id": chat_id, "type": "private"}, "from": {"id": chat_id}, "text": text}}


@pytest.mark.django_db
def test_start_offers_the_app_and_my_markets():
    Trader.objects.create(name="Test", pseudonym="Test", telegram_chat_id=123)
    with patch("market.telegram.send") as send:
        handle_update(private("/start"))
    keyboard = send.call_args.kwargs["reply_markup"]["inline_keyboard"]
    assert keyboard[0][0]["web_app"]
    assert keyboard[1][0]["text"] == "Мои рынки"


@pytest.mark.django_db
@override_settings(SITE_URL="https://site.example")
def test_old_menu_only_points_to_the_app():
    trader = Trader.objects.create(name="Test", pseudonym="Test", telegram_chat_id=123)
    with patch("market.telegram.send") as send:
        for text in ("💰 Баланс", "🌐 Войти на сайт", "📊 Рынки", "/balance", "/login", "/register", "/unlink", "привет"):
            handle_update(private(text))
            assert send.call_args.kwargs["reply_markup"] == {"remove_keyboard": True}
            assert "/my" in send.call_args.args[1]
    assert "site.example" not in str(send.call_args_list)
    trader.refresh_from_db()
    assert trader.telegram_chat_id == 123


@pytest.mark.django_db
def test_stranger_gets_the_welcome_without_registration():
    with patch("market.telegram.send") as send:
        handle_update(private("Зарегистрироваться", chat_id=456))
    assert send.call_args.kwargs["reply_markup"]["inline_keyboard"][0][0]["web_app"]
    assert not Trader.objects.exists()
