"""Mini App rating and personal stats. Bonus coins never count as profit."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from . import services
from .models import LedgerEntry, Position, Question, Trade, Trader

PERIODS = {"week": timedelta(days=7), "month": timedelta(days=30)}
TOP = 100


def _since(period: str):
    return timezone.now() - PERIODS[period] if period in PERIODS else None


def realized_profit(trader: Trader, since) -> Decimal:
    """Cash result of markets settled since `since`: stakes, sales, payouts, creator income."""
    total = trader.ledger.filter(question__resolved_at__gte=since).exclude(
        kind__in=LedgerEntry.BONUS_KINDS).aggregate(t=Sum("amount"))["t"]
    return total or Decimal(0)


def accuracy(trader: Trader, since=None) -> tuple[int, int]:
    """(correct, evaluated) over resolved markets where the trader leaned to one side."""
    positions = Position.objects.filter(trader=trader, question__status=Question.Status.RESOLVED,
                                        question__kind=Question.Kind.LMSR).select_related("question")
    if since is not None:
        positions = positions.filter(question__resolved_at__gte=since)
    correct = evaluated = 0
    for p in positions:
        if p.yes_shares == p.no_shares:
            continue
        evaluated += 1
        correct += ("yes" if p.yes_shares > p.no_shares else "no") == p.question.outcome
    return correct, evaluated


def friends(trader: Trader):
    """People I invited and the one who invited me (spec E2, «Рейтинг»)."""
    ids = set(trader.referrals.values_list("pk", flat=True)) | {trader.pk}
    if trader.referred_by_id:
        ids.add(trader.referred_by_id)
    return Trader.objects.filter(pk__in=ids)


def participants():
    return Trader.objects.filter(
        Q(telegram_chat_id__isnull=False) | Q(positions__isnull=False) | Q(created_questions__isnull=False)).distinct()


def leaderboard(me: Trader, *, scope: str = "all", by: str = "profit", period: str = "all") -> dict:
    since = _since(period)
    people = friends(me) if scope == "friends" else participants()
    rows = []
    for t in people:
        if by == "accuracy":
            correct, evaluated = accuracy(t, since)
            if not evaluated:
                continue
            rows.append((correct / evaluated, evaluated, t))
        else:
            value = services.profit(t) if since is None else realized_profit(t, since)
            rows.append((value, 0, t))
    rows.sort(key=lambda r: (-r[0], -r[1], r[2].pk))
    items, mine = [], None
    for rank, (value, _, t) in enumerate(rows, start=1):
        shown = f"{value:.4f}" if by == "accuracy" else f"{value:.2f}"
        if t.pk == me.pk:
            mine = {"rank": rank, "value": shown}
        if rank <= TOP:
            items.append({"rank": rank, "display_name": t.display_name, "photo_url": t.photo_url or None,
                          "value": shown, "is_me": t.pk == me.pk})
    return {"items": items, "me": mine}


def trader_stats(trader: Trader) -> dict:
    correct, evaluated = accuracy(trader)
    return {
        "profit": f"{services.profit(trader):.2f}",
        "accuracy": round(correct / evaluated, 4) if evaluated else None,
        "predictions": Trade.objects.filter(trader=trader, cost__gt=0).count(),
        "markets_created": trader.created_questions.count(),
    }
