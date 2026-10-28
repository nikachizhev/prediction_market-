"""AI rules helper for the market creation wizard (spec E9.1)."""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from .models import Event, Trader

MSK = ZoneInfo("Europe/Moscow")
EVENT = "assist_request"


def enabled() -> bool:
    """The helper is on for everyone: switched on and a key is set on the server."""
    return bool(settings.ASSIST_ENABLED and settings.LLM_API_KEY)


def used_today(trader: Trader) -> int:
    """Requests that count against the daily limit: every `assist_request` of this Moscow day but refusals by the limit."""
    day_start = datetime.combine(timezone.localdate(timezone=MSK), time.min, tzinfo=MSK)
    return (Event.objects.filter(trader=trader, name=EVENT, created_at__gte=day_start)
            .exclude(props__result="limit").count())


def left(trader: Trader) -> int:
    return max(0, settings.ASSIST_DAILY_LIMIT - used_today(trader))


def state(trader: Trader) -> dict:
    """`Me.assist` in the API."""
    return {"enabled": enabled(), "left": left(trader)}
