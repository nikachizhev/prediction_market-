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


def _d(x: float | Decimal | int | str) -> Decimal:
    value = x if isinstance(x, Decimal) else Decimal(repr(float(x)) if isinstance(x, float) else str(x))
    if not value.is_finite():
        raise MarketError("Введите конечное число.")
    return value


def check_open(question):
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
    pos = Position.objects.filter(trader=trader, question=question).first()
    return pos.spent if pos else Decimal(0)


def max_buy_amount(trader: Trader, question: Question) -> Decimal:
    room = Decimal(settings.MARKET_CAP) - invested(trader, question)
    return max(Decimal(0), min(trade_limit(trader), room, trader.balance))


def check_can_trade(trader: Trader, question: Question) -> None:
    if question.creator_id is not None and question.creator_id == trader.pk:
        raise MarketError("Создатель рынка не может делать прогнозы на свой рынок.")
    if question.conflicted_traders.filter(pk=trader.pk).exists():
        raise MarketError("Конфликт интересов: вы не можете ставить на этот вопрос.")


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
    check_can_trade(trader, question)
    if amount <= 0:
        raise MarketError("Сумма ставки должна быть положительной.")
    if amount > trader.balance:
        raise MarketError("Недостаточно средств на балансе.")
    limit = trade_limit(trader)
    if amount > limit:
        raise MarketError(f"Максимум за одну сделку — {_fmt(limit)}: 20% баланса, но не больше {settings.MAX_TRADE}.")
    room = Decimal(settings.MARKET_CAP) - invested(trader, question)
    if amount > room:
        raise MarketError(f"В один рынок можно вложить не больше {settings.MARKET_CAP}. Осталось {_fmt(max(room, Decimal(0)))}.")
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
    if question.status == Question.Status.OPEN:
        question.status = Question.Status.CLOSED
        question.save(update_fields=["status"])
    return question


def _lock_positions(question: Question) -> tuple[list[Position], dict[int, Trader]]:
    """Lock positions, their holders and the creator (in pk order, one object per trader)."""
    positions = list(Position.objects.select_for_update().filter(question=question).order_by("trader_id"))
    ids = {p.trader_id for p in positions}
    if question.creator_id:
        ids.add(question.creator_id)
    traders = {t.pk: t for t in Trader.objects.select_for_update().filter(pk__in=ids).order_by("pk")}
    for p in positions:
        p.trader = traders[p.trader_id]
    return positions, traders


def _settle_creator(question: Question, traders: dict[int, Trader]) -> None:
    """Return the unused subsidy and pay the creator's share of fees; the rest burns."""
    creator = traders.get(question.creator_id) if question.creator_id else None
    if creator is None:
        return
    from django.db.models import Sum
    from django.db.models.functions import Coalesce
    zero = Decimal(0)
    trades = Trade.objects.filter(question=question).aggregate(
        cost=Coalesce(Sum("cost"), zero), fee=Coalesce(Sum("fee"), zero))
    paid = Payout.objects.filter(question=question, kind=Payout.Kind.PAYOUT).aggregate(
        total=Coalesce(Sum("amount"), zero))["total"]
    # What the market maker holds: the subsidy plus net stakes (fees excluded) minus payouts.
    maker = question.subsidy + trades["cost"] - trades["fee"] - paid
    subsidy_back = _floor_cent(min(max(maker, zero), question.subsidy))
    if subsidy_back > 0:
        _credit(creator, subsidy_back, LedgerEntry.Kind.SUBSIDY_RETURN, question)
    creator_fee = _floor_cent(question.fee_pool * Decimal(settings.CREATOR_FEE_SHARE))
    if creator_fee > 0:
        _credit(creator, creator_fee, LedgerEntry.Kind.CREATOR_FEE, question)


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
    positions, traders = _lock_positions(question)
    for pos in positions:
        shares = pos.yes_shares if outcome == "yes" else pos.no_shares
        amount = _floor_cent(shares)
        if amount > 0:
            _credit(pos.trader, amount, LedgerEntry.Kind.PAYOUT, question)
            Payout.objects.create(trader=pos.trader, question=question, amount=amount, kind=Payout.Kind.PAYOUT)
    _settle_creator(question, traders)
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
    positions, traders = _lock_positions(question)
    from django.db.models import Sum
    # Net money each trader put in: purchases with fees minus sale proceeds.
    net = dict(Trade.objects.filter(question=question).values("trader_id").annotate(n=Sum("cost")).values_list("trader_id", "n"))
    wanted = {tid: amount for tid, amount in net.items() if amount > 0}
    # The market holds exactly the subsidy plus all net stakes; refunds can never exceed that.
    pool = question.subsidy + sum(net.values(), Decimal(0))
    total = sum(wanted.values(), Decimal(0))
    subsidy_back = max(Decimal(0), min(question.subsidy, pool - total)) if refund_subsidy else Decimal(0)
    available = max(Decimal(0), pool - subsidy_back)
    scale = Decimal(1) if total <= available else available / total
    for tid, amount in sorted(wanted.items()):
        refund = _floor_cent(amount * scale)
        if refund > 0:
            _credit(traders[tid], refund, LedgerEntry.Kind.REFUND, question)
            Payout.objects.create(trader=traders[tid], question=question, amount=refund, kind=Payout.Kind.REFUND)
    creator = traders.get(question.creator_id) if question.creator_id else None
    subsidy_back = _floor_cent(subsidy_back)
    if creator is not None and subsidy_back > 0:
        _credit(creator, subsidy_back, LedgerEntry.Kind.SUBSIDY_RETURN, question)
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


@transaction.atomic
def create_market(trader: Trader, *, title: str, criteria: str, category: str, closes_at, resolves_at) -> Question:
    """User-created YES/NO market: the creator pays the market maker's subsidy."""
    from datetime import timedelta
    from .models import CATEGORIES
    trader = Trader.objects.select_for_update().get(pk=trader.pk)
    title = (title or "").strip()
    criteria = (criteria or "").strip()
    if not title:
        raise MarketError("Напишите вопрос.")
    if len(title) > 120:
        raise MarketError("Вопрос — не длиннее 120 символов.")
    if not criteria:
        raise MarketError("Опишите, как определяем результат.")
    if category not in dict(CATEGORIES):
        raise MarketError("Выберите категорию.")
    now = timezone.now()
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
    recent = Question.objects.filter(creator=trader, created_at__gte=now - timedelta(days=1)).count()
    if recent >= settings.MARKETS_PER_DAY:
        raise MarketError(f"Не больше {settings.MARKETS_PER_DAY} новых рынков в день.")
    cost = Decimal(settings.MARKET_CREATION_COST)
    if trader.balance < cost:
        raise MarketError(f"Создание рынка стоит {settings.MARKET_CREATION_COST} монет — на балансе не хватает.")
    question = create_question(
        title=title, resolution_criteria=criteria, deadline=closes_at, initial_prob=0.5,
        b=settings.LMSR_DEFAULT_B, creator=trader, category=category, resolves_at=resolves_at, subsidy=cost,
    )
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
