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
