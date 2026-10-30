"""Mini App rating and personal stats. Bonus coins never count as profit."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Q, Sum
from django.utils import timezone

from . import services
from .models import LedgerEntry, Position, Question, Trade, Trader

PERIODS = {"week": timedelta(days=7), "month": timedelta(days=30)}
TOP = 100


def _since(period: str):
    return timezone.now() - PERIODS[period] if period in PERIODS else None


def realized_profit(trader: Trader, since, group=None) -> Decimal:
    """Cash result of markets settled since `since`: stakes, sales, payouts, creator income.

    With `group` only that chat's markets count (spec E8.3); `since=None` then means all time.
    """
    entries = trader.ledger.exclude(kind__in=LedgerEntry.BONUS_KINDS)
    entries = entries.filter(question__resolved_at__gte=since) if since is not None else \
        entries.filter(question__resolved_at__isnull=False)
    if group is not None:
        entries = entries.filter(question__group=group)
    total = entries.aggregate(t=Sum("amount"))["t"]
    return total or Decimal(0)


def accuracy(trader: Trader, since=None, group=None) -> tuple[int, int]:
    """(correct, evaluated) over resolved markets where the trader leaned to one side."""
    positions = Position.objects.filter(trader=trader, question__status=Question.Status.RESOLVED,
                                        question__kind=Question.Kind.LMSR).select_related("question")
    if since is not None:
        positions = positions.filter(question__resolved_at__gte=since)
    if group is not None:
        positions = positions.filter(question__group=group)
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


def chat_members(group):
    return Trader.objects.filter(memberships__group=group, memberships__status="active")


def leaderboard(me: Trader | None, *, scope: str = "all", by: str = "profit", period: str = "all",
                group=None) -> dict:
    """Rows carry only aggregates: rank, value, resolved predictions, accuracy and profit.

    With `group` (a closed chat, spec E8.3) the board lists the chat's members and counts only the
    chat's markets; `me` may be None (the bot's /top).
    """
    since = _since(period)
    if group is not None:
        people = chat_members(group)
    else:
        people = friends(me) if scope == "friends" else participants()
    # Among friends and in a chat everyone with a result is shown; the overall accuracy board needs a track record.
    min_resolved = 1 if scope == "friends" or group is not None else max(1, settings.RATING_ACCURACY_MIN_RESOLVED)
    rows = []
    for t in people:
        correct, evaluated = accuracy(t, since, group)
        share = correct / evaluated if evaluated else None
        if group is not None:
            profit = realized_profit(t, since, group)
        else:
            profit = services.profit(t) if since is None else realized_profit(t, since)
        if by == "accuracy":
            if evaluated < min_resolved:
                continue
            rows.append((share, evaluated, t, evaluated, share, profit))
        else:
            rows.append((profit, 0, t, evaluated, share, profit))
    rows.sort(key=lambda r: (-r[0], -r[1], r[2].pk))
    items, mine = [], None
    for rank, (value, _, t, evaluated, share, profit) in enumerate(rows, start=1):
        shown = f"{value:.4f}" if by == "accuracy" else f"{value:.2f}"
        row = {"rank": rank, "value": shown, "predictions": evaluated,
               "accuracy": round(share, 4) if share is not None else None, "profit": f"{profit:.2f}"}
        is_me = me is not None and t.pk == me.pk
        if is_me:
            mine = row
        if rank <= TOP:
            items.append({**row, "display_name": t.display_name, "photo_url": t.photo_url or None,
                          "is_me": is_me})
    return {"items": items, "me": mine,
            "min_resolved": min_resolved if group is not None else settings.RATING_ACCURACY_MIN_RESOLVED}


def chat_rank(me: Trader, group) -> int | None:
    """My place on `leaderboard(me, group=group)` by all-time profit, without building the whole board.

    Same order: profit on the chat's settled markets, ties by registration. None for someone who is not an
    active member or has no settled prediction and no result in the chat yet.
    """
    members = list(chat_members(group).values_list("pk", flat=True))
    if me.pk not in members:
        return None
    profits = dict(LedgerEntry.objects.filter(trader__memberships__group=group, trader__memberships__status="active",
                                              question__group=group, question__resolved_at__isnull=False)
                   .exclude(kind__in=LedgerEntry.BONUS_KINDS)
                   .values("trader").annotate(total=Sum("amount")).values_list("trader", "total"))
    mine = profits.get(me.pk) or Decimal(0)
    if mine == 0 and not accuracy(me, None, group)[1]:
        return None
    ahead = sum(1 for pk in members if (profits.get(pk) or Decimal(0), -pk) > (mine, -me.pk))
    return ahead + 1


def trader_stats(trader: Trader) -> dict:
    correct, evaluated = accuracy(trader)
    place = leaderboard(trader)["me"]
    return {
        "profit": f"{services.profit(trader):.2f}",
        "accuracy": round(correct / evaluated, 4) if evaluated else None,
        "correct": correct, "evaluated": evaluated,
        "rank": place["rank"] if place else None,
        "predictions": Trade.objects.filter(trader=trader, cost__gt=0).count(),
        "markets_created": trader.created_questions.count(),
        "disputes_lost": trader.created_questions.filter(creator_at_fault=True).count(),
    }
