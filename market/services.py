"""All money / market-state mutations live here."""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from django.conf import settings
from django.db import transaction

from . import lmsr
from . import mechanisms
from django.utils import timezone
from .models import LedgerEntry, Payout, Position, Question, Trade, Trader

CENT = Decimal("0.01")
SHARE_Q = Decimal("0.000001")
SIDES = ("yes", "no")


class MarketError(ValueError):
    """Business-rule violation; message is user-facing (Russian)."""


class PriceChanged(MarketError):
    """The creation price is not the one the creator saw; nothing was charged."""

    def __init__(self, cost: int):
        super().__init__(f"Цена создания изменилась: теперь {cost} монет. Нажми ещё раз, чтобы создать.")
        self.cost = cost


def _d(x: float | Decimal | int | str) -> Decimal:
    value = x if isinstance(x, Decimal) else Decimal(repr(float(x)) if isinstance(x, float) else str(x))
    if not value.is_finite():
        raise MarketError("Введите конечное число.")
    return value


def check_open(question):
    if question.publication_state != "approved":
        raise MarketError("Рынок ещё не прошёл проверку перед публикацией.")
    if question.status != Question.Status.OPEN or (question.deadline and question.deadline <= timezone.now()):
        raise MarketError("Участие в этом вопросе закрыто.")
    if question.hidden_at:
        raise MarketError("Рынок скрыт до решения модератора.")


def fee_rate() -> Decimal:
    return Decimal(settings.TRADE_FEE)


def _record(trader: Trader, kind: str, amount: Decimal, question: Question | None = None) -> LedgerEntry:
    """Ledger line for a balance change already applied to `trader` (in memory or saved)."""
    return LedgerEntry.objects.create(
        trader=trader, kind=kind, amount=amount, balance_after=trader.balance, question=question,
    )


def _credit(trader: Trader, amount: Decimal, kind: str, question: Question | None = None) -> None:
    trader.balance += amount
    trader.save(update_fields=["balance"])
    _record(trader, kind, amount, question)


def trade_limit(trader: Trader) -> Decimal:
    """Largest single purchase: a share of the balance, capped."""
    share = (trader.balance * Decimal(settings.TRADE_LIMIT_SHARE)).quantize(CENT, rounding=ROUND_FLOOR)
    return max(Decimal(0), min(share, Decimal(settings.MAX_TRADE)))


def invested(trader: Trader, question: Question) -> Decimal:
    """Net stake in the whole market: the options of a multi-option market share one cap."""
    from django.db.models import Q, Sum
    root_id = question.parent_id or question.pk
    total = (Position.objects.filter(trader=trader).filter(Q(question_id=root_id) | Q(question__parent_id=root_id))
             .aggregate(total=Sum("spent"))["total"])
    return total or Decimal(0)


def cap_message(question: Question, room: Decimal | None = None) -> str:
    where = "В один рынок (на все варианты вместе)" if question.parent_id else "В один рынок"
    text = f"{where} можно вложить не больше {settings.MARKET_CAP}."
    if room is not None:
        text += f" Осталось {_fmt(max(room, Decimal(0)))}."
    return text


def max_buy_amount(trader: Trader, question: Question) -> Decimal:
    room = Decimal(settings.MARKET_CAP) - invested(trader, question)
    return max(Decimal(0), min(trade_limit(trader), room, trader.balance))


def check_can_trade(trader: Trader, question: Question) -> None:
    if question.creator_id is not None and question.creator_id == trader.pk:
        raise MarketError("Создатель рынка не может делать прогнозы на свой рынок.")
    if question.conflicted_traders.filter(pk=trader.pk).exists():
        raise MarketError("Конфликт интересов: вы не можете ставить на этот вопрос.")


OPTION_ROOT_BLOCK = "Прогноз делается на конкретный вариант — выберите его в списке."


def is_option_root(question: Question) -> bool:
    """The root of a multi-option market only groups the options; it has no market maker of its own."""
    return question.pk is not None and question.parent_id is None and question.submarkets.exists()


def check_not_option_root(question: Question) -> None:
    if is_option_root(question):
        raise MarketError(OPTION_ROOT_BLOCK)


def check_access(trader, question):
    if question.publication_state != "approved":
        raise MarketError("Рынок ещё не прошёл проверку перед публикацией.")
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
    check_not_option_root(question)
    if amount <= 0:
        raise MarketError("Сумма ставки должна быть положительной.")
    if question.status != Question.Status.OPEN:
        raise MarketError("Ставки на этот вопрос закрыты.")
    # The fee is withheld from the stake; the rest buys shares.
    fee = _ceil_cent(amount * fee_rate())
    net = amount - fee
    if net <= 0:
        raise MarketError("Сумма слишком мала.")
    shares_f = mechanisms.shares_for_amount(question, side, float(net))
    shares = _floor_shares(shares_f)
    if shares <= 0:
        raise MarketError("Сумма слишком мала.")
    real = mechanisms.trade_cost(question, side, float(shares))
    maker_cost = min(_ceil_cent(_d(real)), net.quantize(CENT, rounding=ROUND_CEILING))
    cost = maker_cost + fee
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
        "fee": fee,
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
    check_not_option_root(question)
    check_can_trade(trader, question)
    if amount <= 0:
        raise MarketError("Сумма ставки должна быть положительной.")
    if amount > trader.balance:
        raise MarketError("Недостаточно средств на балансе.")
    limit = trade_limit(trader)
    if amount > limit:
        raise MarketError(f"Максимум за одну сделку — {_fmt(limit)}: 20% баланса, но не больше {settings.MAX_TRADE}.")
    # The trader lock above serialises buys on sibling options, so the family cap holds under concurrency.
    room = Decimal(settings.MARKET_CAP) - invested(trader, question)
    if amount > room:
        raise MarketError(cap_message(question, room))
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
    question.fee_pool += q["fee"]
    trader.balance -= q["cost"]
    trade = Trade.objects.create(
        trader=trader, question=question, side=side, shares=q["shares"], cost=q["cost"], fee=q["fee"],
        prob_before=q["prob_before"], prob_after=q["new_prob"],
        request_key=request_key, requested_amount=amount,
    )
    question.save(update_fields=["q_yes", "q_no", "pool_yes", "pool_no", "fee_pool"])
    pos.save()
    trader.save(update_fields=["balance"])
    _record(trader, LedgerEntry.Kind.BUY, -q["cost"], question)
    from .subscriptions import price_changed
    price_changed(question)
    grant_referral_reward(trader)
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
    check_not_option_root(question)
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
    gross = _floor_cent(_d(-real_cost))
    fee = min(_ceil_cent(gross * fee_rate()), gross)
    proceeds = gross - fee
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
    # Consumed basis rounds up, so tiny sales cannot keep the full basis (it caps the 500 per market).
    setattr(pos, cost_field, max(Decimal(0), basis - (basis * shares / held).quantize(CENT, rounding=ROUND_CEILING)))
    pos.spent = pos.yes_spent + pos.no_spent
    trader.balance += proceeds
    question.fee_pool += fee
    trade = Trade.objects.create(
        trader=trader, question=question, side=side, shares=-shares, cost=-proceeds, fee=fee,
        prob_before=before, prob_after=question.prob,
    )
    question.save(update_fields=["q_yes", "q_no", "pool_yes", "pool_no", "fee_pool"])
    pos.save()
    trader.save(update_fields=["balance"])
    _record(trader, LedgerEntry.Kind.SELL, proceeds, question)
    from .subscriptions import price_changed
    price_changed(question)
    return trade


@transaction.atomic
def close_betting(question: Question) -> Question:
    question = Question.objects.select_for_update().get(pk=question.pk)
    subs = list(question.submarkets.select_for_update().order_by("id"))
    if question.status == Question.Status.OPEN:
        question.status = Question.Status.CLOSED
        question.save(update_fields=["status"])
    for sub in subs:
        if sub.status == Question.Status.OPEN:
            sub.status = Question.Status.CLOSED
            sub.save(update_fields=["status"])
    return question


def _lock_positions(question: Question, extra_trader_ids=()) -> tuple[list[Position], dict[int, Trader]]:
    """Lock positions, their holders and the creator (in pk order, one object per trader)."""
    return _lock_family_positions([question], question.creator_id, extra_trader_ids)


def _lock_family_positions(questions: list[Question], creator_id: int | None,
                           extra_trader_ids=()) -> tuple[list[Position], dict[int, Trader]]:
    positions = list(Position.objects.select_for_update()
                     .filter(question_id__in=[q.pk for q in questions]).order_by("question_id", "trader_id"))
    ids = {p.trader_id for p in positions}
    ids.update(extra_trader_ids)
    if creator_id:
        ids.add(creator_id)
    traders = {t.pk: t for t in Trader.objects.select_for_update().filter(pk__in=ids).order_by("pk")}
    for p in positions:
        p.trader = traders[p.trader_id]
    return positions, traders


def _settle_creator(question: Question, traders: dict[int, Trader], *, refund_subsidy: bool = True,
                    submarkets: list[Question] | None = None, fee_to_id: int | None = None,
                    burn_fee: bool = False) -> None:
    """Return the unused subsidy and pay the creator's share of fees; the rest burns.

    `fee_to_id` — a participant who stated the outcome gets that share instead of the creator (phase 3d);
    `burn_fee` — nobody earned it (a participant's outcome was overturned), so it burns too.
    """
    creator = traders.get(question.creator_id) if question.creator_id else None
    from django.db.models import Sum
    from django.db.models.functions import Coalesce
    zero = Decimal(0)
    subs = submarkets or []
    family_ids = [question.pk, *(s.pk for s in subs)]
    trades = Trade.objects.filter(question_id__in=family_ids).aggregate(
        cost=Coalesce(Sum("cost"), zero), fee=Coalesce(Sum("fee"), zero))
    paid = Payout.objects.filter(question_id__in=family_ids, kind=Payout.Kind.PAYOUT).aggregate(
        total=Coalesce(Sum("amount"), zero))["total"]
    total_subsidy = question.subsidy + sum((s.subsidy for s in subs), zero)
    # What the market maker holds: the subsidy plus net stakes (fees excluded) minus payouts.
    maker = total_subsidy + trades["cost"] - trades["fee"] - paid
    subsidy_back = _floor_cent(min(max(maker, zero), total_subsidy))
    if creator is not None and subsidy_back > 0 and refund_subsidy:
        _credit(creator, subsidy_back, LedgerEntry.Kind.SUBSIDY_RETURN, question)
    if burn_fee:
        return
    total_fee_pool = question.fee_pool + sum((s.fee_pool for s in subs), zero)
    creator_fee = _floor_cent(total_fee_pool * Decimal(settings.CREATOR_FEE_SHARE))
    if creator_fee <= 0:
        return
    if fee_to_id is not None and fee_to_id != question.creator_id:
        _credit(traders[fee_to_id], creator_fee, LedgerEntry.Kind.PROPOSER_FEE, question)
    elif creator is not None:
        _credit(creator, creator_fee, LedgerEntry.Kind.CREATOR_FEE, question)


def _resolve_family(root: Question, subs: list[Question], outcomes: dict[int, str],
                    *, refund_subsidy: bool = True, fee_to_id: int | None = None,
                    burn_fee: bool = False) -> Question:
    """Resolve all option submarkets of a multiple-choice market and settle the creator once on `root`."""
    if root.status in (Question.Status.RESOLVED, Question.Status.ANNULLED):
        raise MarketError("Вопрос уже разрешён или аннулирован.")
    unsettled = [s for s in subs if s.status not in (Question.Status.RESOLVED, Question.Status.ANNULLED)]
    for sub in unsettled:
        if outcomes.get(sub.pk) not in ("yes", "no"):
            raise MarketError("Укажите ДА или НЕТ для каждого варианта.")
    positions, traders = _lock_family_positions([root, *unsettled], root.creator_id,
                                                 {fee_to_id} if fee_to_id else ())
    by_sub: dict[int, list[Position]] = {}
    for pos in positions:
        by_sub.setdefault(pos.question_id, []).append(pos)
    now = timezone.now()
    for sub in unsettled:
        out = outcomes[sub.pk]
        for pos in by_sub.get(sub.pk, ()):
            shares = pos.yes_shares if out == "yes" else pos.no_shares
            amount = _floor_cent(shares)
            if amount > 0:
                _credit(pos.trader, amount, LedgerEntry.Kind.PAYOUT, sub)
                Payout.objects.create(trader=pos.trader, question=sub, amount=amount, kind=Payout.Kind.PAYOUT)
        sub.status = Question.Status.RESOLVED
        sub.resolved_at = now
        sub.outcome = out
        sub.save(update_fields=["status", "outcome", "resolved_at"])
    _settle_creator(root, traders, refund_subsidy=refund_subsidy, submarkets=unsettled,
                    fee_to_id=fee_to_id, burn_fee=burn_fee)
    root.status = Question.Status.RESOLVED
    root.resolved_at = now
    root.outcome = None
    root.save(update_fields=["status", "outcome", "resolved_at"])
    from .notifications import market_finished
    market_finished(root)
    return root


@transaction.atomic
def resolve(question: Question, outcome: str, *, refund_subsidy: bool = True,
            fee_to_id: int | None = None, burn_fee: bool = False) -> Question:
    """Pay 1 coin per winning share (rounded down to 0.01 per holder); a creator at fault loses the subsidy."""
    if outcome not in ("yes", "no"):
        raise MarketError("Исход должен быть «yes» или «no».")
    question = Question.objects.select_for_update().get(pk=question.pk)
    if not question.is_trading:
        raise MarketError("Используйте форму результата для этого типа вопроса.")
    if question.status in (Question.Status.RESOLVED, Question.Status.ANNULLED):
        raise MarketError("Вопрос уже разрешён или аннулирован.")
    positions, traders = _lock_positions(question, {fee_to_id} if fee_to_id else ())
    for pos in positions:
        shares = pos.yes_shares if outcome == "yes" else pos.no_shares
        amount = _floor_cent(shares)
        if amount > 0:
            _credit(pos.trader, amount, LedgerEntry.Kind.PAYOUT, question)
            Payout.objects.create(trader=pos.trader, question=question, amount=amount, kind=Payout.Kind.PAYOUT)
    _settle_creator(question, traders, refund_subsidy=refund_subsidy, fee_to_id=fee_to_id, burn_fee=burn_fee)
    question.status = Question.Status.RESOLVED
    question.resolved_at = timezone.now()
    question.outcome = outcome
    question.save(update_fields=["status", "outcome", "resolved_at"])
    from .notifications import market_finished
    market_finished(question)
    return question


@transaction.atomic
def annul(question: Question, *, refund_subsidy: bool = True) -> Question:
    """Cancel: everyone gets back their net stake, fees included; the creator gets the subsidy unless at fault.

    Someone who sold at a profit keeps it, so the subsidy covers the gap first, then refunds shrink pro rata.
    """
    question = Question.objects.select_for_update().get(pk=question.pk)
    if question.status in (Question.Status.RESOLVED, Question.Status.ANNULLED):
        raise MarketError("Вопрос уже разрешён или аннулирован.")
    subs = list(question.submarkets.select_for_update().order_by("id"))
    unsettled_subs = [s for s in subs if s.status not in (Question.Status.RESOLVED, Question.Status.ANNULLED)]
    family = [question, *unsettled_subs]
    family_ids = [q.pk for q in family]
    by_q = {q.pk: q for q in family}
    from django.db.models import Sum
    rows = list(Trade.objects.filter(question_id__in=family_ids)
                .values("question_id", "trader_id").annotate(n=Sum("cost")))
    _, traders = _lock_family_positions(family, question.creator_id, {r["trader_id"] for r in rows})
    # Net money each trader put into each market/option: purchases with fees minus sale proceeds.
    wanted = [(r["question_id"], r["trader_id"], r["n"]) for r in rows if r["n"] > 0]
    total_subsidy = question.subsidy + sum((s.subsidy for s in unsettled_subs), Decimal(0))
    # The market holds the subsidy plus all net stakes; refunds can never exceed that.
    pool = total_subsidy + sum((r["n"] for r in rows), Decimal(0))
    total = sum((amount for _, _, amount in wanted), Decimal(0))
    subsidy_back = max(Decimal(0), min(total_subsidy, pool - total)) if refund_subsidy else Decimal(0)
    available = max(Decimal(0), pool - subsidy_back)
    scale = Decimal(1) if total <= available else available / total
    for qid, tid, amount in sorted(wanted):
        refund = _floor_cent(amount * scale)
        if refund > 0:
            target = by_q[qid]
            _credit(traders[tid], refund, LedgerEntry.Kind.REFUND, target)
            Payout.objects.create(trader=traders[tid], question=target, amount=refund, kind=Payout.Kind.REFUND)
    creator = traders.get(question.creator_id) if question.creator_id else None
    subsidy_back = _floor_cent(subsidy_back)
    if creator is not None and subsidy_back > 0:
        _credit(creator, subsidy_back, LedgerEntry.Kind.SUBSIDY_RETURN, question)
    now = timezone.now()
    for sub in subs:
        if sub.status not in (Question.Status.RESOLVED, Question.Status.ANNULLED):
            sub.status = Question.Status.ANNULLED
            sub.resolved_at = now
            sub.outcome = None
            sub.save(update_fields=["status", "outcome", "resolved_at"])
    question.status = Question.Status.ANNULLED
    question.resolved_at = now
    question.outcome = None
    question.save(update_fields=["status", "outcome", "resolved_at"])
    from .notifications import market_finished
    market_finished(question)
    return question


@transaction.atomic
def cancel_moderated(question: Question) -> Question:
    """Full net-investment refunds, platform-funded shortfall, no creator subsidy return.

    Separate from normal annulment: existing cancellation policies are unchanged.
    The caller must check moderator permissions; all money mutations remain here.
    """
    from django.db.models import Sum
    from .models import Dispute
    root_id = question.parent_id or question.pk
    question = Question.objects.select_for_update().get(pk=root_id)
    if question.status not in (Question.Status.OPEN, Question.Status.CLOSED):
        raise MarketError("Рынок уже завершён или отменён. Повторный возврат невозможен.")
    if not question.is_trading:
        raise MarketError("Отмена с возвратом доступна только для торгового рынка.")
    subs = list(question.submarkets.select_for_update().order_by("id"))
    unsettled_subs = [s for s in subs if s.status not in (Question.Status.RESOLVED, Question.Status.ANNULLED)]
    family = [question, *unsettled_subs]
    family_ids = [q.pk for q in family]
    by_q = {q.pk: q for q in family}
    rows = list(Trade.objects.filter(question_id__in=family_ids)
                .values('question_id', 'trader_id').annotate(amount=Sum('cost')))
    disputes = list(Dispute.objects.select_for_update().filter(
        question_id__in=[question.pk, *(s.pk for s in subs)], upheld__isnull=True).order_by('trader_id', 'pk'))
    bond_holder = {question.proposed_by_id} if question.proposal_bond > 0 and question.proposed_by_id else set()
    _, traders = _lock_family_positions(
        family, question.creator_id, {r['trader_id'] for r in rows} | {d.trader_id for d in disputes} | bond_holder)
    wanted = [(r['question_id'], r['trader_id'], r['amount']) for r in rows if r['amount'] > 0]
    total = sum((amount for _, _, amount in wanted), Decimal(0))
    total_subsidy = question.subsidy + sum((s.subsidy for s in unsettled_subs), Decimal(0))
    available = max(Decimal(0), total_subsidy + sum((r['amount'] for r in rows), Decimal(0)))
    question.refund_compensation = max(Decimal(0), total - available)
    for qid, tid, amount in sorted(wanted):
        target = by_q[qid]
        _credit(traders[tid], amount, LedgerEntry.Kind.REFUND, target)
        Payout.objects.create(trader=traders[tid], question=target, amount=amount, kind=Payout.Kind.REFUND)
    for dispute in disputes:
        _credit(traders[dispute.trader_id], dispute.stake, LedgerEntry.Kind.DISPUTE_REFUND, question)
        dispute.upheld = True
        dispute.save(update_fields=['upheld'])
    if bond_holder:
        # A complaint cancelled the market while a participant's outcome waited: nobody overturned it.
        _credit(traders[question.proposed_by_id], question.proposal_bond,
                LedgerEntry.Kind.PROPOSAL_BOND_REFUND, question)
    now = timezone.now()
    hidden_at = question.hidden_at or now
    for sub in subs:
        sub.status = Question.Status.ANNULLED
        sub.outcome = None
        sub.resolution_state = Question.Resolution.FINAL
        sub.creator_at_fault = True
        sub.moderation_hidden = True
        sub.hidden_at = sub.hidden_at or hidden_at
        sub.resolved_at = now
        sub.dispute_until = None
        sub.save(update_fields=['status', 'outcome', 'resolution_state', 'creator_at_fault',
                                'moderation_hidden', 'hidden_at', 'resolved_at', 'dispute_until'])
    question.status = Question.Status.ANNULLED
    question.outcome = None
    question.resolution_state = Question.Resolution.FINAL
    question.creator_at_fault = True
    question.moderation_hidden = True
    question.hidden_at = hidden_at
    question.resolved_at = now
    question.dispute_until = None
    question.save(update_fields=['status', 'outcome', 'resolution_state', 'creator_at_fault',
        'moderation_hidden', 'hidden_at', 'resolved_at', 'dispute_until', 'refund_compensation'])
    from .notifications import market_finished
    market_finished(question)
    # The ordinary worker sends final result notifications from committed state.
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
    **extra,
) -> Question:
    b = float(b if b is not None else settings.LMSR_DEFAULT_B)
    q_yes, q_no = lmsr.initial_q(initial_prob, b)
    with transaction.atomic():
        question = Question.objects.create(
            title=title, resolution_criteria=resolution_criteria, source_type=source_type,
            source_ref=source_ref, deadline=deadline, initial_prob=initial_prob, b=b,
            q_yes=q_yes, q_no=q_no, group_id=group_id, tracks_deadline=source_type == Question.Source.GITHUB,
            **extra,
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
    """Wealth gained by forecasting and running markets; bonus coins do not count."""
    trader.refresh_from_db(fields=["balance"])
    from django.db.models import Sum
    daily = trader.daily_rewards.aggregate(total=Sum("coins"))["total"] or Decimal(0)
    # Daily bonuses are counted via DailyReward (it predates the ledger); the starter pack via START_BALANCE.
    other = trader.ledger.filter(kind__in=[LedgerEntry.Kind.INSURANCE, LedgerEntry.Kind.REFERRAL]).aggregate(
        total=Sum("amount"))["total"] or Decimal(0)
    locked = trader.created_questions.exclude(
        status__in=[Question.Status.RESOLVED, Question.Status.ANNULLED]).aggregate(total=Sum("subsidy"))["total"] or Decimal(0)
    return trader.balance + portfolio_value(trader) + locked - Decimal(settings.START_BALANCE) - daily - other


@transaction.atomic
def claim_daily_reward(trader):
    from zoneinfo import ZoneInfo
    from django.utils import timezone
    from .models import DailyReward
    trader = Trader.objects.select_for_update().get(pk=trader.pk)
    day = timezone.localdate(timezone=ZoneInfo("Europe/Moscow"))
    reward, created = DailyReward.objects.get_or_create(
        trader=trader, day=day, defaults={"coins": Decimal(settings.DAILY_BONUS)})
    if created:
        _credit(trader, reward.coins, LedgerEntry.Kind.DAILY)
    return reward if created else None


def skip_daily_reward_today(trader: Trader) -> None:
    """The starter pack is the first day's gift; the daily bonus starts tomorrow."""
    from zoneinfo import ZoneInfo
    from .models import DailyReward
    day = timezone.localdate(timezone=ZoneInfo("Europe/Moscow"))
    DailyReward.objects.get_or_create(trader=trader, day=day, defaults={"coins": Decimal(0), "xp": 0})


def _fmt(value: Decimal) -> str:
    return f"{value.normalize():f}".replace(".", ",")


@transaction.atomic
def grant_starter_pack(trader: Trader) -> LedgerEntry | None:
    """Starter coins, once per account (enforced by a unique constraint too)."""
    trader = Trader.objects.select_for_update().get(pk=trader.pk)
    if trader.ledger.filter(kind=LedgerEntry.Kind.STARTER).exists():
        return None
    amount = Decimal(settings.START_BALANCE)
    trader.balance += amount
    trader.save(update_fields=["balance"])
    return _record(trader, LedgerEntry.Kind.STARTER, amount)


def has_open_positions(trader: Trader) -> bool:
    """Coins still at stake: shares in unsettled markets or the subsidy of an own unsettled market."""
    from django.db.models import Q
    unsettled = [Question.Status.OPEN, Question.Status.CLOSED]
    return trader.positions.filter(
        Q(yes_shares__gt=0) | Q(no_shares__gt=0), question__status__in=unsettled,
    ).exists() or trader.created_questions.filter(status__in=unsettled, subsidy__gt=0).exists()


@transaction.atomic
def claim_insurance(trader: Trader) -> LedgerEntry | None:
    """Weekly top-up for a trader who lost everything and has nothing at stake."""
    from datetime import timedelta
    trader = Trader.objects.select_for_update().get(pk=trader.pk)
    if trader.balance >= Decimal(settings.INSURANCE_THRESHOLD) or has_open_positions(trader):
        return None
    since = timezone.now() - timedelta(days=settings.INSURANCE_PERIOD_DAYS)
    if trader.ledger.filter(kind=LedgerEntry.Kind.INSURANCE, created_at__gt=since).exists():
        return None
    amount = Decimal(settings.INSURANCE_AMOUNT)
    trader.balance += amount
    trader.save(update_fields=["balance"])
    return _record(trader, LedgerEntry.Kind.INSURANCE, amount)


def check_market_fields(*, title: str, criteria: str, category: str, closes_at, resolves_at, now=None) -> None:
    """Rules for the fields of a new user market (stripped title and criteria, aware dates); raises MarketError.

    No side effects: the AI helper checks its suggestions with the same rules (market/assist.py).
    """
    from datetime import timedelta
    from .models import CATEGORIES
    if not title:
        raise MarketError("Напишите вопрос.")
    if len(title) > 120:
        raise MarketError("Вопрос — не длиннее 120 символов.")
    if not criteria:
        raise MarketError("Опишите, как определяем результат.")
    if category not in dict(CATEGORIES):
        raise MarketError("Выберите категорию.")
    now = now or timezone.now()
    if closes_at is None or resolves_at is None:
        raise MarketError("Укажите, до какого момента принимаем прогнозы и когда станет известен итог.")
    if closes_at < now + timedelta(hours=settings.MARKET_MIN_HOURS):
        raise MarketError("Приём прогнозов — хотя бы час.")
    if closes_at > now + timedelta(days=settings.MARKET_MAX_DAYS):
        raise MarketError("Приём прогнозов — не дольше года.")
    if resolves_at < closes_at:
        raise MarketError("Итог не может стать известен раньше конца приёма прогнозов.")
    if resolves_at > closes_at + timedelta(days=settings.MARKET_MAX_DAYS):
        raise MarketError("Итог должен стать известен не позже чем через год после конца приёма.")


@transaction.atomic
def create_market(trader: Trader, *, title: str, criteria: str, category: str, closes_at, resolves_at,
                  request_key=None, group=None, options: list[str] | None = None,
                  announce: bool = False, expected_cost: int | None = None,
                  ai_check: bool = False, ai_source: str = "") -> Question:
    """User-created YES/NO or multi-option market: the creator pays the market maker's subsidy.

    With `request_key` the call is idempotent: a repeat by the same creator returns the market
    created first (marked `replayed`) without charging again or counting against the daily limit.
    With `group` (a closed chat, spec E8.3) the market is the chat's: the creator must be a member
    by the last check (the caller refreshes it with `chats.check_member`), the chat must not be frozen,
    and the price and depth follow the chat's rules and stay fixed in the market.
    `announce` (chat markets only, spec E8.4): the bot announces the market in the chat.
    `expected_cost`: the price the creator saw; if the chat's price has moved, PriceChanged and no charge.
    `ai_check`, `ai_source`: «Итог проверит ИИ» (spec E4): only a public YES/NO market, otherwise ignored.
    """
    from datetime import timedelta
    from uuid import UUID
    from django.db import IntegrityError
    trader = Trader.objects.select_for_update().get(pk=trader.pk)
    title = (title or "").strip()
    criteria = (criteria or "").strip()
    cleaned_options: list[str] = []
    if options is not None and options != []:
        if not isinstance(options, list):
            raise MarketError("Укажите от 2 до 10 разных вариантов ответа.")
        cleaned_options = [str(o or "").strip() for o in options if str(o or "").strip()]
        if (len(cleaned_options) < 2 or len(cleaned_options) > 10
                or len({o.lower() for o in cleaned_options}) != len(cleaned_options)):
            raise MarketError("Укажите от 2 до 10 разных вариантов ответа.")
        if any(len(o) > 80 for o in cleaned_options):
            raise MarketError("Вариант — не длиннее 80 символов.")
    if request_key is not None:
        try:
            request_key = UUID(str(request_key))
        except (ValueError, TypeError, AttributeError):
            raise MarketError("Недействительный идентификатор запроса.") from None
        # The trader lock serialises repeats of one creator, so the first market is visible here.
        previous = Question.objects.filter(request_key=request_key).first()
        if previous:
            if (previous.creator_id != trader.pk or previous.title != title
                    or previous.resolution_criteria != criteria or previous.category != category
                    or previous.group_id != (group.pk if group is not None else None)):
                raise MarketError("Этот идентификатор уже использован для другого рынка. Обновите страницу.")
            previous.replayed = True
            return previous
    now = timezone.now()
    check_market_fields(title=title, criteria=criteria, category=category, closes_at=closes_at,
                        resolves_at=resolves_at, now=now)
    recent = Question.objects.filter(
        creator=trader, parent__isnull=True, created_at__gte=now - timedelta(days=1),
    ).count()
    if recent >= settings.MARKETS_PER_DAY:
        raise MarketError(f"Не больше {settings.MARKETS_PER_DAY} новых рынков в день.")
    cost_coins, b = settings.MARKET_CREATION_COST, settings.LMSR_DEFAULT_B
    if group is not None:
        from . import chats
        from .models import GroupMembership
        if not group.is_chat:
            raise MarketError("Рынок можно создать только для чата.")
        if not GroupMembership.objects.filter(group=group, trader=trader, status="active").exists():
            raise MarketError("Рынок для чата создаёт только участник чата.")
        if not group.bot_active:
            raise MarketError("Бот больше не админ в этом чате — новые рынки создать нельзя.")
        cost_coins = chats.creation_cost(group)
        b = chats.depth(cost_coins)
    if cleaned_options:
        # Every option is an independent YES/NO market (several may resolve ДА), each losing at most b * ln 2;
        # splitting the depth keeps the whole family's loss within the creator's subsidy.
        from . import chats
        b = chats.depth(cost_coins, len(cleaned_options))
        if b < 1:
            raise MarketError("Для такой цены рынка слишком много вариантов.")
    ai_source = (ai_source or "").strip()
    use_ai = bool(ai_check and ai_source) and group is None and not cleaned_options
    if use_ai and (len(ai_source) > 120 or "://" in ai_source):
        raise MarketError("Источник — коротким названием, без ссылки.")
    cost = Decimal(cost_coins)
    if expected_cost is not None and expected_cost != cost_coins:
        raise PriceChanged(cost_coins)
    if trader.balance < cost:
        raise MarketError(f"Создание рынка стоит {cost_coins} монет — на балансе не хватает.")
    try:
        question = create_question(
            title=title, resolution_criteria=criteria, deadline=closes_at, initial_prob=0.5,
            b=b, creator=trader, category=category, resolves_at=resolves_at, subsidy=cost,
            request_key=request_key, group_id=group.pk if group is not None else None,
            options=cleaned_options, announce=bool(announce and group is not None),
            ai_check=use_ai, ai_source=ai_source if use_ai else "",
        )
    except IntegrityError:
        # Another trader raced us with the same key; never reveal their market.
        raise MarketError("Этот идентификатор уже использован для другого рынка. Обновите страницу.") from None
    for label in cleaned_options:
        create_question(
            title=f"{title} — {label}"[:240], option_label=label, parent=question,
            resolution_criteria=criteria, deadline=closes_at, initial_prob=0.5,
            b=b, creator=trader, category=category, resolves_at=resolves_at,
            subsidy=Decimal(0), group_id=group.pk if group is not None else None,
        )
    question.replayed = False
    trader.balance -= cost
    trader.save(update_fields=["balance"])
    _record(trader, LedgerEntry.Kind.CREATION, -cost, question)
    return question


def purchase_snapshot(question):
    return {field: getattr(question, field) for field in ("kind", "b", "alpha", "q_yes", "q_no", "pool_yes", "pool_no")}


def prepare_purchase(trader, question, side, amount):
    import hashlib
    import secrets
    from datetime import timedelta
    from .models import PurchaseConfirmation
    check_access(trader, question)
    check_not_option_root(question)
    check_can_trade(trader, question)
    amount = _d(amount)
    if amount > max_buy_amount(trader, question):
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


# --- resolution by the creator or a participant, disputes (spec E4) ----------------------------

def _participant(trader: Trader, question: Question) -> bool:
    from django.db.models import Q
    root_id = question.parent_id or question.pk
    return Trade.objects.filter(trader=trader).filter(Q(question_id=root_id) | Q(question__parent_id=root_id)).exists()


def outcome_author_id(question: Question) -> int | None:
    """Who stated the outcome: `proposed_by`, the creator for outcomes stated before phase 3d, nobody for AI's."""
    if question.proposed_by_ai:
        return None
    return question.proposed_by_id or question.creator_id


def by_participant(question: Question) -> bool:
    """The outcome was stated by a participant because the creator stayed silent (phase 3d)."""
    return bool(question.proposed_by_id) and question.proposed_by_id != question.creator_id


def not_by_creator(question: Question) -> bool:
    """The creator stayed silent and someone else's outcome stands in: they aren't to blame if it's wrong."""
    return by_participant(question) or question.proposed_by_ai


def has_evidence(question: Question) -> bool:
    return bool(question.evidence or question.evidence_file_id or question.evidence_note)


def evidence_deadline(question: Question):
    """Until when the outcome's author may send proof for a disputed outcome; None — nothing was asked."""
    from datetime import timedelta
    if question.evidence_requested_at is None:
        return None
    return question.evidence_requested_at + timedelta(hours=settings.EVIDENCE_HOURS)


def participant_proposal_from(question: Question):
    """From when participants may state the outcome; None — the market has no outcome date."""
    from datetime import timedelta
    if question.resolves_at is None:
        return None
    return question.resolves_at + timedelta(hours=settings.PARTICIPANT_PROPOSAL_HOURS)


def _lock_root(question: Question) -> tuple[Question, list[Question]]:
    root_id = question.parent_id or question.pk
    root = Question.objects.select_for_update().get(pk=root_id)
    return root, list(root.submarkets.select_for_update().order_by("id"))


def _outcomes_map(subs: list[Question], submarket_outcomes, error: str) -> dict[int, str]:
    if not submarket_outcomes:
        raise MarketError(error)
    try:
        outcomes = {int(k): str(v) for k, v in submarket_outcomes.items()}
    except (AttributeError, TypeError, ValueError):
        raise MarketError(error) from None
    if set(outcomes) != {s.pk for s in subs} or any(v not in ("yes", "no") for v in outcomes.values()):
        raise MarketError(error)
    return outcomes


def _state_outcome(question: Question, subs: list[Question], author: Trader | None, outcome: str,
                   outcomes_map: dict[int, str], *, bond: Decimal = Decimal(0), evidence=None,
                   evidence_file_id: str = "", note: str = "") -> None:
    """Record the stated outcome on the market and its options; a 24-hour dispute window opens."""
    from datetime import timedelta
    now = timezone.now()
    until = now + timedelta(hours=settings.DISPUTE_WINDOW_HOURS)
    question.status = Question.Status.CLOSED
    question.resolution_state = Question.Resolution.PENDING
    question.proposed_outcome = outcome
    question.proposed_at = now
    question.proposed_by = author
    question.proposal_bond = bond
    question.dispute_until = until
    question.evidence_file_id = evidence_file_id
    question.evidence_note = (note or "")[:500]
    fields = ["status", "resolution_state", "proposed_outcome", "proposed_at", "proposed_by", "proposal_bond",
              "dispute_until", "evidence_file_id", "evidence_note"]
    if evidence is not None:
        question.evidence = evidence
        fields.append("evidence")
    question.save(update_fields=fields)
    for sub in subs:
        if sub.status in (Question.Status.RESOLVED, Question.Status.ANNULLED):
            continue
        sub.status = Question.Status.CLOSED
        sub.resolution_state = Question.Resolution.PENDING
        sub.proposed_outcome = "cancel" if outcome == "cancel" else outcomes_map[sub.pk]
        sub.proposed_at = now
        sub.dispute_until = until
        sub.save(update_fields=["status", "resolution_state", "proposed_outcome", "proposed_at", "dispute_until"])


@transaction.atomic
def propose_resolution(trader: Trader, question: Question, outcome: str, *, evidence=None,
                       evidence_file_id: str = "", note: str = "",
                       submarket_outcomes: dict[int | str, str] | None = None) -> Question:
    """The creator states the outcome in one tap, a photo is optional; a 24-hour dispute window opens."""
    question, subs = _lock_root(question)
    if question.creator_id != trader.pk:
        raise MarketError("Итог указывает только создатель рынка.")
    if question.status not in (Question.Status.OPEN, Question.Status.CLOSED) or question.resolution_state:
        raise MarketError("Итог этого рынка уже указан.")
    if question.accepting_entries:
        raise MarketError("Приём прогнозов ещё идёт — итог можно указать после его окончания.")
    outcomes_map: dict[int, str] = {}
    if subs and outcome != "cancel":
        outcomes_map = _outcomes_map(subs, submarket_outcomes,
                                     "Укажите ДА или НЕТ для каждого варианта или отмените рынок.")
        outcome = "yes"
    elif outcome not in ("yes", "no", "cancel"):
        raise MarketError("Выберите ДА, НЕТ или отмену рынка.")
    _state_outcome(question, subs, trader, outcome, outcomes_map, evidence=evidence,
                   evidence_file_id=evidence_file_id, note=note)
    return question


@transaction.atomic
def propose_by_participant(trader: Trader, question: Question, outcome: str, *,
                           submarket_outcomes: dict[int | str, str] | None = None) -> Question:
    """A participant states the outcome the creator didn't give, for a bond; the first one wins (phase 3d).

    The bond comes back when the outcome stands and burns if a dispute against it is upheld.
    """
    question, subs = _lock_root(question)
    trader = Trader.objects.select_for_update().get(pk=trader.pk)
    check_access(trader, question)  # someone who left a closed chat no longer speaks for its markets
    if question.creator_id == trader.pk:
        raise MarketError("Создатель указывает итог сам, без залога.")
    if not _participant(trader, question):
        raise MarketError("Предложить итог может только участник рынка.")
    if question.resolution_state or question.status not in (Question.Status.OPEN, Question.Status.CLOSED):
        if question.proposed_by_id == trader.pk:
            raise MarketError("Ты уже предложил итог этого рынка.")
        if by_participant(question):
            raise MarketError("Итог уже предложил другой участник. Не согласен — оспорь его.")
        if question.proposed_by_id:
            raise MarketError("Автор уже указал итог. Не согласен — оспорь его.")
        raise MarketError("Итог этого рынка уже указан.")
    if question.accepting_entries:
        raise MarketError("Приём прогнозов ещё идёт — итог можно указать после его окончания.")
    since = participant_proposal_from(question)
    if since is None or timezone.now() < since:
        raise MarketError(f"Участники могут предложить итог через {settings.PARTICIPANT_PROPOSAL_HOURS} ч "
                          "после даты итога, если автор его не укажет.")
    outcomes_map: dict[int, str] = {}
    if subs:
        if outcome == "cancel":
            raise MarketError("Укажите ДА или НЕТ для каждого варианта.")
        outcomes_map = _outcomes_map(subs, submarket_outcomes, "Укажите ДА или НЕТ для каждого варианта.")
        outcome = "yes"
    elif outcome not in ("yes", "no"):
        raise MarketError("Выберите ДА или НЕТ.")
    bond = Decimal(settings.PROPOSAL_BOND)
    if trader.balance < bond:
        raise MarketError(f"Предложить итог стоит {settings.PROPOSAL_BOND} монет — на балансе не хватает.")
    trader.balance -= bond
    trader.save(update_fields=["balance"])
    _record(trader, LedgerEntry.Kind.PROPOSAL_BOND, -bond, question)
    _state_outcome(question, subs, trader, outcome, outcomes_map, bond=bond)
    return question


@transaction.atomic
def propose_by_ai(question: Question) -> Question:
    """AI's proposal becomes the stated outcome because the creator stayed silent (spec E4, phase 3e).

    No bond and no share of fees: the creator's share burns, the subsidy comes back as usual.
    """
    question, subs = _lock_root(question)
    if question.resolution_state or question.status not in (Question.Status.OPEN, Question.Status.CLOSED):
        raise MarketError("Итог этого рынка уже указан.")
    if question.accepting_entries:
        raise MarketError("Приём прогнозов ещё идёт — итог можно указать после его окончания.")
    if subs or question.group_id or question.ai_state != "proposed" or question.ai_outcome not in ("yes", "no"):
        raise MarketError("У этого рынка нет предложения ИИ.")
    since = participant_proposal_from(question)
    if since is None or timezone.now() < since:
        raise MarketError("Создатель ещё может указать итог сам.")
    _state_outcome(question, [], None, question.ai_outcome, {}, note=question.ai_evidence_url)
    question.proposed_by_ai = True
    question.save(update_fields=["proposed_by_ai"])
    return question


@transaction.atomic
def open_dispute(trader: Trader, question: Question, reason: str = ""):
    """A participant challenges the stated outcome; the stake comes back if the dispute is upheld.

    The creator may challenge a participant's outcome without being a participant (phase 3d).
    The first dispute asks the outcome's author for proof unless it is already there.
    """
    from .models import Dispute
    question, subs = _lock_root(question)
    trader = Trader.objects.select_for_update().get(pk=trader.pk)
    if question.resolution_state not in (Question.Resolution.PENDING, Question.Resolution.DISPUTED):
        raise MarketError("Оспорить можно только итог в окне спора.")
    if question.dispute_until is None or question.dispute_until <= timezone.now():
        raise MarketError("Окно спора закрыто.")
    creator_vs_participant = trader.pk == question.creator_id and not_by_creator(question)
    if not creator_vs_participant and (trader.pk == question.creator_id or not _participant(trader, question)):
        raise MarketError("Оспорить итог может только участник рынка.")
    if trader.pk == outcome_author_id(question):
        raise MarketError("Свой итог оспорить нельзя.")
    if Dispute.objects.filter(question=question, trader=trader).exists():
        raise MarketError("Вы уже оспорили этот итог.")
    stake = Decimal(settings.DISPUTE_COST)
    if trader.balance < stake:
        raise MarketError(f"Спор стоит {settings.DISPUTE_COST} монет — на балансе не хватает.")
    trader.balance -= stake
    trader.save(update_fields=["balance"])
    _record(trader, LedgerEntry.Kind.DISPUTE, -stake, question)
    dispute = Dispute.objects.create(question=question, trader=trader, stake=stake, reason=(reason or "")[:500])
    question.resolution_state = Question.Resolution.DISPUTED
    fields = ["resolution_state"]
    ask_proof = question.evidence_requested_at is None and not has_evidence(question)
    if ask_proof:
        question.evidence_requested_at = timezone.now()
        fields.append("evidence_requested_at")
    question.save(update_fields=fields)
    for sub in subs:
        sub.resolution_state = Question.Resolution.DISPUTED
        sub.save(update_fields=["resolution_state"])
    if not creator_vs_participant:  # the creator doesn't need to hear about their own dispute
        _after_commit("dispute_opened", question.pk)
    if ask_proof:
        # Only a row in the durable bot queue: nothing here talks to Telegram.
        from .bot_resolution import request_evidence
        request_evidence(question)
    return dispute


@transaction.atomic
def attach_evidence(trader: Trader, question: Question, *, evidence=None, evidence_file_id: str = "",
                    note: str = "") -> Question:
    """The outcome's author sends proof — a photo, a file or a link — for a disputed outcome (phase 3d)."""
    question, _ = _lock_root(question)
    if outcome_author_id(question) != trader.pk:
        raise MarketError("Доказательство присылает тот, кто указал итог.")
    if question.resolution_state != Question.Resolution.DISPUTED:
        raise MarketError("Доказательство нужно, только когда итог оспорили.")
    deadline = evidence_deadline(question)
    if deadline is not None and timezone.now() >= deadline:
        raise MarketError("Время на доказательство вышло — спор решат без него.")
    note = (note or "").strip()[:500]
    if evidence is None and not evidence_file_id and not note:
        raise MarketError("Пришлите фото, скриншот или ссылку.")
    fields = []
    if evidence is not None:
        question.evidence = evidence
        fields.append("evidence")
    if evidence_file_id or evidence is not None:
        question.evidence_file_id = evidence_file_id
        fields.append("evidence_file_id")
    if note:
        question.evidence_note = note
        fields.append("evidence_note")
    question.save(update_fields=fields)
    return question


def _apply(question: Question, decision: str, *, creator_at_fault: bool, outcome_stands: bool,
           submarket_outcomes: dict[int, str] | None = None) -> Question:
    """Settle the market. A participant whose outcome stands gets the bond back and the creator's share
    of fees; an overturned one loses the bond, and that share burns."""
    subs = list(question.submarkets.select_for_update().order_by("id"))
    participant_id = question.proposed_by_id if by_participant(question) else None
    fee_to_id = participant_id if outcome_stands else None
    burn_fee = question.proposed_by_ai or (participant_id is not None and not outcome_stands)
    if decision == "cancel":
        question = annul(question, refund_subsidy=not creator_at_fault)
    elif subs:
        outcomes = submarket_outcomes or {s.pk: s.proposed_outcome for s in subs}
        question = _resolve_family(question, subs, outcomes, refund_subsidy=not creator_at_fault,
                                   fee_to_id=fee_to_id, burn_fee=burn_fee)
    else:
        question = resolve(question, decision, refund_subsidy=not creator_at_fault,
                           fee_to_id=fee_to_id, burn_fee=burn_fee)
    if fee_to_id is not None and question.proposal_bond > 0:
        proposer = Trader.objects.select_for_update().get(pk=fee_to_id)
        _credit(proposer, question.proposal_bond, LedgerEntry.Kind.PROPOSAL_BOND_REFUND, question)
    for sub in subs:
        sub.resolution_state = Question.Resolution.FINAL
        sub.creator_at_fault = creator_at_fault
        sub.save(update_fields=["resolution_state", "creator_at_fault"])
    question.resolution_state = Question.Resolution.FINAL
    question.creator_at_fault = creator_at_fault
    question.save(update_fields=["resolution_state", "creator_at_fault"])
    return question


@transaction.atomic
def finalize_resolution(question: Question) -> Question:
    """No dispute within the window: the stated outcome stands."""
    root_id = question.parent_id or question.pk
    question = Question.objects.select_for_update().get(pk=root_id)
    if question.resolution_state != Question.Resolution.PENDING:
        raise MarketError("Рынок не ждёт окончания окна спора.")
    if question.dispute_until > timezone.now():
        raise MarketError("Окно спора ещё открыто.")
    return _apply(question, question.proposed_outcome, creator_at_fault=False, outcome_stands=True)


@transaction.atomic
def moderator_decide(question: Question, decision: str, *,
                     submarket_outcomes: dict[int | str, str] | None = None) -> Question:
    """Final word on a dispute. Upheld disputes get their stake back; a wrong creator loses the subsidy,
    a wrong participant loses the bond (the creator is not punished for a participant's mistake)."""
    from .models import Dispute
    if decision not in ("yes", "no", "cancel"):
        raise MarketError("Решение: ДА, НЕТ или отмена.")
    question, subs = _lock_root(question)
    if question.resolution_state != Question.Resolution.DISPUTED:
        raise MarketError("По этому рынку нет открытого спора.")
    participant = not_by_creator(question)
    if decision == "cancel" and not participant:
        question = cancel_moderated(question)
        _after_commit("moderator_decided", question.pk, decision, True)
        return question
    outcomes_map: dict[int, str] | None = None
    if decision == "cancel":
        upheld = True  # a participant never proposes a cancellation
    elif subs:
        if submarket_outcomes is not None:
            outcomes_map = _outcomes_map(subs, submarket_outcomes, "Укажите ДА или НЕТ для каждого варианта.")
            upheld = question.proposed_outcome == "cancel" or any(outcomes_map[s.pk] != s.proposed_outcome for s in subs)
        elif decision == question.proposed_outcome and question.proposed_outcome != "cancel":
            outcomes_map = {s.pk: s.proposed_outcome for s in subs}
            upheld = False
        else:
            raise MarketError("Для рынка с вариантами подтвердите указанный итог или отмените рынок.")
    else:
        upheld = decision != question.proposed_outcome
    disputes = list(Dispute.objects.select_for_update().filter(question=question, upheld__isnull=True).order_by("trader_id"))
    for dispute in disputes:
        dispute.upheld = upheld
        dispute.save(update_fields=["upheld"])
        if upheld:
            _credit(Trader.objects.select_for_update().get(pk=dispute.trader_id), dispute.stake,
                    LedgerEntry.Kind.DISPUTE_REFUND, question)
    question = _apply(question, decision, creator_at_fault=upheld and not participant, outcome_stands=not upheld,
                      submarket_outcomes=outcomes_map)
    if participant:
        _after_commit("participant_outcome_decided", question.pk, decision, upheld, module="bot_resolution")
    else:
        _after_commit("moderator_decided", question.pk, decision, upheld)
    return question


@transaction.atomic
def cancel_unresolved(question: Question) -> Question:
    """Nobody stated the outcome past the deadline: everyone, the creator included, gets their coins back."""
    root_id = question.parent_id or question.pk
    question = Question.objects.select_for_update().get(pk=root_id)
    if question.resolution_state or question.status not in (Question.Status.OPEN, Question.Status.CLOSED):
        raise MarketError("Итог уже указан.")
    question = annul(question)
    question.submarkets.update(resolution_state=Question.Resolution.FINAL)
    question.resolution_state = Question.Resolution.FINAL
    question.save(update_fields=["resolution_state"])
    return question


# --- notifications and referral rewards (spec E6.3, E5) ----------------------------------------

def _after_commit(event: str, *args, module: str = "bot_notifications") -> None:
    """Notify once the money is committed; a notification failure never rolls it back."""
    def run():
        import importlib
        getattr(importlib.import_module(f"{__package__}.{module}"), event)(*args)
    transaction.on_commit(run, robust=True)


def _moscow_month_start():
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    msk = ZoneInfo("Europe/Moscow")
    today = timezone.localdate(timezone=msk)
    return datetime.combine(today.replace(day=1), time(0), tzinfo=msk)


@transaction.atomic
def grant_referral_reward(friend: Trader):
    """Both the friend and the inviter get REFERRAL_REWARD once the friend bought in REFERRAL_PREDICTIONS markets.

    Over the inviter's monthly cap nobody is paid (so account farms don't pay off); the row still records it.
    """
    from .models import ReferralReward
    if not friend.referred_by_id or friend.referred_by_id == friend.pk:
        return None
    if ReferralReward.objects.filter(friend_id=friend.pk).exists():
        return None
    from django.db.models.functions import Coalesce
    markets = (Trade.objects.filter(trader_id=friend.pk, shares__gt=0)
               .annotate(root_id=Coalesce("question__parent_id", "question_id"))
               .values("root_id").distinct().count())
    if markets < settings.REFERRAL_PREDICTIONS:
        return None
    locked = {t.pk: t for t in Trader.objects.select_for_update()
              .filter(pk__in=[friend.pk, friend.referred_by_id]).order_by("pk")}
    friend, referrer = locked[friend.pk], locked.get(friend.referred_by_id)
    if referrer is None or ReferralReward.objects.filter(friend=friend).exists():
        return None
    granted = ReferralReward.objects.filter(referrer=referrer, granted=True,
                                            created_at__gte=_moscow_month_start()).count()
    if granted >= settings.REFERRAL_MONTHLY_CAP:
        return ReferralReward.objects.create(friend=friend, referrer=referrer, granted=False)
    reward = ReferralReward.objects.create(friend=friend, referrer=referrer, granted=True)
    amount = Decimal(settings.REFERRAL_REWARD).quantize(CENT)
    _credit(friend, amount, LedgerEntry.Kind.REFERRAL)
    _credit(referrer, amount, LedgerEntry.Kind.REFERRAL)
    _after_commit("referral_granted", friend.pk, referrer.pk)
    return reward
