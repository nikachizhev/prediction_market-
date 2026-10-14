import hashlib
import secrets
from datetime import timedelta
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import TelegramAuth, Trader
from .models import TelegramSiteLink
from .forms import TelegramRegistrationForm


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


@transaction.atomic
def site_login(request):
    if request.method == "POST":
        token = request.POST.get("token", "")
        link = TelegramSiteLink.objects.select_for_update().filter(token_hash=digest(token), expires_at__gt=timezone.now()).first()
        if link:
            trader = Trader.objects.select_related("user").get(pk=link.trader_id)
            if trader.telegram_chat_id and trader.user and trader.user.is_active:
                if request.user.is_authenticated and request.user.pk != trader.user_id:
                    messages.error(request, "Сначала выйдите из текущего аккаунта.")
                    return redirect("market:me")
                link.delete()
                login(request, trader.user)
                return redirect("market:welcome")
        messages.error(request, "Ссылка истекла или использована. Получите новую командой /login в боте.")
        return redirect("market:login")
    response = render(request, "market/telegram_site_login.html")
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


@require_POST
def start(request):
    if request.user.is_authenticated:
        return redirect("market:me")
    if not settings.TELEGRAM_BOT_USERNAME or not settings.TELEGRAM_BOT_TOKEN:
        messages.error(request, "Telegram пока не настроен. Используйте регистрацию по почте.")
        return redirect("market:register")
    token = secrets.token_urlsafe(24)
    old = request.session.get("telegram_auth")
    if old:
        TelegramAuth.objects.filter(token_hash=old).delete()
    TelegramAuth.objects.create(token_hash=digest(token), expires_at=timezone.now()+timedelta(minutes=10))
    request.session["telegram_auth"] = digest(token)
    request.session["telegram_auth_url"] = f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start=auth_{token}"
    return redirect("market:telegram_complete")


@transaction.atomic
def confirm(token, telegram_id, first_name):
    auth = TelegramAuth.objects.select_for_update().filter(token_hash=digest(token), expires_at__gt=timezone.now(), consumed=False, telegram_id__isnull=True).first()
    if not auth:
        return False
    auth.telegram_id = telegram_id
    auth.first_name = first_name[:100]
    auth.save(update_fields=["telegram_id", "first_name"])
    return True


@transaction.atomic
def complete(request):
    if request.user.is_authenticated:
        return redirect("market:markets")
    auth = TelegramAuth.objects.select_for_update().filter(token_hash=request.session.get("telegram_auth", ""), consumed=False, expires_at__gt=timezone.now()).first()
    if not auth:
        messages.error(request, "Ссылка истекла. Начните вход через Telegram ещё раз.")
        return redirect("market:login")
    form = TelegramRegistrationForm(request.POST if request.method == "POST" else None)
    trader = Trader.objects.select_related("user").filter(telegram_chat_id=auth.telegram_id).first() if auth.telegram_id else None
    if request.method == "POST" and auth.telegram_id:
        if trader and trader.user:
            user = trader.user
        elif form.is_valid():
            user = User(username=f"tg_{auth.telegram_id}_{secrets.token_hex(4)}", email=form.cleaned_data["email"], first_name=auth.first_name, is_active=False)
            user.set_unusable_password()
            user.save()
            from .email_verification import mark_pending
            mark_pending(user)
            if trader:
                trader.user = user
                trader.save(update_fields=["user"])
            else:
                Trader.objects.create(user=user, name=auth.first_name or "Участник", pseudonym=f"Участник {auth.telegram_id}", telegram_chat_id=auth.telegram_id)
        else:
            user = None
        if user is not None:
            if not user.is_active:
                from .email_verification import send_verification
                ok = send_verification(user)
                messages.info(request, "Подтвердите почту по ссылке в письме." if ok else "Письмо не отправлено. Проверьте SMTP-настройки и повторите отправку.")
                return redirect("market:email_pending")
            auth.consumed = True
            auth.save(update_fields=["consumed"])
            request.session.pop("telegram_auth", None)
            request.session.pop("telegram_auth_url", None)
            login(request, user)
            return redirect("market:welcome")
    return render(request, "market/telegram_auth.html", {"verified": bool(auth.telegram_id), "existing": bool(trader and trader.user), "form": form})
