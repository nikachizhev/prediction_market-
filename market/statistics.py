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
