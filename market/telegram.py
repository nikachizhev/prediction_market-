"""Telegram transport and private-chat account linking."""
import hashlib
import json
import secrets
from datetime import timedelta

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import TelegramLink, Trader, Question


class TelegramError(Exception):
    pass


def api(method, *, http_timeout=40, **data):
    """Bot API call. `http_timeout` is ours (seconds); `data` goes to Telegram as is."""
    if not settings.TELEGRAM_BOT_TOKEN:
        raise TelegramError("Токен Telegram-бота не настроен.")
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}",
            json=data, timeout=http_timeout,
        )
        result = response.json()
        if not response.ok or not result.get("ok"):
            raise TelegramError("Telegram отклонил запрос.")
        return result["result"]
    except (requests.RequestException, ValueError):
        raise TelegramError("Не удалось связаться с Telegram.") from None


def upload(method, files, **data):
    """Multipart call for sending a new file; `files` maps a field to (filename, bytes, content type).

    Nested values (reply_markup, ...) go as JSON strings, as the Bot API expects in form data.
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        raise TelegramError("Токен Telegram-бота не настроен.")
    form = {key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, bool)) else str(value)
            for key, value in data.items() if value is not None}
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}",
            data=form, files=files, timeout=60,
        )
        result = response.json()
        if not response.ok or not result.get("ok"):
            raise TelegramError("Telegram отклонил запрос.")
        return result["result"]
    except (requests.RequestException, ValueError):
        raise TelegramError("Не удалось связаться с Telegram.") from None


APP_ONLY = "Всё теперь в приложении — открой его кнопкой «Открыть приложение» слева от поля ввода. Твои рынки — /my."


def send(chat_id, text, **kwargs):
    return api("sendMessage", chat_id=chat_id, text=text, **kwargs)


MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # Bot API getFile limit


def download_file(file_id: str) -> tuple[bytes, str]:
    """Fetch a file the user sent to the bot; returns its bytes and Telegram file_path."""
    info = api("getFile", file_id=file_id)
    path = info.get("file_path")
    if not path or (info.get("file_size") or 0) > MAX_DOWNLOAD_BYTES:
        raise TelegramError("Файл недоступен или слишком большой.")
    try:
        response = requests.get(
            f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{path}", timeout=40,
        )
    except requests.RequestException:
        raise TelegramError("Не удалось скачать файл из Telegram.") from None
    if not response.ok or len(response.content) > MAX_DOWNLOAD_BYTES:
        raise TelegramError("Не удалось скачать файл из Telegram.")
    return response.content, path


def create_link(trader):
    token = secrets.token_urlsafe(24)
    TelegramLink.objects.update_or_create(
        trader=trader,
        defaults={"token_hash": hashlib.sha256(token.encode()).hexdigest(),
                  "expires_at": timezone.now() + timedelta(minutes=10)},
    )
    return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={token}"


@transaction.atomic
def consume_link(token, chat_id):
    link = TelegramLink.objects.select_for_update().filter(
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at__gt=timezone.now(),
    ).first()
    if not link:
        return False
    trader = Trader.objects.select_for_update().get(pk=link.trader_id)
    if Trader.objects.filter(telegram_chat_id=chat_id).exclude(pk=trader.pk).exists():
        return False
    trader.telegram_chat_id = chat_id
    trader.save(update_fields=["telegram_chat_id"])
    link.delete()
    return True


def handle_update(update):
    from . import bot_resolution_dialog as resolution_dialog
    if "inline_query" in update:
        from .share import handle_inline_query
        handle_inline_query(update["inline_query"])
        return
    if "callback_query" in update:
        callback = update["callback_query"]
        data = callback.get("data") or ""
        from . import bot_chats, bot_markets, chat_disputes
        if bot_markets.owns(data):
            bot_markets.handle_callback(callback)
        elif bot_chats.owns(data):
            bot_chats.handle_callback(callback)
        elif chat_disputes.owns(data):
            chat_disputes.handle_callback(callback)
        else:
            resolution_dialog.handle_callback(callback)
        return
    from . import bot_chats
    if bot_chats.handle_update(update):
        return
    message = update.get("message", {})
    chat = message.get("chat", {})
    if chat.get("type") != "private" or message.get("from", {}).get("is_bot"):
        return
    chat_id = chat["id"]
    if resolution_dialog.handle_message(message):
        return
    parts = message.get("text", "").split(maxsplit=1)
    command = parts[0].split("@")[0] if parts else ""
    if command == "/start" and len(parts) == 2 and parts[1].startswith("resolve_"):
        resolution_dialog.start(chat_id, parts[1][len("resolve_"):])
        return
    if command == "/start" and len(parts) == 2 and parts[1].startswith("dispute_"):
        resolution_dialog.start_dispute(chat_id, parts[1][len("dispute_"):])
        return
    if command == "/start" and len(parts) == 2:
        if parts[1].startswith("auth_"):
            from .telegram_auth import confirm
            ok = confirm(parts[1][5:], chat_id, message.get("from", {}).get("first_name", ""))
            send(chat_id, "Telegram подтверждён. Вернитесь в браузер, обновите страницу и нажмите «Продолжить». Подтверждайте только свой запрос входа." if ok else "Ссылка истекла или уже использована. Начните вход на сайте заново.")
            return
        ok = consume_link(parts[1], chat_id)
        send(chat_id, "Аккаунт привязан. " + APP_ONLY if ok else "Ссылка недействительна или Telegram уже привязан. Создайте новую ссылку на сайте.")
        return
    from .bot_resolution import welcome
    if command == "/start":
        welcome(chat_id)
        return
    if command == "/my":
        from .bot_markets import show
        show(chat_id)
        return
    # For now the bot only leads to the Mini App. Any other text, including taps on the old
    # site menu, gets a pointer there and clears that menu's keyboard from the chat.
    if Trader.objects.filter(telegram_chat_id=chat_id).exists():
        send(chat_id, APP_ONLY, reply_markup={"remove_keyboard": True})
    else:
        welcome(chat_id)
