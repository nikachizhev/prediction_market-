"""Notifications (spec E6.3): in-app inbox for everything, bot messages through the `BotMessage` outbox.

`enqueue` records a notification; the worker (`resolution.tick`) runs the detectors and `flush`,
which sends due bot messages honouring the user's switches, quiet hours and the daily cap.
Texts follow docs/mockups/Notifications.dc.html.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from . import telegram
from .access import can_access
from .models import NOTIFY_KINDS, BotMessage, MarketSubscription, Notification, Position, Question, Trade, Trader

log = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")
KINDS = [kind for kind, _ in NOTIFY_KINDS]
# Sent even over the daily cap: the outcome of a market and the payout.
MANDATORY_KINDS = ("results",)
MAX_ATTEMPTS = 5

# Filters of the «Уведомления» screen. Everything else (creator events, outcome asks, groups) is «Мои рынки».
LEASE = timedelta(minutes=5)
PAY_KINDS = ("results", "referral", "daily", "insurance", "market_result")
MARKETS_KINDS = ("moves", "closing", "market_change", "market_invite")


def group_of(kind: str) -> str:
    if kind in PAY_KINDS:
        return "pay"
    if kind in MARKETS_KINDS:
        return "markets"
    return "mine"


def kind_enabled(trader: Trader, kind: str) -> bool:
    """Bot switch from the profile; a missing key means «on»."""
    return (trader.notify_prefs or {}).get(kind, True) is not False


def notify_settings(trader: Trader) -> dict[str, bool]:
    return {kind: kind_enabled(trader, kind) for kind in KINDS}


def market_button(question: Question) -> dict:
    from .bot_resolution import app_button
    return {"inline_keyboard": [[app_button("Открыть рынок", f"market/{question.pk}")]]}


def coins(value: Decimal) -> str:
    """218 or 218,5 — as on the screens."""
    value = Decimal(value).quantize(Decimal("0.01"))
    return f"{value.normalize():f}".replace(".", ",")


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def short(text: str, limit: int = 100) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _join(title: str, text: str) -> str:
    if not title:
        return text
    # «Снег до 1 декабря: вероятность…» reads as one sentence; «Ты угадал! +218» stands on its own line.
    return f"{title}\n{text}" if title[-1].isdigit() or title[-1] in ".!?" else f"{title} {text}"


def enqueue(trader: Trader, kind: str, key: str, title: str, text: str, *, question: Question | None = None,
            payload: dict | None = None, bot_text: str | None = None, now: datetime | None = None):
    """Inbox notice always; a bot message too if the user has Telegram and this kind is on. Idempotent by `key`."""
    notice, created = Notification.objects.get_or_create(key=key, defaults={
        "recipient": trader, "kind": kind, "title": title[:120], "text": text[:500], "question": question})
    if not created and notice.kind != kind:
        # An older plain notice under the same key (e.g. market_finished) becomes the full one.
        notice.kind, notice.title, notice.text = kind, title[:120], text[:500]
        notice.save(update_fields=["kind", "title", "text"])
    message = None
    if trader.telegram_chat_id and kind_enabled(trader, kind):
        message, _ = BotMessage.objects.get_or_create(key=key, defaults={
            "trader": trader, "kind": kind, "text": bot_text or _join(title, text), "payload": payload or {},
            "question": question, "send_after": now or timezone.now()})
    return notice, message


# --- sending -----------------------------------------------------------------------------------

def _quiet_until(now: datetime) -> datetime | None:
    """End of the quiet hours if `now` falls inside them (Moscow time)."""
    start, end = settings.NOTIFY_QUIET_HOURS
    local = timezone.localtime(now, MSK)
    if start <= local.hour or local.hour < end:
        day = local.date() + timedelta(days=1) if local.hour >= start else local.date()
        return datetime.combine(day, time(end), tzinfo=MSK)
    return None


def _day_bounds(now: datetime) -> tuple[datetime, datetime]:
    day = timezone.localtime(now, MSK).date()
    start = datetime.combine(day, time(0), tzinfo=MSK)
    return start, start + timedelta(days=1)


def sent_today(trader_id: int, now: datetime) -> int:
    start, end = _day_bounds(now)
    return BotMessage.objects.filter(trader_id=trader_id, status=BotMessage.Status.SENT,
                                     sent_at__gte=start, sent_at__lt=end).count()


def _finish(message: BotMessage, status: str, now: datetime | None = None) -> None:
    message.status = status
    message.sent_at = now
    message.save(update_fields=["status", "sent_at", "attempts", "send_after"])


def _claim(message: BotMessage, now: datetime) -> bool:
    """Lease a queued row so a second worker process cannot send it too; released by _finish or a retry."""
    claimed = BotMessage.objects.filter(pk=message.pk, status=BotMessage.Status.QUEUED,
                                        send_after=message.send_after).update(send_after=now + LEASE)
    if claimed:
        message.send_after = now + LEASE
    return bool(claimed)


def flush(now: datetime | None = None) -> dict:
    """Send due queued bot messages. Telegram errors are retried on later ticks, never raised."""
    now = now or timezone.now()
    done = {"sent": 0, "skipped": 0, "failed": 0, "postponed": 0}
    due = BotMessage.objects.filter(status=BotMessage.Status.QUEUED, send_after__lte=now)
    wake = _quiet_until(now)
    if wake is not None:
        done["postponed"] = due.update(send_after=wake)
        return done
    for message in due.select_related("trader", "question").order_by("send_after", "id"):
        if not _claim(message, now):
            continue
        trader = message.trader
        if (not trader.telegram_chat_id or not kind_enabled(trader, message.kind)
                or (message.question and not can_access(trader, message.question))):
            _finish(message, BotMessage.Status.SKIPPED)
            done["skipped"] += 1
            continue
        if message.kind not in MANDATORY_KINDS and sent_today(trader.pk, now) >= settings.NOTIFY_DAILY_CAP:
            # Over the cap optional notices are dropped, not piled up for tomorrow.
            _finish(message, BotMessage.Status.SKIPPED)
            done["skipped"] += 1
            continue
        try:
            telegram.send(trader.telegram_chat_id, message.text, **(message.payload or {}))
        except Exception:  # noqa: BLE001 - Telegram may be down; retried next tick
            log.warning("bot message %s to trader %s failed", message.pk, trader.pk, exc_info=True)
            message.attempts += 1
            if message.attempts >= MAX_ATTEMPTS:
                _finish(message, BotMessage.Status.FAILED)
                done["failed"] += 1
            else:
                message.send_after = now + timedelta(minutes=message.attempts)
                message.save(update_fields=["attempts", "send_after"])
            continue
        _finish(message, BotMessage.Status.SENT, now)
        done["sent"] += 1
    return done


# --- detectors run by the worker ---------------------------------------------------------------

def _position_text(trader: Trader, question: Question) -> str:
    pos = Position.objects.filter(trader=trader, question=question).first()
    if not pos:
        return ""
    sides = [f"{label} на {coins(spent)}" for label, shares, spent in
             (("ДА", pos.yes_shares, pos.yes_spent), ("НЕТ", pos.no_shares, pos.no_spent)) if shares > 0]
    return f" У тебя {' и '.join(sides)}." if sides else ""


def _open_markets(now: datetime):
    return Question.objects.filter(kind=Question.Kind.LMSR, status=Question.Status.OPEN, hidden_at__isnull=True).filter(
        Q(deadline__isnull=True) | Q(deadline__gt=now))


def detect_moves(now: datetime | None = None) -> int:
    """Tracked market moved NOTIFY_MOVE_POINTS+ p.p. in 24 h: one notice per user, market and Moscow day."""
    now = now or timezone.now()
    since = now - timedelta(hours=24)
    day = timezone.localtime(now, MSK).date().isoformat()
    created = 0
    markets = _open_markets(now).filter(pk__in=Trade.objects.filter(created_at__gte=since).values("question_id"))
    for question in markets:
        before = (Trade.objects.filter(question=question, created_at__lt=since).order_by("created_at", "id")
                  .values_list("prob_after", flat=True).last())
        if before is None:
            before = question.initial_prob
        current = question.prob
        points = round(abs(current - before) * 100, 6)
        if points < settings.NOTIFY_MOVE_POINTS:
            continue
        verb, arrow = ("выросла", "▲") if current > before else ("упала", "▼")
        for sub in MarketSubscription.objects.filter(question=question).select_related("trader"):
            trader = sub.trader
            key = f"moves:{question.pk}:{trader.pk}:{day}"
            if Notification.objects.filter(key=key).exists() or not can_access(trader, question):
                continue
            text = (f"вероятность {verb} до {round(current * 100)}% ({arrow} {round(points)} за сутки)."
                    + _position_text(trader, question))
            enqueue(trader, "moves", key, f"{short(question.title)}:", text, question=question,
                    payload={"reply_markup": market_button(question)}, now=now)
            created += 1
    return created


def detect_closing(now: datetime | None = None) -> int:
    """Betting ends within NOTIFY_CLOSING_HOURS: once per user and market, for followers and holders, not the creator."""
    now = now or timezone.now()
    window = now + timedelta(hours=settings.NOTIFY_CLOSING_HOURS)
    created = 0
    for question in _open_markets(now).filter(deadline__lte=window):
        ids = set(MarketSubscription.objects.filter(question=question).values_list("trader_id", flat=True))
        ids.update(Position.objects.filter(question=question).filter(Q(yes_shares__gt=0) | Q(no_shares__gt=0))
                   .values_list("trader_id", flat=True))
        ids.discard(question.creator_id)
        hours = max(1, math.ceil((question.deadline - now).total_seconds() / 3600))
        title = f"Через {hours} {plural(hours, 'час', 'часа', 'часов')}"
        for trader in Trader.objects.filter(pk__in=ids):
            key = f"closing:{question.pk}:{trader.pk}"
            if Notification.objects.filter(key=key).exists() or not can_access(trader, question):
                continue
            enqueue(trader, "closing", key, title, f"закрывается приём прогнозов: «{question.title}»",
                    question=question, payload={"reply_markup": market_button(question)}, now=now)
            created += 1
    return created


# --- events from services (called via transaction.on_commit) ---------------------------------

def dispute_opened(question_id: int) -> None:
    question = Question.objects.select_related("creator").get(pk=question_id)
    if not question.creator:
        return
    enqueue(question.creator, "creator", f"dispute-opened:{question.pk}", "Итог оспорили:",
            f"«{question.title}» — модератор разберёт спор.", question=question,
            payload={"reply_markup": market_button(question)})


def moderator_decided(question_id: int, decision: str, upheld: bool) -> None:
    question = Question.objects.select_related("creator").get(pk=question_id)
    if not question.creator:
        return
    if decision == "cancel":
        verdict = "рынок отменён"
    else:
        verdict = f"итог {'ДА' if decision == 'yes' else 'НЕТ'}"
    if upheld:
        text = f"«{question.title}» — спор признан: {verdict}. Монеты за создание рынка не возвращаются."
    else:
        text = f"«{question.title}» — спор отклонён: {verdict}, как ты и указал."
    enqueue(question.creator, "creator", f"moderator:{question.pk}", "Решение модератора:", text,
            question=question, payload={"reply_markup": market_button(question)})


def referral_granted(friend_id: int, referrer_id: int) -> None:
    friend, referrer = Trader.objects.get(pk=friend_id), Trader.objects.get(pk=referrer_id)
    n = settings.REFERRAL_PREDICTIONS
    reward = coins(Decimal(settings.REFERRAL_REWARD))
    predictions = f"{n} {plural(n, 'прогноз', 'прогноза', 'прогнозов')}"
    enqueue(referrer, "referral", f"referral:{friend.pk}:referrer", f"{friend.display_name} сделал {predictions} —",
            f"вам обоим по +{reward} монет за приглашение.")
    enqueue(friend, "referral", f"referral:{friend.pk}:friend", f"Ты сделал {predictions} —",
            f"тебе и {referrer.display_name} по +{reward} монет за приглашение.")
