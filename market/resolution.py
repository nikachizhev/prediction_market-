"""Resolution deadlines (spec E4): what the worker does every minute. Money moves only via services."""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import services
from .models import Question, Trader

log = logging.getLogger(__name__)

# Reminders go out this many hours after resolves_at; the market is cancelled at RESOLUTION_TIMEOUT_HOURS.
REMINDER_HOURS = (24, 48)


def public_state(question: Question, trader: Trader) -> dict | None:
    """What the market card shows about the creator's stated outcome."""
    if not question.resolution_state:
        return None
    from .models import Dispute
    return {
        "proposed_outcome": question.proposed_outcome or None,
        "evidence_url": question.evidence.url if question.evidence else None,
        "note": question.evidence_note,
        "dispute_until": question.dispute_until.isoformat() if question.dispute_until else None,
        "can_dispute": (question.resolution_state in (Question.Resolution.PENDING, Question.Resolution.DISPUTED)
                        and question.dispute_until is not None and question.dispute_until > timezone.now()
                        and question.creator_id != trader.pk
                        and question.trades.filter(trader=trader).exists()
                        and not Dispute.objects.filter(question=question, trader=trader).exists()),
    }


def _bot():
    from . import bot_resolution
    return bot_resolution


def _safe(action, question: Question, *args) -> bool:
    """Telegram failures must not stop the worker or roll back money; the step is retried next tick."""
    try:
        action(question, *args)
        return True
    except Exception:  # noqa: BLE001 - logged and retried
        log.exception("bot step %s failed for market %s", getattr(action, "__name__", action), question.pk)
        return False


def user_markets():
    return Question.objects.filter(kind=Question.Kind.LMSR, creator__isnull=False, resolves_at__isnull=False)


def tick(now=None) -> dict:
    now = now or timezone.now()
    done = {"closed": 0, "asked": 0, "reminded": 0, "cancelled": 0, "finalized": 0, "results": 0}
    bot = _bot()

    for q in user_markets().filter(status=Question.Status.OPEN, deadline__lte=now):
        services.close_betting(q)
        done["closed"] += 1

    waiting = user_markets().filter(status=Question.Status.CLOSED, resolution_state="")
    for q in waiting.filter(asked_at__isnull=True, resolves_at__lte=now):
        if _safe(bot.ask_creator, q):
            Question.objects.filter(pk=q.pk).update(asked_at=now)
            done["asked"] += 1

    timeout = timedelta(hours=settings.RESOLUTION_TIMEOUT_HOURS)
    for q in waiting.filter(resolves_at__lte=now - timeout):
        try:
            services.cancel_unresolved(q)
            done["cancelled"] += 1
        except services.MarketError:
            continue

    for number, hours in enumerate(REMINDER_HOURS, start=1):
        for q in waiting.filter(resolves_at__lte=now - timedelta(hours=hours), reminders_sent__lt=number,
                                asked_at__isnull=False):
            if _safe(bot.remind_creator, q, number):
                Question.objects.filter(pk=q.pk).update(reminders_sent=number)
                done["reminded"] += 1

    for q in Question.objects.filter(resolution_state=Question.Resolution.PENDING, dispute_until__lte=now):
        try:
            services.finalize_resolution(q)
            done["finalized"] += 1
        except services.MarketError:
            continue

    finished = user_markets().filter(resolution_state=Question.Resolution.FINAL, results_sent_at__isnull=True)
    for q in finished:
        if _safe(bot.send_results, q):
            Question.objects.filter(pk=q.pk).update(results_sent_at=now)
            done["results"] += 1
    return done
