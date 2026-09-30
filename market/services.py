"""All money / market-state mutations live here."""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from django.conf import settings
from django.db import transaction

from . import lmsr
from .models import Payout, Position, Question, Trade, Trader

CENT = Decimal("0.01")
SHARE_Q = Decimal("0.000001")
SIDES = ("yes", "no")


class MarketError(ValueError):
    """Business-rule violation; message is user-facing (Russian)."""


def _d(x: float | Decimal | int | str) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(repr(float(x)) if isinstance(x, float) else str(x))


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
    if amount <= 0:
        raise MarketError("Сумма ставки должна быть положительной.")
    if question.status != Question.Status.OPEN:
        raise MarketError("Ставки на этот вопрос закрыты.")
    shares_f = lmsr.shares_for_amount(side, float(amount), question.q_yes, question.q_no, question.b)
    shares = _floor_shares(shares_f)
    if shares <= 0:
        raise MarketError("Сумма слишком мала.")
    real = lmsr.trade_cost(side, float(shares), question.q_yes, question.q_no, question.b)
    cost = min(_ceil_cent(_d(real)), amount.quantize(CENT, rounding=ROUND_CEILING))
    dq = float(shares)
    new_q_yes = question.q_yes + (dq if side == "yes" else 0.0)
    new_q_no = question.q_no + (dq if side == "no" else 0.0)
    return {
        "side": side,
        "shares": shares,
        "cost": cost,
        "prob_before": question.prob,
        "new_prob": lmsr.price_yes(new_q_yes, new_q_no, question.b),
        "payout_if_win": _floor_cent(shares),
    }


def _get_position(trader: Trader, question: Question) -> Position:
    pos, _ = Position.objects.select_for_update().get_or_create(trader=trader, question=question)
    return pos


@transaction.atomic
def buy(trader: Trader, question: Question, side: str, amount: Decimal | float | int | str) -> Trade:
    _check_side(side)
    amount = _d(amount)
    question = Question.objects.select_for_update().get(pk=question.pk)
    trader = Trader.objects.select_for_update().get(pk=trader.pk)
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
    q = quote(question, side, amount)
    if q["cost"] > trader.balance:
        raise MarketError("Недостаточно средств на балансе.")
    pos = _get_position(trader, question)
    if side == "yes":
        question.q_yes += float(q["shares"])
        pos.yes_shares += q["shares"]
    else:
        question.q_no += float(q["shares"])
        pos.no_shares += q["shares"]
    pos.spent += q["cost"]
    trader.balance -= q["cost"]
    trade = Trade.objects.create(
        trader=trader, question=question, side=side, shares=q["shares"], cost=q["cost"],
        prob_before=q["prob_before"], prob_after=q["new_prob"],
    )
    question.save(update_fields=["q_yes", "q_no"])
    pos.save()
    trader.save(update_fields=["balance"])
    return trade


@transaction.atomic
def sell(trader: Trader, question: Question, side: str, shares: Decimal | float | int | str) -> Trade:
    """Sell back shares of `side` (at most what the trader holds). Proceeds rounded down."""
    _check_side(side)
    shares = _d(shares).quantize(SHARE_Q, rounding=ROUND_FLOOR)
    question = Question.objects.select_for_update().get(pk=question.pk)
    trader = Trader.objects.select_for_update().get(pk=trader.pk)
    if question.status != Question.Status.OPEN:
        raise MarketError("Ставки на этот вопрос закрыты.")
    if shares <= 0:
        raise MarketError("Количество акций должно быть положительным.")
    pos = _get_position(trader, question)
    held = pos.yes_shares if side == "yes" else pos.no_shares
    if shares > held:
        raise MarketError("Нельзя продать больше акций, чем у вас есть.")
    before = question.prob
    proceeds = _floor_cent(_d(-lmsr.trade_cost(side, -float(shares), question.q_yes, question.q_no, question.b)))
    if side == "yes":
        question.q_yes -= float(shares)
        pos.yes_shares -= shares
    else:
        question.q_no -= float(shares)
        pos.no_shares -= shares
    # Reduce `spent` proportionally so annulment refunds the remaining cost basis.
    pos.spent -= (pos.spent * shares / held).quantize(CENT, rounding=ROUND_FLOOR)
    trader.balance += proceeds
    trade = Trade.objects.create(
        trader=trader, question=question, side=side, shares=-shares, cost=-proceeds,
        prob_before=before, prob_after=question.prob,
    )
    question.save(update_fields=["q_yes", "q_no"])
    pos.save()
    trader.save(update_fields=["balance"])
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
    question.outcome = outcome
    question.save(update_fields=["status", "outcome"])
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
    question.outcome = None
    question.save(update_fields=["status", "outcome"])
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
) -> Question:
    b = float(b if b is not None else settings.LMSR_DEFAULT_B)
    q_yes, q_no = lmsr.initial_q(initial_prob, b)
    with transaction.atomic():
        question = Question.objects.create(
            title=title, resolution_criteria=resolution_criteria, source_type=source_type,
            source_ref=source_ref, deadline=deadline, initial_prob=initial_prob, b=b,
            q_yes=q_yes, q_no=q_no,
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
    return trader.balance + portfolio_value(trader) - Decimal(settings.START_BALANCE)
