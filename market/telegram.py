"""Telegram transport and private-chat account linking."""
import hashlib
import secrets
from datetime import timedelta

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import TelegramLink, Trader, Question


class TelegramError(Exception):
    pass


def api(method, **data):
    if not settings.TELEGRAM_BOT_TOKEN:
        raise TelegramError("Токен Telegram-бота не настроен.")
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}",
            json=data, timeout=40,
        )
        result = response.json()
        if not response.ok or not result.get("ok"):
            raise TelegramError("Telegram отклонил запрос.")
        return result["result"]
    except (requests.RequestException, ValueError):
        raise TelegramError("Не удалось связаться с Telegram.") from None


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
    if "callback_query" in update:
        resolution_dialog.handle_callback(update["callback_query"])
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
        send(chat_id, "Аккаунт привязан. /balance — баланс, /markets — рынки, /unlink — отключить." if ok else "Ссылка недействительна или Telegram уже привязан. Создайте новую ссылку на сайте.")
        return
    trader = Trader.objects.select_related("user").filter(telegram_chat_id=chat_id).first()
    from .telegram_menu import BUTTONS, handle_menu, keyboard
    text = message.get("text", "")
    text = BUTTONS.get(text, text)
    command = text.split(maxsplit=1)[0].split("@")[0] if text else ""
    if trader and (not trader.user or trader.user.is_active) and command in ("/start", "/menu", "/help"):
        handle_menu(trader, command, send)
        return
    if not trader and command == "/start":
        from .bot_resolution import welcome
        welcome(chat_id)
        return
    from .bot_registration import handle_dialog
    if handle_dialog(chat_id, text):
        return
    if trader and trader.user and not trader.user.is_active:
        send(chat_id, "Сначала подтвердите почту. /resend — повторить отправку письма.")
        return
    if not trader:
        from .bot_resolution import welcome
        welcome(chat_id)
    elif handle_menu(trader, command, send):
        return
    elif command == "/unlink":
        if trader.user and not trader.user.has_usable_password():
            send(chat_id, "Это ваш единственный способ входа. Отключение доступно после настройки пароля аккаунта.")
            return
        from .models import TelegramSiteLink
        TelegramSiteLink.objects.filter(trader=trader).delete()
        trader.telegram_chat_id = None
        trader.save(update_fields=["telegram_chat_id"])
        TelegramLink.objects.filter(trader=trader).delete()
        send(chat_id, "Telegram отключён.", reply_markup={"remove_keyboard": True})
    else:
        send(chat_id, "Выберите раздел в меню. /help — помощь.", reply_markup=keyboard())
