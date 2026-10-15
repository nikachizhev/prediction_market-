"""All money / market-state mutations live here."""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from django.conf import settings
from django.db import transaction

from . import lmsr
from . import mechanisms
from django.utils import timezone
from .models import Payout, Position, Question, Trade, Trader

CENT = Decimal("0.01")
SHARE_Q = Decimal("0.000001")
SIDES = ("yes", "no")


class MarketError(ValueError):
    """Business-rule violation; message is user-facing (Russian)."""


def _d(x: float | Decimal | int | str) -> Decimal:
    value = x if isinstance(x, Decimal) else Decimal(repr(float(x)) if isinstance(x, float) else str(x))
    if not value.is_finite():
        raise MarketError("Введите конечное число.")
    return value


def check_open(question):
    if question.status != Question.Status.OPEN or (question.deadline and question.deadline <= timezone.now()):
        raise MarketError("Участие в этом вопросе закрыто.")


def check_access(trader, question):
    from .access import can_access
    if not can_access(trader, question):
        raise MarketError("Этот вопрос доступен только участникам группы.")


def _ceil_cent(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_CEILING)


def _floor_cent(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_FLOOR)


def _floor_shares(x: float) -> Decimal:
    return Decimal(repr(x)).quantize(SHARE_Q, rounding=ROUND_FLOOR)


def _check_side(side: str) -> None:
    if side not in SIDES:
        raise MarketError("Неизвестная сторона: нужно «yes» или «no».")


def _opposite_prob(question: Question, side: str) -> float:
    return question.prob if side == "yes" else 1.0 - question.prob


def quote(question: Question, side: str, amount: Decimal | float | int | str) -> dict[str, Any]:
    """Preview of buying `side` for `amount` coins. Does not validate balance."""
    _check_side(side)
    amount = _d(amount)
    check_open(question)
    if not question.is_trading:
        raise MarketError("Этот вопрос не поддерживает торговлю.")
    if amount <= 0:
        raise MarketError("Сумма ставки должна быть положительной.")
    if question.status != Question.Status.OPEN:
        raise MarketError("Ставки на этот вопрос закрыты.")
    shares_f = mechanisms.shares_for_amount(question, side, float(amount))
    shares = _floor_shares(shares_f)
    if shares <= 0:
        raise MarketError("Сумма слишком мала.")
    real = mechanisms.trade_cost(question, side, float(shares))
    cost = min(_ceil_cent(_d(real)), amount.quantize(CENT, rounding=ROUND_CEILING))
    dq = float(shares)
    new_q_yes = question.q_yes + (dq if side == "yes" else 0.0)
    new_q_no = question.q_no + (dq if side == "no" else 0.0)
    import copy
    preview = copy.copy(question)
    mechanisms.move_pool(preview, side, dq, real)
    return {
        "side": side,
        "shares": shares,
        "cost": cost,
        "prob_before": question.prob,
        "new_prob": mechanisms.probability(preview, new_q_yes, new_q_no),
        "pool_cost": real,
        "payout_if_win": _floor_cent(shares),
    }


def _get_position(trader: Trader, question: Question) -> Position:
    pos, _ = Position.objects.select_for_update().get_or_create(trader=trader, question=question)
    return pos


@transaction.atomic
def buy(trader: Trader, question: Question, side: str, amount: Decimal | float | int | str, *, request_key=None) -> Trade:
    _check_side(side)
    amount = _d(amount)
    question = Question.objects.select_for_update().get(pk=question.pk)
    trader = Trader.objects.select_for_update().get(pk=trader.pk)
    check_access(trader, question)
    if request_key is not None:
        from uuid import UUID
        try:
            request_key = UUID(str(request_key))
        except (ValueError, TypeError, AttributeError):
            raise MarketError("Недействительный идентификатор покупки.") from None
        previous = Trade.objects.filter(request_key=request_key).first()
        if previous:
            if (previous.trader_id != trader.pk or previous.question_id != question.pk
                    or previous.side != side or previous.requested_amount != amount):
                raise MarketError("Этот идентификатор уже использован для другой покупки. Обновите страницу.")
            return previous
    if question.status != Question.Status.OPEN:
        raise MarketError("Ставки на этот вопрос закрыты.")
    if question.conflicted_traders.filter(pk=trader.pk).exists():
        raise MarketError("Конфликт интересов: вы не можете ставить на этот вопрос.")
    max_bet = min(trader.balance, Decimal(settings.MAX_BET))
    if amount <= 0:
        raise MarketError("Сумма ставки должна быть положительной.")
    if amount > trader.balance:
        raise MarketError("Недостаточно средств на балансе.")
    if amount > max_bet:
        raise MarketError(f"Максимальная ставка за сделку — {settings.MAX_BET}.")
    if amount != amount.quantize(CENT):
        raise MarketError("Укажите сумму не более чем с двумя знаками после запятой.")
    q = quote(question, side, amount)
    if q["cost"] > trader.balance:
        raise MarketError("Недостаточно средств на балансе.")
    pos = _get_position(trader, question)
    if side == "yes":
        question.q_yes += float(q["shares"])
        pos.yes_shares += q["shares"]
        pos.yes_spent += q["cost"]
    else:
        question.q_no += float(q["shares"])
        pos.no_shares += q["shares"]
        pos.no_spent += q["cost"]
    pos.spent = pos.yes_spent + pos.no_spent
    mechanisms.move_pool(question, side, float(q["shares"]), q["pool_cost"])
    trader.balance -= q["cost"]
    trade = Trade.objects.create(
        trader=trader, question=question, side=side, shares=q["shares"], cost=q["cost"],
        prob_before=q["prob_before"], prob_after=q["new_prob"],
        request_key=request_key, requested_amount=amount,
    )
    question.save(update_fields=["q_yes", "q_no", "pool_yes", "pool_no"])
    pos.save()
    trader.save(update_fields=["balance"])
    from .subscriptions import price_changed
    price_changed(question)
    return trade


@transaction.atomic
def sell(trader: Trader, question: Question, side: str, shares: Decimal | float | int | str) -> Trade:
    """Sell back shares of `side` (at most what the trader holds). Proceeds rounded down."""
    _check_side(side)
    shares = _d(shares).quantize(SHARE_Q, rounding=ROUND_FLOOR)
    question = Question.objects.select_for_update().get(pk=question.pk)
    trader = Trader.objects.select_for_update().get(pk=trader.pk)
    check_access(trader, question)
    check_open(question)
    if not question.is_trading:
        raise MarketError("Этот вопрос не поддерживает торговлю.")
    if question.status != Question.Status.OPEN:
        raise MarketError("Ставки на этот вопрос закрыты.")
    if shares <= 0:
        raise MarketError("Количество акций должно быть положительным.")
    pos = _get_position(trader, question)
    held = pos.yes_shares if side == "yes" else pos.no_shares
    if shares > held:
        raise MarketError("Нельзя продать больше акций, чем у вас есть.")
    before = question.prob
    real_cost = mechanisms.trade_cost(question, side, -float(shares))
    proceeds = _floor_cent(_d(-real_cost))
    mechanisms.move_pool(question, side, -float(shares), real_cost)
    if side == "yes":
        question.q_yes -= float(shares)
        pos.yes_shares -= shares
    else:
        question.q_no -= float(shares)
        pos.no_shares -= shares
    # A sale only consumes the cost basis of its own side, never the opposite side.
    cost_field = "yes_spent" if side == "yes" else "no_spent"
    basis = getattr(pos, cost_field)
    setattr(pos, cost_field, basis - (basis * shares / held).quantize(CENT, rounding=ROUND_FLOOR))
    pos.spent = pos.yes_spent + pos.no_spent
    trader.balance += proceeds
    trade = Trade.objects.create(
        trader=trader, question=question, side=side, shares=-shares, cost=-proceeds,
        prob_before=before, prob_after=question.prob,
    )
    question.save(update_fields=["q_yes", "q_no", "pool_yes", "pool_no"])
    pos.save()
    trader.save(update_fields=["balance"])
    from .subscriptions import price_changed
    price_changed(question)
    return trade


@transaction.atomic
def close_betting(question: Question) -> Question:
    question = Question.objects.select_for_update().get(pk=question.pk)
    if question.status == Question.Status.OPEN:
        question.status = Question.Status.CLOSED
        question.save(update_fields=["status"])
    return question


def _lock_positions(question: Question) -> list[Position]:
    positions = list(Position.objects.select_for_update().filter(question=question).order_by("trader_id"))
    traders = {
        t.pk: t
        for t in Trader.objects.select_for_update().filter(pk__in=[p.trader_id for p in positions]).order_by("pk")
    }
    for p in positions:
        p.trader = traders[p.trader_id]
    return positions


@transaction.atomic
def resolve(question: Question, outcome: str) -> Question:
    """Pay 1 coin per winning share (rounded down to 0.01 per holder)."""
    if outcome not in ("yes", "no"):
        raise MarketError("Исход должен быть «yes» или «no».")
    question = Question.objects.select_for_update().get(pk=question.pk)
    if not question.is_trading:
        raise MarketError("Используйте форму результата для этого типа вопроса.")
    if question.status in (Question.Status.RESOLVED, Question.Status.ANNULLED):
        raise MarketError("Вопрос уже разрешён или аннулирован.")
    for pos in _lock_positions(question):
        shares = pos.yes_shares if outcome == "yes" else pos.no_shares
        amount = _floor_cent(shares)
        if amount > 0:
            pos.trader.balance += amount
            pos.trader.save(update_fields=["balance"])
            Payout.objects.create(trader=pos.trader, question=question, amount=amount, kind=Payout.Kind.PAYOUT)
    question.status = Question.Status.RESOLVED
    question.resolved_at = timezone.now()
    question.outcome = outcome
    question.save(update_fields=["status", "outcome", "resolved_at"])
    from .notifications import market_finished
    market_finished(question)
    return question


@transaction.atomic
def annul(question: Question) -> Question:
    """Refund each trader's `spent` on this question."""
    question = Question.objects.select_for_update().get(pk=question.pk)
    if question.status in (Question.Status.RESOLVED, Question.Status.ANNULLED):
        raise MarketError("Вопрос уже разрешён или аннулирован.")
    for pos in _lock_positions(question):
        if pos.spent > 0:
            pos.trader.balance += pos.spent
            pos.trader.save(update_fields=["balance"])
            Payout.objects.create(trader=pos.trader, question=question, amount=pos.spent, kind=Payout.Kind.REFUND)
    question.status = Question.Status.ANNULLED
    question.resolved_at = timezone.now()
    question.outcome = None
    question.save(update_fields=["status", "outcome", "resolved_at"])
    from .notifications import market_finished
    market_finished(question)
    return question


def create_question(
    *,
    title: str,
    resolution_criteria: str,
    source_type: str = Question.Source.MANUAL,
    source_ref: str = "",
    deadline=None,
    initial_prob: float = 0.5,
    b: float | None = None,
    conflicted_traders=(),
    group_id=None,
) -> Question:
    b = float(b if b is not None else settings.LMSR_DEFAULT_B)
    q_yes, q_no = lmsr.initial_q(initial_prob, b)
    with transaction.atomic():
        question = Question.objects.create(
            title=title, resolution_criteria=resolution_criteria, source_type=source_type,
            source_ref=source_ref, deadline=deadline, initial_prob=initial_prob, b=b,
            q_yes=q_yes, q_no=q_no, group_id=group_id, tracks_deadline=source_type == Question.Source.GITHUB,
        )
        if conflicted_traders:
            question.conflicted_traders.set(conflicted_traders)
    return question


def portfolio_value(trader: Trader) -> Decimal:
    """Positions in open/closed questions valued at the current price."""
    total = 0.0
    positions = Position.objects.filter(
        trader=trader, question__status__in=[Question.Status.OPEN, Question.Status.CLOSED]
    ).select_related("question")
    for p in positions:
        pr = p.question.prob
        total += float(p.yes_shares) * pr + float(p.no_shares) * (1.0 - pr)
    return _d(total).quantize(CENT)


def profit(trader: Trader) -> Decimal:
    trader.refresh_from_db(fields=["balance"])
    from django.db.models import Sum
    bonuses = trader.daily_rewards.aggregate(total=Sum("coins"))["total"] or Decimal(0)
    return trader.balance + portfolio_value(trader) - Decimal(settings.START_BALANCE) - bonuses


@transaction.atomic
def claim_daily_reward(trader):
    from zoneinfo import ZoneInfo
    from django.utils import timezone
    from .models import DailyReward
    trader = Trader.objects.select_for_update().get(pk=trader.pk)
    day = timezone.localdate(timezone=ZoneInfo("Europe/Moscow"))
    reward, created = DailyReward.objects.get_or_create(trader=trader, day=day)
    if created:
        trader.balance += reward.coins
        trader.save(update_fields=["balance"])
    return reward if created else None


def purchase_snapshot(question):
    return {field: getattr(question, field) for field in ("kind", "b", "alpha", "q_yes", "q_no", "pool_yes", "pool_no")}


def prepare_purchase(trader, question, side, amount):
    import hashlib
    import secrets
    from datetime import timedelta
    from .models import PurchaseConfirmation
    check_access(trader, question)
    if question.conflicted_traders.filter(pk=trader.pk).exists():
        raise MarketError("Конфликт интересов: вы не можете ставить на этот вопрос.")
    amount = _d(amount)
    if amount > min(trader.balance, Decimal(settings.MAX_BET)):
        raise MarketError("Недостаточно средств или превышена максимальная ставка.")
    if amount <= 0 or amount != amount.quantize(CENT):
        raise MarketError("Укажите положительную сумму, не более двух знаков после запятой.")
    preview = quote(question, side, amount)
    token = secrets.token_urlsafe(32)
    PurchaseConfirmation.objects.create(trader=trader, question=question,
        token_hash=hashlib.sha256(token.encode()).hexdigest(), side=side,
        amount=amount, snapshot=purchase_snapshot(question),
        expires_at=timezone.now()+timedelta(minutes=2))
    return preview, token


@transaction.atomic
def confirm_purchase(trader, question, token):
    import hashlib
    from .models import PurchaseConfirmation
    confirmation = PurchaseConfirmation.objects.select_for_update().filter(
        trader=trader, question=question, token_hash=hashlib.sha256(token.encode()).hexdigest()).first()
    if not confirmation:
        raise MarketError("Подтверждение недействительно. Рассчитайте ставку заново.")
    question = Question.objects.select_for_update().get(pk=question.pk)
    check_access(trader, question)
    if confirmation.trade_id:
        return confirmation.trade
    if confirmation.expires_at <= timezone.now():
        raise MarketError("Расчёт действовал две минуты и устарел. Рассчитайте ставку заново.")
    if confirmation.snapshot != purchase_snapshot(question):
        raise MarketError("Цена изменилась после расчёта. Проверьте новую ставку перед покупкой.")
    trade = buy(trader, question, confirmation.side, confirmation.amount)
    confirmation.trade = trade
    confirmation.save(update_fields=["trade"])
    return trade
