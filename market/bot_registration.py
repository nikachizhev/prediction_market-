"""One-time website login links; the web page market:telegram_site_login consumes them."""
import secrets
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import TelegramSiteLink
from .telegram_auth import digest


@transaction.atomic
def site_link(trader):
    token = secrets.token_urlsafe(32)
    TelegramSiteLink.objects.update_or_create(trader=trader, defaults={
        "token_hash": digest(token), "expires_at": timezone.now()+timedelta(minutes=10),
    })
    # Fragment stays out of HTTP request/access logs. The landing page submits it via POST.
    return f"{settings.SITE_URL}/auth/telegram/link/#{token}"
