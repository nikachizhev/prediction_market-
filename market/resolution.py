"""Resolution deadlines (spec E4): what the worker does every minute. Money moves only via services."""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import services
from .models import Question, Trader

log = logging.getLogger(__name__)

# Reminders go out this many hours after resolves_at; the market is cancelled at RESOLUTION_TIMEOUT_HOURS
# unless someone states the outcome. Participants are asked at PARTICIPANT_PROPOSAL_HOURS (with reminder 1).
REMINDER_HOURS = (24, 48)


def public_state(question: Question, trader: Trader) -> dict | None:
    """What the market card shows about the stated outcome (by the creator or, phase 3d, a participant)."""
    if not question.resolution_state:
        return None
    from django.db.models import Q
    from .models import Dispute, Trade
    root = question.parent or question
    participant = services.by_participant(root)
    if trader.pk == root.creator_id:
        may_dispute = services.not_by_creator(root)  # the creator may challenge a participant's or AI's outcome
    else:
        may_dispute = Trade.objects.filter(trader=trader).filter(Q(question=root) | Q(question__parent=root)).exists()
    return {
        "proposed_outcome": question.proposed_outcome or None,
        "evidence_url": root.evidence.url if root.evidence else None,
        "note": root.evidence_note,
        "dispute_until": root.dispute_until.isoformat() if root.dispute_until else None,
        "can_dispute": (root.resolution_state in (Question.Resolution.PENDING, Question.Resolution.DISPUTED)
                        and root.dispute_until is not None and root.dispute_until > timezone.now()
                        and services.outcome_author_id(root) != trader.pk
                        and may_dispute
                        and not Dispute.objects.filter(question=root, trader=trader).exists()),
        "by_participant": participant,
        "by_ai": root.proposed_by_ai,
    }


def _bot():
    if settings.OBSERVABILITY_DEMO:
        from . import demo_telegram
        return demo_telegram
    from . import bot_resolution
    return bot_resolution


def _safe(action, question: Question, *args) -> bool:
    """Telegram failures must not stop the worker or roll back money; the step is retried next tick."""
    try:
        action(question, *args)
        return True
    except Exception:  # noqa: BLE001 - logged and retried
        from .worker_metrics import record_telegram_failure
        record_telegram_failure(getattr(action, "__name__", "other"))
        log.exception("bot step %s failed for market %s", getattr(action, "__name__", action), question.pk)
        return False


def user_markets():
    return Question.objects.filter(
        kind=Question.Kind.LMSR, parent__isnull=True, creator__isnull=False,
        resolves_at__isnull=False, publication_state="approved",
    )


def tick(now=None) -> dict:
    now = now or timezone.now()
    from . import ai_resolution
    done = {"closed": 0, "asked": 0, "ai_stated": 0, "participants_asked": 0, "reminded": 0, "cancelled": 0, "finalized": 0,
            "evidence_reminded": 0, "results": 0,
            "moves": 0, "closing": 0, "sent": 0, "skipped": 0, "failed": 0, "postponed": 0}
    bot = _bot()

    for q in user_markets().filter(status=Question.Status.OPEN, deadline__lte=now):
        services.close_betting(q)
        done["closed"] += 1

    waiting = user_markets().filter(status=Question.Status.CLOSED, resolution_state="")
    for q in waiting.filter(asked_at__isnull=True, resolves_at__lte=now):
        if ai_resolution.waiting_for_ai(q, now):
            continue
        ask = getattr(bot, "ask_creator_ai", bot.ask_creator) if q.ai_state == "proposed" else bot.ask_creator
        if _safe(ask, q):
            Question.objects.filter(pk=q.pk).update(asked_at=now)
            done["asked"] += 1

    timeout = timedelta(hours=settings.RESOLUTION_TIMEOUT_HOURS)
    for q in waiting.filter(resolves_at__lte=now - timeout):
        try:
            services.cancel_unresolved(q)
            done["cancelled"] += 1
        except services.MarketError:
            continue

    since = now - timedelta(hours=settings.PARTICIPANT_PROPOSAL_HOURS)
    # A silent creator: AI's proposal goes into the dispute window; participants are asked only without one.
    for q in waiting.filter(ai_state="proposed", asked_at__isnull=False, resolves_at__lte=since):
        try:
            ai_resolution.state_silent_outcome(q)
            done["ai_stated"] += 1
        except services.MarketError:
            continue

    for q in waiting.filter(participants_asked_at__isnull=True, resolves_at__lte=since):
        if _safe(bot.ask_participants, q):
            Question.objects.filter(pk=q.pk).update(participants_asked_at=now)
            done["participants_asked"] += 1

    for number, hours in enumerate(REMINDER_HOURS, start=1):
        for q in waiting.filter(resolves_at__lte=now - timedelta(hours=hours), reminders_sent__lt=number,
                                asked_at__isnull=False):
            if _safe(bot.remind_creator, q, number):
                Question.objects.filter(pk=q.pk).update(reminders_sent=number)
                done["reminded"] += 1

    for q in Question.objects.filter(parent__isnull=True, resolution_state=Question.Resolution.PENDING,
                                     dispute_until__lte=now, publication_state="approved"):
        try:
            services.finalize_resolution(q)
            done["finalized"] += 1
        except services.MarketError:
            continue

    # A disputed outcome without proof: one reminder to its author halfway through EVIDENCE_HOURS.
    half = timedelta(hours=settings.EVIDENCE_HOURS) / 2
    for q in Question.objects.filter(parent__isnull=True, resolution_state=Question.Resolution.DISPUTED,
                                     evidence_requested_at__lte=now - half, evidence_reminded_at__isnull=True):
        if services.has_evidence(q) or now >= services.evidence_deadline(q):
            continue
        if _safe(bot.remind_evidence, q):
            Question.objects.filter(pk=q.pk).update(evidence_reminded_at=now)
            done["evidence_reminded"] += 1

    finished = user_markets().filter(resolution_state=Question.Resolution.FINAL, results_sent_at__isnull=True)
    for q in finished:
        if _safe(bot.send_results, q):
            Question.objects.filter(pk=q.pk).update(results_sent_at=now)
            done["results"] += 1

    # Notifications (spec E6.3): detectors fill the outbox, then due bot messages go out.
    from . import bot_notifications as notices
    for name, detect in (("moves", notices.detect_moves), ("closing", notices.detect_closing)):
        try:
            done[name] = detect(now)
        except Exception:  # noqa: BLE001 - a broken detector must not stop the worker
            log.exception("notification detector %s failed", name)
    try:
        done.update(notices.flush(now))
    except Exception:  # noqa: BLE001
        log.exception("bot outbox flush failed")

    # Closed chats (spec E8): disputes for chat admins, then the chats' own outbox.
    from . import bot_challenges, bot_chats, chat_disputes
    for name, step in (("challenges", bot_challenges.tick), ("chat_disputes", chat_disputes.tick), ("chats", bot_chats.tick)):
        try:
            for key, value in step(now).items():
                done[f"{name}_{key}"] = value
        except Exception:  # noqa: BLE001
            log.exception("%s step failed", name)
    return done
