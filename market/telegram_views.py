from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from .identity import get_trader
from .models import TelegramLink, TelegramSiteLink
from .telegram import create_link, send, TelegramError


@require_POST
def connection(request):
    trader = get_trader(request)
    if not trader:
        return redirect("market:login")
    action = request.POST.get("action")
    if action == "disconnect":
        if trader.user and not trader.user.has_usable_password():
            messages.error(request, "Telegram — ваш единственный способ входа. Сначала настройте пароль аккаунта.")
            return redirect("market:me")
        TelegramSiteLink.objects.filter(trader=trader).delete()
        trader.telegram_chat_id = None
        trader.save(update_fields=["telegram_chat_id"])
        TelegramLink.objects.filter(trader=trader).delete()
        request.session.pop("telegram_link", None)
        messages.success(request, "Telegram отключён.")
    elif action == "test" and trader.telegram_chat_id:
        try:
            send(trader.telegram_chat_id, "Связь с рынком прогнозов работает!")
            messages.success(request, "Тестовое сообщение отправлено.")
        except TelegramError as exc:
            messages.error(request, str(exc))
    elif action == "connect":
        if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_BOT_USERNAME:
            messages.error(request, "Telegram-бот ещё не настроен администратором.")
        else:
            request.session["telegram_link"] = create_link(trader)
    return redirect("market:me")
