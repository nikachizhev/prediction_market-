from decimal import Decimal
from django.db.models import Sum
from .models import Trader, Trade, Submission, Payout
from .services import profit


def statistics(trader):
    positions = trader.positions.select_related("question")
    correct = evaluated = neutral = 0
    for p in positions:
        if p.question.status != "resolved":
            continue
        if p.yes_shares == p.no_shares:
            neutral += 1
            continue
        evaluated += 1
        correct += ("yes" if p.yes_shares > p.no_shares else "no") == p.question.outcome
    answers = Submission.objects.filter(trader=trader).select_related("question")
    errors = []
    for s in answers:
        q = s.question
        if q.status != "resolved" or q.kind == "poll":
            continue
        evaluated += 1
        if q.kind == "number":
            error = abs(Decimal(s.value) - Decimal(q.answer))
            errors.append(error)
            correct += error <= q.tolerance
        else:
            correct += set(s.value) == set(q.answer)
    trades = Trade.objects.filter(trader=trader)
    total = lambda qs, field: qs.aggregate(value=Sum(field))["value"] or Decimal(0)
    return {
        "trader": trader, "trades": trades.count(),
        "markets": len(set(trades.values_list("question_id", flat=True)) | set(answers.values_list("question_id", flat=True))),
        "spent": total(trades.filter(cost__gt=0), "cost"),
        "received": -total(trades.filter(cost__lt=0), "cost"),
        "payouts": total(Payout.objects.filter(trader=trader, kind="payout"), "amount"),
        "correct": correct, "evaluated": evaluated, "wrong": evaluated-correct,
        "neutral": neutral, "accuracy": round(correct/evaluated*100, 1) if evaluated else None,
        "mean_error": sum(errors)/len(errors) if errors else None,
        "profit": profit(trader),
    }


def ranking():
    rows = [statistics(t) for t in Trader.objects.filter(user__isnull=False)]
    rows.sort(key=lambda r: (-r["correct"], r["wrong"], -r["profit"], r["trader"].pk))
    for index, row in enumerate(rows, 1):
        row["rank"] = index
    return rows


def chart_data(trader):
    from datetime import timedelta
    from django.utils import timezone
    from django.db.models.functions import TruncDate
    from django.db.models import Count
    today = timezone.localdate()
    start = today - timedelta(days=29)
    activity = {}
    for model in (Trade, Submission):
        for row in model.objects.filter(trader=trader, created_at__date__gte=start).annotate(day=TruncDate("created_at")).values("day").annotate(count=Count("pk")):
            activity[row["day"]] = activity.get(row["day"], 0) + row["count"]
    days = [start + timedelta(days=i) for i in range(30)]
    # Results by market, not a reconstructed historical portfolio valuation.
    results = []
    from .access import visible_questions
    for position in trader.positions.filter(question__in=visible_questions(trader), question__status="resolved").select_related("question").order_by("question__created_at"):
        cost = Trade.objects.filter(trader=trader, question=position.question).aggregate(value=Sum("cost"))["value"] or Decimal(0)
        payout = Payout.objects.filter(trader=trader, question=position.question).aggregate(value=Sum("amount"))["value"] or Decimal(0)
        results.append((position.question.title, float(payout-cost)))
    return {"activity_labels": [day.strftime("%d.%m") for day in days],
            "activity": [activity.get(day, 0) for day in days],
            "result_labels": [title for title, _ in results[-10:]],
            "results": [value for _, value in results[-10:]]}


# --- Mini App profile (spec E7): the trader's own buys only, never anyone else's. ---

CALIBRATION_STEP = 20  # buckets 0-20%, 20-40%, ... labelled by their middle: 10, 30, 50, 70, 90


def _resolved_buys(trader):
    from .models import Question
    return (Trade.objects.filter(trader=trader, cost__gt=0, question__kind=Question.Kind.LMSR,
                                 question__status=Question.Status.RESOLVED,
                                 question__outcome__in=[Trade.Side.YES, Trade.Side.NO])
            .select_related("question"))


def _bought_price(trade) -> float:
    """Price of the bought side just before the trade, 0..1."""
    return trade.prob_before if trade.side == Trade.Side.YES else 1 - trade.prob_before


def calibration(trader) -> list[dict]:
    """«Когда ты покупаешь по 70%, сбывается в X%»: buys on resolved markets bucketed by price."""
    last = 100 // CALIBRATION_STEP - 1
    buckets: dict[int, list[int]] = {}
    for trade in _resolved_buys(trader):
        index = min(int(_bought_price(trade) * 100) // CALIBRATION_STEP, last)
        bucket = buckets.setdefault(index, [0, 0])
        bucket[0] += 1
        bucket[1] += trade.side == trade.question.outcome
    return [{"price": index * CALIBRATION_STEP + CALIBRATION_STEP // 2, "count": count, "hits": hits}
            for index, (count, hits) in sorted(buckets.items())]


def best_predictions(trader, limit: int = 3) -> list[dict]:
    """Winning buys made at the lowest prices («Купил ДА по 12% — сбылось»), best first."""
    from .access import visible_questions
    winners = [t for t in _resolved_buys(trader).filter(question__in=visible_questions(trader))
               if t.side == t.question.outcome]
    # A winning share pays 1 coin; the gain is that minus what the buy cost, fee included.
    winners.sort(key=lambda t: (_bought_price(t), t.cost - t.shares, -t.pk))
    return [{"market": {"id": t.question_id, "title": t.question.title}, "side": t.side,
             "price": round(_bought_price(t), 4), "gain": t.shares - t.cost} for t in winners[:limit]]


def best_prediction(trader) -> dict | None:
    """The single best entry of `best_predictions`."""
    best = best_predictions(trader, limit=1)
    return best[0] if best else None


def category_stats(trader) -> list[dict]:
    """«По категориям»: resolved LMSR markets the trader held a position in, grouped by category.

    `predictions`/`correct` count markets where the position leaned to one side (as in the rating);
    `profit` is the realized cash result in those markets (stakes, sales, payouts, disputes), bonus
    coins excluded. Only visible markets of known categories; categories without a resolved
    prediction are left out. Ordered by predictions, then by the category list.
    """
    from .access import visible_questions
    from .models import CATEGORIES, LedgerEntry, Position, Question
    labels = dict(CATEGORIES)
    order = {slug: i for i, (slug, _) in enumerate(CATEGORIES)}
    positions = (Position.objects.filter(trader=trader, question__in=visible_questions(trader),
                                         question__kind=Question.Kind.LMSR, question__status=Question.Status.RESOLVED,
                                         question__category__in=list(labels))
                 .select_related("question"))
    rows: dict[str, dict] = {}
    market_category: dict[int, str] = {}
    for p in positions:
        slug = p.question.category
        market_category[p.question_id] = slug
        row = rows.setdefault(slug, {"predictions": 0, "correct": 0, "profit": Decimal(0)})
        if p.yes_shares == p.no_shares:
            continue
        row["predictions"] += 1
        row["correct"] += ("yes" if p.yes_shares > p.no_shares else "no") == p.question.outcome
    money = (trader.ledger.filter(question_id__in=list(market_category)).exclude(kind__in=LedgerEntry.BONUS_KINDS)
             .values("question_id").annotate(total=Sum("amount")))
    for line in money:
        rows[market_category[line["question_id"]]]["profit"] += line["total"] or Decimal(0)
    result = [{"slug": slug, "label": labels[slug], "predictions": r["predictions"], "correct": r["correct"],
               "accuracy": round(r["correct"] / r["predictions"], 4), "profit": r["profit"]}
              for slug, r in rows.items() if r["predictions"]]
    result.sort(key=lambda r: (-r["predictions"], order[r["slug"]]))
    return result


def referral_summary(trader) -> dict:
    """«Пригласи друзей»: invited people, rewards received for them and the reward rules.

    `earned` counts only rewards for friends this trader invited, not the one received as an invited friend.
    """
    from django.conf import settings
    from .models import ReferralReward
    rewarded = ReferralReward.objects.filter(referrer=trader, granted=True).count()
    return {
        "invited": trader.referrals.count(),
        "rewarded": rewarded,
        "earned": Decimal(settings.REFERRAL_REWARD) * rewarded,
        "reward": Decimal(settings.REFERRAL_REWARD),
        "predictions_needed": settings.REFERRAL_PREDICTIONS,
    }


HISTORY_PERIODS = {"d": 1, "w": 7, "m": 30}  # days; anything else means the whole account history
HISTORY_POINTS = 200


def balance_profit_history(trader, period: str, now=None) -> list[dict]:
    """Balance and realized profit over time for the profile chart («Баланс и прибыль»).

    Points are `{t, balance, profit}` sorted by time, at most HISTORY_POINTS (evenly thinned, the
    first and last kept):
    - the period starts `HISTORY_PERIODS[period]` days before `now` (`all` or unknown: the account
      start), but never before the account start (`created_at`, else the first ledger line, else now);
    - a start point at the period start, a point at every moment the balance or the profit changed
      inside the period, and an end point at `now`;
    - `balance` is `balance_after` of the latest ledger line at that moment (before the first line:
      that line's balance minus its amount); the end point shows the current balance;
    - `profit` is the running sum of non-bonus ledger amounts since the account start. A line tied
      to a market counts only once the market is settled (resolved or annulled), at the market's
      `resolved_at`; a line without a market counts at its own time. Bonuses (starter pack, daily
      bonus, insurance, friend rewards) never count. So stakes in open markets do not show as losses.
    """
    from datetime import timedelta
    from django.utils import timezone
    from .models import LedgerEntry
    now = now or timezone.now()
    lines = list(trader.ledger.order_by("created_at", "id")
                 .values_list("created_at", "amount", "balance_after", "kind", "question_id", "question__resolved_at"))
    started = trader.created_at or (lines[0][0] if lines else now)
    if lines:
        started = min(started, lines[0][0])
    since = max(started, now - timedelta(days=HISTORY_PERIODS[period])) if period in HISTORY_PERIODS else started
    since = min(since, now)

    balance_changes = [(at, after) for at, _, after, _, _, _ in lines]
    profit_changes: dict = {}
    for at, amount, _, kind, question_id, resolved_at in lines:
        if kind in LedgerEntry.BONUS_KINDS:
            continue
        if question_id is not None:
            if resolved_at is None:
                continue
            at = max(at, resolved_at)  # a line written after settlement (e.g. a late refund) counts when written
        profit_changes[at] = profit_changes.get(at, Decimal(0)) + amount
    initial_balance = lines[0][2] - lines[0][1] if lines else trader.balance

    events = sorted({at for at, _ in balance_changes} | set(profit_changes))
    balance, profit, b_i = initial_balance, Decimal(0), 0
    points = []
    start_added = False
    for at in events:
        if at > now:
            break
        if at > since and not start_added:
            points.append((since, balance, profit))
            start_added = True
        while b_i < len(balance_changes) and balance_changes[b_i][0] <= at:
            balance = balance_changes[b_i][1]
            b_i += 1
        profit += profit_changes.get(at, Decimal(0))
        if at >= since:
            if at == since:
                start_added = True
            points.append((at, balance, profit))
    if not start_added:
        points.append((since, balance, profit))
    points.append((now, trader.balance, profit))
    if len(points) > HISTORY_POINTS:
        step = len(points) / HISTORY_POINTS
        points = [points[int(i * step)] for i in range(HISTORY_POINTS - 1)] + [points[-1]]
    return [{"t": t, "balance": b, "profit": p} for t, b, p in points]
