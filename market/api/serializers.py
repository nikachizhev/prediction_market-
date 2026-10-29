"""Plain-dict serializers for the Mini App API (shapes are documented in docs/api.md)."""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Decimal

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .. import assist
from ..bot_notifications import group_of, notify_settings
from ..models import CATEGORIES, MarketSubscription, Position, Question, Trade, Trader
from ..notifications import visible_notifications

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
        "trade_limit_share": float(Decimal(settings.TRADE_LIMIT_SHARE)),
    }


def me(trader: Trader) -> dict:
    open_positions = Position.objects.filter(
        trader=trader, spent__gt=0, question__status__in=[Question.Status.OPEN, Question.Status.CLOSED])
    return {
        "id": trader.pk, "name": trader.name, "pseudonym": trader.pseudonym,
        "show_real_name": trader.show_real_name, "display_name": trader.display_name,
        "photo_url": trader.photo_url or None, "balance": money(trader.balance),
        "onboarded": trader.onboarded_at is not None,
        "interests": list(trader.interests or []),
        "referral_link": referral_link(trader),
        "referred_by": trader.referred_by.display_name if trader.referred_by_id else None,
        "invested": {str(p.question_id): money(p.spent) for p in open_positions},
        "limits": limits(),
        "notify": notify_settings(trader),
        "unread_notifications": visible_notifications(trader).filter(read_at__isnull=True).count(),
        "assist": assist.state(trader),
    }


def notice(item) -> dict:
    """One row of the «Уведомления» screen."""
    action = "resolve" if item.kind == "resolve" else ("market" if item.question_id else None)
    return {
        "id": item.pk, "kind": item.kind, "group": group_of(item.kind),
        "title": item.title, "text": item.text, "at": iso(item.created_at), "read": item.read_at is not None,
        "market": {"id": item.question_id, "title": item.question.title} if item.question_id else None,
        "action": action,
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


def my_sides(trader: Trader, question_ids: list[int]) -> dict[int, str]:
    """The side a trader leans to in each market (by shares held), for the feed's «Твой прогноз»."""
    sides = {}
    for pos in Position.objects.filter(trader=trader, question_id__in=question_ids):
        if pos.yes_shares > pos.no_shares:
            sides[pos.question_id] = "yes"
        elif pos.no_shares > pos.yes_shares:
            sides[pos.question_id] = "no"
    return sides


def trade_block(question: Question, trader: Trader) -> str | None:
    """Why this trader cannot buy here, in Russian; None when trading is allowed."""
    from .. import services
    if question.status == Question.Status.RESOLVED or question.status == Question.Status.ANNULLED:
        return "Рынок завершён."
    if question.hidden_at:
        return "Рынок скрыт до решения модератора."
    if not question.accepting_entries:
        return "Приём прогнозов закрыт."
    if question.creator_id == trader.pk:
        return "Создатель рынка не может делать прогнозы на свой рынок."
    if question.conflicted_traders.filter(pk=trader.pk).exists():
        return "Конфликт интересов: вы не можете ставить на этот рынок."
    if services.max_buy_amount(trader, question) <= 0:
        room = Decimal(settings.MARKET_CAP) - services.invested(trader, question)
        if room <= 0:
            return f"В один рынок можно вложить не больше {settings.MARKET_CAP}."
        return "На балансе не хватает монет."
    return None


def card(question: Question, *, starred: set[int] | None = None, now: datetime | None = None,
         my_side: str | None = None) -> dict:
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
        "closes_at": iso(question.deadline), "resolves_at": iso(question.resolves_at),
        "status": market_status(question),
        "outcome": question.outcome, "starred": question.pk in (starred or set()), "my_side": my_side,
        "chat": chat_ref(question), "created_at": iso(question.created_at),
    }


def chat_ref(question: Question) -> dict | None:
    """The closed chat a market belongs to; old site groups are not chats. Lists select_related("group")."""
    if not question.group_id or question.group.kind != "chat":
        return None
    return {"code": question.group.code, "title": question.group.name}


def chat_space(group, trader: Trader) -> dict:
    """`ChatSpace` in docs/api.md «Фаза 3b»: the chat's header for one of its members."""
    from .. import chats
    from ..rating import leaderboard
    markets = Question.objects.filter(group=group, kind=Question.Kind.LMSR, hidden_at__isnull=True,
                                      status__in=[Question.Status.OPEN, Question.Status.CLOSED]).count()
    volume = Trade.objects.filter(question__group=group, cost__gt=0).aggregate(v=Sum("cost"))["v"]
    mine = leaderboard(trader, group=group)["me"]
    # Members with no settled prediction and no result in the chat have no place yet.
    ranked = mine is not None and (mine["predictions"] > 0 or Decimal(mine["profit"]) != 0)
    cost = chats.creation_cost(group)
    return {
        "code": group.code, "title": group.name, "members": group.member_count, "frozen": not group.bot_active,
        "markets": markets, "volume": money(volume), "my_rank": mine["rank"] if ranked else None,
        "creation_cost": money(cost), "b": int(chats.depth(cost)),
        "assist": bool(group.assist_enabled and assist.enabled()), "link": chats.link(group),
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
    data = card(question, starred=starred_ids(trader), my_side=my_sides(trader, [question.pk]).get(question.pk))
    pos = Position.objects.filter(trader=trader, question=question).first()
    block = trade_block(question, trader)
    is_creator = question.creator_id == trader.pk
    trades = Trade.objects.filter(question=question).order_by("-created_at", "-id")[:20]
    data.update({
        "criteria": question.resolution_criteria, "resolves_at": iso(question.resolves_at),
        "author": author(question.creator),
        "is_creator": is_creator,
        "can_trade": block is None, "trade_block": block,
        "max_amount": money(services.max_buy_amount(trader, question)),
        "my_position": position(pos, question),
        # Anonymous: no trader, only side, size and the price it moved to.
        "b": question.b,
        "trades": [{"side": t.side, "amount": money(abs(t.cost)), "prob_before": round(t.prob_before, 4),
                    "prob_after": round(t.prob_after, 4),
                    "at": iso(t.created_at), "sell": t.cost < 0} for t in trades],
        "resolution": resolution(question, trader),
        "top_moves": top_moves(question),
    })
    return data


def top_moves(question: Question, limit: int = 3) -> list[dict]:
    """The sharpest price moves made by a single trade, anonymous; newest first among equal moves."""
    from django.db.models import F
    from django.db.models.functions import Abs
    moves = (Trade.objects.filter(question=question).annotate(move=Abs(F("prob_after") - F("prob_before")))
             .filter(move__gt=0).order_by("-move", "-created_at", "-id")[:limit])
    return [{"at": iso(t.created_at), "from": round(t.prob_before, 4), "to": round(t.prob_after, 4)} for t in moves]


def categories() -> list[dict]:
    return [{"slug": slug, "label": label} for slug, label in CATEGORIES]
