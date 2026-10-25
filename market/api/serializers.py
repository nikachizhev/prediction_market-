"""Plain-dict serializers for the Mini App API (shapes are documented in docs/api.md)."""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Decimal

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.utils import timezone

from ..models import CATEGORIES, MarketSubscription, Position, Question, Trade, Trader

CENT = Decimal("0.01")


def money(value) -> str:
    return f"{Decimal(value or 0).quantize(CENT, rounding=ROUND_FLOOR):.2f}"


def shares(value) -> str:
    return f"{Decimal(value or 0):.6f}"


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def market_status(question: Question) -> str:
    if question.status == Question.Status.ANNULLED:
        return "annulled"
    if question.status == Question.Status.RESOLVED:
        return "resolved"
    resolution = getattr(question, "resolution_state", None)
    if resolution in ("pending", "disputed"):
        return resolution
    return "open" if question.accepting_entries else "closed"


def referral_link(trader: Trader) -> str:
    return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}/{settings.TELEGRAM_APP_NAME}?startapp=ref_{trader.referral_code}"


def limits() -> dict:
    return {
        "max_trade": money(settings.MAX_TRADE), "market_cap": money(settings.MARKET_CAP),
        "fee_rate": float(Decimal(settings.TRADE_FEE)), "creation_cost": money(settings.MARKET_CREATION_COST),
        "dispute_cost": money(settings.DISPUTE_COST), "b": settings.LMSR_DEFAULT_B,
    }


def me(trader: Trader) -> dict:
    open_positions = Position.objects.filter(
        trader=trader, spent__gt=0, question__status__in=[Question.Status.OPEN, Question.Status.CLOSED])
    return {
        "id": trader.pk, "name": trader.name, "pseudonym": trader.pseudonym,
        "show_real_name": trader.show_real_name, "display_name": trader.display_name,
        "photo_url": trader.photo_url or None, "balance": money(trader.balance),
        "onboarded": trader.onboarded_at is not None,
        "referral_link": referral_link(trader),
        "referred_by": trader.referred_by.display_name if trader.referred_by_id else None,
        "invested": {str(p.question_id): money(p.spent) for p in open_positions},
        "limits": limits(),
    }


class History:
    """Probability over time from trades; one query per market."""

    def __init__(self, question: Question, since: datetime | None = None):
        trades = Trade.objects.filter(question=question).order_by("created_at", "id")
        start_prob = question.initial_prob
        if since is not None:
            before = trades.filter(created_at__lt=since).values_list("prob_after", flat=True).last()
            if before is not None:
                start_prob = before
            trades = trades.filter(created_at__gte=since)
        rows = list(trades.values_list("created_at", "prob_after"))
        self.start_prob = start_prob
        self.times = [t for t, _ in rows]
        self.probs = [p for _, p in rows]
        self.current = question.prob

    def at(self, moment: datetime) -> float:
        i = bisect_right(self.times, moment)
        return self.probs[i - 1] if i else self.start_prob

    def points(self, since: datetime, until: datetime, limit: int = 200) -> list[dict]:
        pts = [(since, self.at(since))] + [(t, p) for t, p in zip(self.times, self.probs) if since <= t <= until]
        pts.append((until, self.current))
        if len(pts) > limit:
            step = len(pts) / limit
            pts = [pts[int(i * step)] for i in range(limit - 1)] + [pts[-1]]
        return [{"t": iso(t), "p": round(p, 4)} for t, p in pts]


def card(question: Question, *, starred: set[int] | None = None, now: datetime | None = None) -> dict:
    now = now or timezone.now()
    history = History(question, since=now - timedelta(days=7))
    spark = [history.at(now - timedelta(days=6 - i)) for i in range(6)] + [question.prob]
    stats = Trade.objects.filter(question=question).aggregate(
        volume=Sum("cost", filter=Q(cost__gt=0)), traders=Count("trader", distinct=True))
    return {
        "id": question.pk, "title": question.title, "category": question.category,
        "prob": round(question.prob, 4),
        "change_24h": round((question.prob - history.at(now - timedelta(days=1))) * 100, 1),
        "spark": [round(p, 4) for p in spark],
        "participants": stats["traders"] or 0, "volume": money(stats["volume"]),
        "closes_at": iso(question.deadline), "status": market_status(question),
        "outcome": question.outcome, "starred": question.pk in (starred or set()),
    }


def starred_ids(trader: Trader) -> set[int]:
    return set(MarketSubscription.objects.filter(trader=trader).values_list("question_id", flat=True))


def position(pos: Position | None, question: Question) -> dict | None:
    if pos is None or (pos.yes_shares <= 0 and pos.no_shares <= 0 and pos.spent <= 0):
        return None
    p = question.prob
    value = Decimal(repr(float(pos.yes_shares) * p + float(pos.no_shares) * (1 - p)))
    return {
        "yes_shares": shares(pos.yes_shares), "no_shares": shares(pos.no_shares), "spent": money(pos.spent),
        "value": money(value), "payout_if_yes": money(pos.yes_shares), "payout_if_no": money(pos.no_shares),
    }


def author(trader: Trader | None) -> dict:
    if trader is None:
        return {"display_name": "Команда", "photo_url": None, "resolved": 0, "disputes_lost": 0}
    resolved = trader.created_questions.filter(status=Question.Status.RESOLVED).count()
    return {"display_name": trader.display_name, "photo_url": trader.photo_url or None,
            "resolved": resolved, "disputes_lost": trader.created_questions.filter(creator_at_fault=True).count()}


def resolution(question: Question, trader: Trader) -> dict | None:
    from ..resolution import public_state
    return public_state(question, trader)


def detail(question: Question, trader: Trader) -> dict:
    from .. import services
    data = card(question, starred=starred_ids(trader))
    pos = Position.objects.filter(trader=trader, question=question).first()
    is_creator = question.creator_id == trader.pk
    trades = Trade.objects.filter(question=question).order_by("-created_at", "-id")[:20]
    data.update({
        "criteria": question.resolution_criteria, "resolves_at": iso(question.resolves_at),
        "created_at": iso(question.created_at), "author": author(question.creator),
        "is_creator": is_creator,
        "can_trade": (question.accepting_entries and not question.hidden_at and not is_creator
                      and not question.conflicted_traders.filter(pk=trader.pk).exists()),
        "max_amount": money(services.max_buy_amount(trader, question)),
        "my_position": position(pos, question),
        # Anonymous: no trader, only side, size and the price it moved to.
        "trades": [{"side": t.side, "amount": money(abs(t.cost)), "prob_after": round(t.prob_after, 4),
                    "at": iso(t.created_at), "sell": t.cost < 0} for t in trades],
        "resolution": resolution(question, trader),
    })
    return data


def categories() -> list[dict]:
    return [{"slug": slug, "label": label} for slug, label in CATEGORIES]
