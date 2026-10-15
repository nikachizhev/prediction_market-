"""Registration dialog; its state survives bot restarts."""
import secrets
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from .forms import TelegramRegistrationForm
from .models import BotRegistration, TelegramSiteLink, Trader
from .telegram_auth import digest


@transaction.atomic
def site_link(trader):
    token = secrets.token_urlsafe(32)
    TelegramSiteLink.objects.update_or_create(trader=trader, defaults={
        "token_hash": digest(token), "expires_at": timezone.now()+timedelta(minutes=10),
    })
    # Fragment stays out of HTTP request/access logs. The landing page submits it via POST.
    return f"{settings.SITE_URL}/auth/telegram/link/#{token}"


def send_login(trader):
    from .telegram import send
    if trader.user and not trader.user.is_active:
        send(trader.telegram_chat_id, "Подтвердите почту по ссылке в письме. /resend — отправить письмо ещё раз. После подтверждения: /login.")
        return
    if not trader.user:
        send(trader.telegram_chat_id, "Аккаунт недоступен. Для регистрации используйте /register.")
        return
    url = site_link(trader)
    send(trader.telegram_chat_id, "Вход на сайт без пароля. Ссылка одноразовая, действует 10 минут. Не пересылайте её другим людям.",
         reply_markup={"inline_keyboard": [[{"text": "Открыть сайт ↗", "url": url}]]})


@transaction.atomic
def register_email(state, email):
    state = BotRegistration.objects.select_for_update().get(pk=state.pk)
    if state.expires_at <= timezone.now():
        return None, "Время регистрации истекло. Начните заново: /register."
    existing = Trader.objects.select_related("user").filter(telegram_chat_id=state.chat_id).first()
    if existing and existing.user:
        state.delete()
        return existing, None
    form = TelegramRegistrationForm({"email": email})
    if not form.is_valid():
        return None, " ".join(form.errors["email"])
    user = User(username=f"tg_{state.chat_id}_{secrets.token_hex(4)}", first_name=state.name, email=form.cleaned_data["email"], is_active=False)
    user.set_unusable_password()
    user.save()
    from .email_verification import mark_pending
    mark_pending(user)
    if existing:
        existing.user = user
        existing.save(update_fields=["user"])
        trader = existing
    else:
        trader = Trader.objects.create(user=user, telegram_chat_id=state.chat_id, name=state.name, pseudonym=state.name)
    state.delete()
    return trader, None


def handle_dialog(chat_id, text):
    from .telegram import send
    trader = Trader.objects.select_related("user").filter(telegram_chat_id=chat_id).first()
    command = text.split(maxsplit=1)[0].split("@")[0] if text else ""
    if command == "/resend":
        from .email_verification import send_verification
        if trader and trader.user and not trader.user.is_active:
            ok = send_verification(trader.user)
            send(chat_id, "Письмо отправлено или уже отправлялось в последнюю минуту. Проверьте папку «Спам»." if ok else "Не удалось отправить письмо. Администратору нужно проверить SMTP-настройки.")
        else:
            send(chat_id, "Нет регистрации, ожидающей подтверждения. /register — регистрация, /login — вход.")
        return True
    if command == "/cancel":
        BotRegistration.objects.filter(chat_id=chat_id).delete()
        send(chat_id, "Регистрация отменена. /register — начать, /login — войти на сайт.")
        return True
    if command in ("/register", "/start", "Зарегистрироваться"):
        if trader and trader.user:
            if not trader.user.is_active:
                send_login(trader)
                return True
            send(chat_id, "Вы уже зарегистрированы. /balance — баланс, /markets — рынки, /login — вход на сайт.")
        elif command == "/start":
            send(chat_id, "Добро пожаловать! Создайте аккаунт прямо здесь. Если аккаунт на сайте уже есть, привяжите Telegram в его профиле.",
                 reply_markup={"keyboard": [[{"text": "Зарегистрироваться"}]], "resize_keyboard": True, "one_time_keyboard": True})
        else:
            BotRegistration.objects.update_or_create(chat_id=chat_id, defaults={"name": "", "expires_at": timezone.now()+timedelta(minutes=15)})
            send(chat_id, "Как к вам обращаться? Введите имя или псевдоним (до 100 символов). /cancel — отмена.", reply_markup={"remove_keyboard": True})
        return True
    if command == "/login":
        if trader and trader.user:
            send_login(trader)
        else:
            send(chat_id, "Сначала зарегистрируйтесь: /register. Или привяжите существующий аккаунт на сайте.")
        return True
    state = BotRegistration.objects.filter(chat_id=chat_id).first()
    if not state or command.startswith("/"):
        return False
    if state.expires_at <= timezone.now():
        state.delete()
        send(chat_id, "Время регистрации истекло. Начните заново: /register.")
    elif not state.name:
        name = text.strip()
        if not name or len(name)>100:
            send(chat_id, "Введите имя от 1 до 100 символов.")
        else:
            state.name = name
            state.save(update_fields=["name"])
            send(chat_id, "Теперь введите электронную почту. Она сохранится в вашем аккаунте и не будет показана другим участникам.")
    else:
        trader, error = register_email(state, text.strip())
        if error:
            send(chat_id, error)
        else:
            from .email_verification import send_verification
            ok = send_verification(trader.user)
            send(chat_id, "Аккаунт создан! На почту отправлено письмо. Подтвердите адрес, затем отправьте /login." if ok else "Аккаунт создан, но письмо отправить не удалось. Администратору нужно настроить SMTP. /resend — повторить.")
            send_login(trader)
    return True
