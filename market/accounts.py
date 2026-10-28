from django.contrib import messages
from django.conf import settings
from django.contrib.auth.views import PasswordResetView
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from urllib.parse import urlsplit
from smtplib import SMTPException

from .forms import RegistrationForm, ProfileForm
from .models import Trader
from .email_verification import send_verification, mark_pending


def profile(request):
    from .identity import get_trader
    trader = get_trader(request)
    if not trader:
        return redirect("market:login")
    form = ProfileForm(request.POST if request.method == "POST" else None, initial={
        "name": trader.name, "pseudonym": trader.pseudonym,
        "show_real_name": trader.show_real_name, "github_login": trader.github_login,
        **{field: getattr(trader, field) for field in ("birth_date", "city", "workplace", "education", "bio")},
    })
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            for field in ("name", "pseudonym", "show_real_name", "birth_date", "city", "workplace", "education", "bio"):
                setattr(trader, field, form.cleaned_data[field])
            trader.github_login = form.cleaned_data["github_login"] or None
            trader.save(update_fields=["name", "pseudonym", "show_real_name", "github_login", "birth_date", "city", "workplace", "education", "bio"])
            request.user.first_name = form.cleaned_data["name"]
            request.user.save(update_fields=["first_name"])
        messages.success(request, "Профиль сохранён.")
        return redirect("market:profile")
    return render(request, "market/profile.html", {"form": form, "trader": trader})


class ProjectPasswordResetView(PasswordResetView):
    def form_valid(self, form):
        site = urlsplit(settings.SITE_URL)
        try:
            form.save(
                use_https=site.scheme == "https",
                domain_override=site.netloc,
                from_email=settings.DEFAULT_FROM_EMAIL,
                email_template_name=self.email_template_name,
                subject_template_name=self.subject_template_name,
                request=self.request,
            )
        except (SMTPException, OSError):
            # Do not reveal whether this address belongs to an account.
            messages.error(self.request, "Не удалось обработать запрос. Попробуйте позже.")
            return self.form_invalid(form)
        return HttpResponseRedirect(self.get_success_url())


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
