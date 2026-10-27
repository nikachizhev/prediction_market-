"""Mini App JSON API. Money changes go through market.services only."""
from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from .. import services
from ..access import visible_questions
from ..miniapp_auth import api_view
from ..models import LedgerEntry, MarketComplaint, MarketSubscription, Position, Question, Trade, Trader
from ..services import MarketError
from . import serializers as s

PAGE = 20


class BadRequest(Exception):
    pass


class Unavailable(Exception):
    """An outside service (Telegram) failed: 503, the client has a fallback."""


def endpoint(*methods, create: bool = False):
    """Authenticated JSON view; MarketError/BadRequest become 400, unknown market 404."""
    def decorate(view):
        @csrf_exempt
        @api_view(create=create)
        def wrapper(request, *args, **kwargs):
            if request.method not in methods:
                return JsonResponse({"error": "Метод не поддерживается."}, status=405)
            try:
                return JsonResponse(view(request, *args, **kwargs), safe=False)
            except (MarketError, BadRequest) as exc:
                return JsonResponse({"error": str(exc)}, status=400)
            except Unavailable as exc:
                return JsonResponse({"error": str(exc)}, status=503)
            except Question.DoesNotExist:
                return JsonResponse({"error": "Рынок не найден."}, status=404)
        wrapper.__name__ = view.__name__
        return wrapper
    return decorate


def body(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        raise BadRequest("Неверный запрос.") from None
    if not isinstance(data, dict):
        raise BadRequest("Неверный запрос.")
    return data


def amount_of(value) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise BadRequest("Укажите сумму числом.") from None
    if not amount.is_finite():
        raise BadRequest("Укажите сумму числом.")
    return amount


def side_of(value) -> str:
    if value not in ("yes", "no"):
        raise BadRequest("Выберите ДА или НЕТ.")
    return value


def feed_markets(trader: Trader):
    """Markets the Mini App lists: visible, binary LMSR, not hidden by complaints."""
    return visible_questions(trader).filter(kind=Question.Kind.LMSR, hidden_at__isnull=True)


def get_market(trader: Trader, pk: int) -> Question:
    # Someone else's private market is indistinguishable from a missing one.
    return visible_questions(trader).filter(kind=Question.Kind.LMSR).get(pk=pk)


def interests_of(value) -> list[str]:
    """Category slugs from the onboarding picker, deduplicated, in the categories' order."""
    from ..models import CATEGORIES
    slugs = [slug for slug, _ in CATEGORIES]
    if not isinstance(value, list) or any(not isinstance(v, str) or v not in slugs for v in value):
        raise BadRequest("Выберите темы из списка.")
    return [slug for slug in slugs if slug in value]


def notify_of(value) -> dict[str, bool]:
    """Partial bot notification switches: known kinds only, true/false only."""
    from ..bot_notifications import KINDS
    if not isinstance(value, dict):
        raise BadRequest("Настройки уведомлений — неверный формат.")
    if any(key not in KINDS for key in value):
        raise BadRequest("Неизвестный тип уведомлений.")
    if any(not isinstance(v, bool) for v in value.values()):
        raise BadRequest("Уведомление можно только включить или выключить.")
    return dict(value)


# --- account -------------------------------------------------------------------------

@endpoint("POST", create=True)
def session(request):
    trader = request.trader
    granted = []
    if request.trader_created:
        starter = trader.ledger.filter(kind=LedgerEntry.Kind.STARTER).first()
        if starter:
            granted.append({"kind": "starter", "amount": s.money(starter.amount)})
        services.skip_daily_reward_today(trader)
    reward = services.claim_daily_reward(trader)
    if reward:
        granted.append({"kind": "daily", "amount": s.money(reward.coins)})
    insurance = services.claim_insurance(trader)
    if insurance:
        granted.append({"kind": "insurance", "amount": s.money(insurance.amount)})
    from ..events import log_event
    log_event(trader, "app_open", {"start_param": request.telegram.start_param, "new": request.trader_created})
    trader.refresh_from_db()
    return {"me": s.me(trader), "new_account": request.trader_created, "granted": granted}


@endpoint("GET", "PATCH")
def me(request):
    trader = request.trader
    if request.method == "PATCH":
        data = body(request)
        fields = []
        if "pseudonym" in data:
            pseudonym = str(data["pseudonym"] or "").strip()
            if not 2 <= len(pseudonym) <= 32:
                raise BadRequest("Псевдоним — от 2 до 32 символов.")
            trader.pseudonym = pseudonym
            fields.append("pseudonym")
        if "show_real_name" in data:
            trader.show_real_name = bool(data["show_real_name"])
            fields.append("show_real_name")
        if "interests" in data:
            trader.interests = interests_of(data["interests"])
            fields.append("interests")
        if "notify" in data:
            trader.notify_prefs = {**(trader.notify_prefs or {}), **notify_of(data["notify"])}
            fields.append("notify_prefs")
        if data.get("onboarded") and not trader.onboarded_at:
            trader.onboarded_at = timezone.now()
            fields.append("onboarded_at")
        if fields:
            trader.save(update_fields=fields)
    return s.me(trader)


@endpoint("GET")
def categories(request):
    return s.categories()


# --- markets -------------------------------------------------------------------------

@endpoint("GET", "POST")
def markets(request):
    if request.method == "POST":
        return create_market(request)
    trader = request.trader
    now = timezone.now()
    qs = feed_markets(trader).filter(status__in=[Question.Status.OPEN, Question.Status.CLOSED])
    category = request.GET.get("category", "")
    if category:
        qs = qs.filter(category=category)
    query = request.GET.get("q", "").strip()
    if query:
        qs = qs.filter(title__icontains=query)
    sort = request.GET.get("sort", "hot")
    if sort == "new":
        qs = qs.order_by("-created_at", "-id")
    elif sort == "soon":
        qs = qs.filter(deadline__gt=now).order_by("deadline", "id")
    else:
        qs = qs.annotate(recent=Count("trades", filter=Q(trades__created_at__gte=now - timedelta(days=1)))) \
               .order_by("-recent", "-created_at", "-id")
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1
    total = qs.count()
    items = list(qs[(page - 1) * PAGE: page * PAGE])
    starred = s.starred_ids(trader)
    sides = s.my_sides(trader, [q.pk for q in items])
    return {"items": [s.card(q, starred=starred, now=now, my_side=sides.get(q.pk)) for q in items],
            "next_page": page + 1 if page * PAGE < total else None, "total": total}


def create_market(request):
    data = body(request)
    closes_at = parse_datetime(str(data.get("closes_at") or ""))
    resolves_at = parse_datetime(str(data.get("resolves_at") or ""))
    for value in (closes_at, resolves_at):
        if value is not None and timezone.is_naive(value):
            raise BadRequest("Укажите дату с часовым поясом.")
    question = services.create_market(
        request.trader, title=str(data.get("title") or ""), criteria=str(data.get("criteria") or ""),
        category=str(data.get("category") or ""), closes_at=closes_at, resolves_at=resolves_at,
        request_key=data.get("request_key") or None)
    if not question.replayed:
        from ..events import log_event
        log_event(request.trader, "market_create", {"market": question.pk, "category": question.category})
    request.trader.refresh_from_db()
    return {"market": s.detail(question, request.trader), "balance": s.money(request.trader.balance)}


@endpoint("GET")
def market(request, pk):
    return s.detail(get_market(request.trader, pk), request.trader)


@endpoint("GET")
def history(request, pk):
    question = get_market(request.trader, pk)
    now = timezone.now()
    spans = {"d": timedelta(days=1), "w": timedelta(days=7), "m": timedelta(days=30)}
    period = request.GET.get("period", "w")
    since = now - spans[period] if period in spans else question.created_at
    since = max(since, question.created_at)
    return {"points": s.History(question, since=since).points(since, now)}


@endpoint("POST")
def quote(request, pk):
    question = get_market(request.trader, pk)
    data = body(request)
    side, amount = side_of(data.get("side")), amount_of(data.get("amount"))
    services.check_can_trade(request.trader, question)
    q = services.quote(question, side, amount)
    return {"side": side, "amount": s.money(amount), "fee": s.money(q["fee"]), "cost": s.money(q["cost"]),
            "shares": s.shares(q["shares"]), "payout_if_win": s.money(q["payout_if_win"]),
            "prob_before": round(q["prob_before"], 4), "prob_after": round(q["new_prob"], 4),
            "max_amount": s.money(services.max_buy_amount(request.trader, question))}


def trade_result(request, question, trade):
    from ..events import log_event
    log_event(request.trader, "trade", {"market": question.pk, "side": trade.side, "sell": trade.cost < 0})
    # Following a market you hold a position in is automatic (spec E6.1).
    MarketSubscription.objects.get_or_create(trader=request.trader, question=question,
                                             defaults={"last_probability": question.prob})
    request.trader.refresh_from_db()
    question.refresh_from_db()
    return {"balance": s.money(request.trader.balance), "market": s.detail(question, request.trader),
            "trade": {"side": trade.side, "shares": s.shares(abs(trade.shares)), "cost": s.money(abs(trade.cost)),
                      "fee": s.money(trade.fee)}}


@endpoint("POST")
def buy(request, pk):
    question = get_market(request.trader, pk)
    data = body(request)
    if not data.get("request_key"):
        raise BadRequest("Нет идентификатора покупки.")
    trade = services.buy(request.trader, question, side_of(data.get("side")), amount_of(data.get("amount")),
                         request_key=data["request_key"])
    return trade_result(request, question, trade)


@endpoint("POST")
def sell(request, pk):
    question = get_market(request.trader, pk)
    data = body(request)
    side = side_of(data.get("side"))
    try:
        amount = Decimal(str(data.get("shares")))
    except (InvalidOperation, ValueError):
        raise BadRequest("Укажите количество.") from None
    trade = services.sell(request.trader, question, side, amount)
    return trade_result(request, question, trade)


@endpoint("POST")
def star(request, pk):
    question = get_market(request.trader, pk)
    starred = bool(body(request).get("starred"))
    if starred:
        MarketSubscription.objects.get_or_create(trader=request.trader, question=question,
                                                 defaults={"last_probability": question.prob})
    else:
        MarketSubscription.objects.filter(trader=request.trader, question=question).delete()
    return {"starred": starred}


@endpoint("POST")
def share(request, pk):
    """Prepared inline message for Telegram.WebApp.shareMessage (spec E6.4)."""
    from .. import share as sharing
    from ..events import log_event
    from ..telegram import TelegramError
    question = get_market(request.trader, pk)
    try:
        prepared_id = sharing.prepare(request.trader, question)
    except TelegramError:
        raise Unavailable("Telegram сейчас недоступен. Поделитесь ссылкой.") from None
    log_event(request.trader, "share", {"market": question.pk, "via": "prepared_message"})
    return {"prepared_message_id": prepared_id}


@endpoint("POST")
def report(request, pk):
    question = get_market(request.trader, pk)
    reason = str(body(request).get("reason") or "")[:300]
    with transaction.atomic():
        question = Question.objects.select_for_update().get(pk=question.pk)
        try:
            with transaction.atomic():
                MarketComplaint.objects.create(question=question, trader=request.trader, reason=reason)
        except IntegrityError:
            return {"ok": True}
        if not question.hidden_at and question.complaints.count() >= settings.COMPLAINTS_TO_HIDE:
            question.hidden_at = timezone.now()
            question.save(update_fields=["hidden_at"])
    return {"ok": True}


# --- personal ------------------------------------------------------------------------

@endpoint("GET")
def mine(request):
    trader = request.trader
    now = timezone.now()
    starred = s.starred_ids(trader)
    visible = visible_questions(trader).filter(kind=Question.Kind.LMSR)
    positions = (Position.objects.filter(trader=trader, question__in=visible)
                 .filter(Q(yes_shares__gt=0) | Q(no_shares__gt=0))
                 .exclude(question__status__in=[Question.Status.RESOLVED, Question.Status.ANNULLED])
                 .select_related("question").order_by("-question__created_at"))
    held = []
    for pos in positions:
        item = s.card(pos.question, starred=starred, now=now)
        item["position"] = s.position(pos, pos.question)
        held.append(item)
    watched = visible.filter(subscriptions__trader=trader).order_by("-subscriptions__created_at")
    created = []
    for q in visible.filter(creator=trader).order_by("-created_at"):
        item = s.card(q, starred=starred, now=now)
        item["fee_expected"] = s.money(q.fee_pool * Decimal(settings.CREATOR_FEE_SHARE))
        item["needs_resolution"] = (q.status in (Question.Status.OPEN, Question.Status.CLOSED)
                                    and not q.accepting_entries and s.market_status(q) == "closed")
        item["resolve_link"] = f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start=resolve_{q.pk}"
        created.append(item)
    return {"positions": held, "watched": [s.card(q, starred=starred, now=now) for q in watched],
            "created": created}


@endpoint("GET")
def rating(request):
    from ..rating import leaderboard
    trader = request.trader
    return leaderboard(trader, scope=request.GET.get("scope", "all"), by=request.GET.get("by", "profit"),
                       period=request.GET.get("period", "all"))


@endpoint("GET")
def profile(request):
    from ..rating import trader_stats
    from ..statistics import best_predictions, calibration, category_stats, referral_summary
    trader = request.trader
    ledger = trader.ledger.select_related("question")[:50]
    history = list(trader.ledger.filter(created_at__gte=timezone.now() - timedelta(days=90))
                   .order_by("created_at", "id").values_list("created_at", "balance_after"))
    best = [{**b, "gain": s.money(b["gain"])} for b in best_predictions(trader)]
    referral = referral_summary(trader)
    joined = trader.created_at or (trader.user.date_joined if trader.user_id else None)
    return {
        "me": s.me(trader), "stats": trader_stats(trader),
        "joined_at": s.iso(joined),
        "invited": referral["invited"],
        "referral": {**referral, "earned": s.money(referral["earned"]), "reward": s.money(referral["reward"])},
        "calibration": calibration(trader), "best_prediction": best[0] if best else None,
        "best_predictions": best,
        "categories": [{**c, "profit": s.money(c["profit"])} for c in category_stats(trader)],
        "balance_history": [{"t": s.iso(t), "balance": s.money(b)} for t, b in history],
        "ledger": [{"kind": e.kind, "label": e.get_kind_display(), "amount": s.money(e.amount), "at": s.iso(e.created_at),
                    "market": {"id": e.question_id, "title": e.question.title} if e.question_id else None}
                   for e in ledger],
    }


NOTICE_FILTERS = ("all", "mine", "pay", "markets")


@endpoint("GET")
def notifications(request):
    from ..bot_notifications import MARKETS_KINDS, PAY_KINDS
    from ..notifications import visible_notifications
    choice = request.GET.get("filter", "all")
    if choice not in NOTICE_FILTERS:
        raise BadRequest("Неизвестный фильтр.")
    items = visible_notifications(request.trader)
    unread = items.filter(read_at__isnull=True).count()
    if choice == "pay":
        items = items.filter(kind__in=PAY_KINDS)
    elif choice == "markets":
        items = items.filter(kind__in=MARKETS_KINDS)
    elif choice == "mine":
        items = items.exclude(kind__in=PAY_KINDS + MARKETS_KINDS)
    return {"items": [s.notice(n) for n in items.select_related("question")[:50]], "unread": unread}


@endpoint("POST")
def notifications_read(request):
    from ..notifications import visible_notifications
    data = body(request)
    items = visible_notifications(request.trader).filter(read_at__isnull=True)
    if data.get("ids") is not None:
        ids = data["ids"]
        if not isinstance(ids, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in ids):
            raise BadRequest("Неверный список уведомлений.")
        items.filter(pk__in=ids).update(read_at=timezone.now())
    else:
        items.update(read_at=timezone.now())
    return {"unread": visible_notifications(request.trader).filter(read_at__isnull=True).count()}


@endpoint("GET")
def profile_history(request):
    """Balance and realized profit (bonuses excluded) for `?period=d|w|m|all`; see `balance_profit_history`."""
    from ..statistics import balance_profit_history
    points = balance_profit_history(request.trader, request.GET.get("period", "w"))
    return {"points": [{"t": s.iso(p["t"]), "balance": s.money(p["balance"]), "profit": s.money(p["profit"])}
                       for p in points]}


@endpoint("POST")
def events(request):
    from ..events import CLIENT_EVENTS, log_event
    data = body(request)
    name = str(data.get("name") or "")
    if name not in CLIENT_EVENTS:
        raise BadRequest("Неизвестное событие.")
    props = data.get("props") if isinstance(data.get("props"), dict) else {}
    log_event(request.trader, name, props)
    return {"ok": True}
