import hashlib
import secrets
import logging
from datetime import timedelta
from smtplib import SMTPException
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import transaction
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from .models import EmailVerification

log = logging.getLogger(__name__)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def mark_pending(user):
    EmailVerification.objects.create(user=user, email=user.email,
        token_hash=digest(secrets.token_urlsafe(32)), expires_at=timezone.now())


@transaction.atomic
def send_verification(user):
    user = User.objects.select_for_update().get(pk=user.pk)
    previous = EmailVerification.objects.filter(user=user).first()
    if not previous or previous.verified_at or user.is_active:
        return False
    if previous and previous.sent_at and previous.sent_at > timezone.now()-timedelta(seconds=60):
        return True
    token = secrets.token_urlsafe(32)
    url = settings.SITE_URL + reverse("market:verify_email", args=[token])
    try:
        count = send_mail(
            "Подтвердите почту — Рынок прогнозов",
            f"Здравствуйте!\n\nДля подтверждения почты откройте ссылку и нажмите кнопку:\n{url}\n\nСсылка действует 24 часа и используется один раз. Если вы не регистрировались, проигнорируйте письмо.",
            settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False,
        )
        if count != 1:
            return False
    except (SMTPException, OSError, ValueError):
        log.warning("Email verification delivery failed for user id=%s", user.pk)
        return False
    EmailVerification.objects.update_or_create(user=user, defaults={
        "token_hash": digest(token), "email": user.email,
        "expires_at": timezone.now()+timedelta(hours=24), "sent_at": timezone.now(), "verified_at": None,
    })
    return True


class ResendForm(forms.Form):
    email = forms.EmailField(label="Электронная почта")


def pending(request):
    form = ResendForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        user = User.objects.filter(email__iexact=form.cleaned_data["email"], is_active=False).first()
        if user:
            send_verification(user)
        messages.info(request, "Если аккаунт ожидает подтверждения, письмо будет отправлено. Повторная отправка доступна раз в минуту.")
        return redirect("market:email_pending")
    return render(request, "market/email_pending.html", {"form": form})


@transaction.atomic
def verify(request, token):
    verification = EmailVerification.objects.select_for_update().filter(token_hash=digest(token), verified_at__isnull=True, expires_at__gt=timezone.now()).first()
    valid = bool(verification)
    if verification:
        user = User.objects.select_for_update().get(pk=verification.user_id)
        valid = user.email == verification.email
        if request.method == "POST" and valid:
            verification.verified_at = timezone.now()
            verification.save(update_fields=["verified_at"])
            user.is_active = True
            user.save(update_fields=["is_active"])
            messages.success(request, "Почта подтверждена! Теперь войдите по паролю или через /login в Telegram-боте.")
            return redirect("market:login")
    response = render(request, "market/verify_email.html", {"valid": valid})
    response["Cache-Control"] = "no-store"
    # Preserve the origin for same-site form POSTs without leaking the token externally.
    response["Referrer-Policy"] = "same-origin"
    return response
