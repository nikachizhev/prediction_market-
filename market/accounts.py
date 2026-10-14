from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render

from .forms import RegistrationForm
from .models import Trader
from .email_verification import send_verification, mark_pending


def register(request):
    if request.user.is_authenticated:
        return redirect("market:markets")
    form = RegistrationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save(commit=False)
            user.is_active = False
            user.save()
            mark_pending(user)
            Trader.objects.create(user=user, name=user.first_name or user.username, pseudonym=user.username)
        if send_verification(user):
            messages.success(request, "Письмо отправлено. Подтвердите почту, чтобы войти.")
        else:
            messages.error(request, "Аккаунт создан, но письмо отправить не удалось. Проверьте настройки почты и запросите отправку повторно.")
        return redirect("market:email_pending")
    return render(request, "market/auth.html", {"form": form, "register": True})
