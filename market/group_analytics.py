"""Explainable group risk signals and point-in-time forecast evaluation."""
from collections import defaultdict
from datetime import timedelta
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from .identity import get_trader
from .models import Group, Question, Trade


def analytics(group):
    now = timezone.now()
    questions = list(group.questions.prefetch_related("submissions").all())
    history = defaultdict(list)
    for trade in Trade.objects.filter(question__group=group).order_by("created_at", "pk"):
        history[trade.question_id].append(trade)

    def probability_at(question, moment):
        if question.created_at > moment:
            return None
        result = question.initial_prob
        for trade in history[question.pk]:
            if trade.created_at > moment:
                break
            result = trade.prob_after
        return result

    risk_rows, completed, brier = [], [], []
    for q in questions:
        trades = history[q.pk]
        people = len({t.trader_id for t in trades}) if q.is_trading else q.submissions.count()
        if q.tracks_deadline and q.is_trading and q.deadline and q.display_state in ("open", "closed"):
            baseline = probability_at(q, now-timedelta(days=7))
            baseline = q.initial_prob if baseline is None else baseline
            change = round((q.prob-baseline)*100, 1)
            last = trades[-1].created_at if trades else q.created_at
            risk = q.deadline <= now+timedelta(days=7) and q.prob < .5 and q.accepting_entries
            falling = change <= -15
            stale = last <= now-timedelta(days=3)
            risk_rows.append({"question": q, "pct": round(q.prob*100), "change": change,
                "people": people, "last": last, "risk": risk, "falling": falling,
                "thin": people < 3, "stale": stale, "waiting": q.display_state == "closed"})
        if q.display_state in ("resolved", "annulled"):
            row = {"question": q, "people": people, "prediction": None, "correct": None}
            if q.is_trading and q.status == "resolved":
                # Evaluate 24h before the planned deadline, or before an earlier resolution.
                cutoff = q.deadline-timedelta(days=1) if q.deadline else None
                if q.resolved_at and (cutoff is None or cutoff >= q.resolved_at):
                    cutoff = q.resolved_at-timedelta(days=1)
                p = probability_at(q, cutoff) if cutoff and q.resolved_at and any(t.created_at <= cutoff for t in trades) else None
                if p is not None:
                    row.update(prediction=round(p*100), correct=None if p == .5 else (p > .5) == (q.outcome == "yes"))
                    brier.append((p-int(q.outcome == "yes"))**2)
            completed.append(row)
    risk_rows.sort(key=lambda r: (not r["risk"], not r["falling"], r["question"].deadline))
    completed.sort(key=lambda r: (r["question"].resolved_at or r["question"].created_at), reverse=True)
    return {"risks": risk_rows, "risk_count": sum(r["risk"] for r in risk_rows),
        "falling_count": sum(r["falling"] for r in risk_rows), "thin_count": sum(r["thin"] or r["stale"] for r in risk_rows),
        "waiting_count": sum(r["waiting"] for r in risk_rows), "completed": completed,
        "done_count": sum(r["question"].display_state == "resolved" for r in completed),
        "cancelled_count": sum(r["question"].status == "annulled" for r in completed),
        "evaluated_count": len(brier), "brier": round(sum(brier)/len(brier), 3) if brier else None,
        "brier_baseline": .25 if brier else None,
        "deadline_yes": sum(r["question"].tracks_deadline and r["question"].outcome == "yes" for r in completed),
        "deadline_no": sum(r["question"].tracks_deadline and r["question"].outcome == "no" for r in completed),
        "risk_chart": {"labels": [r["question"].title for r in risk_rows[:12]], "values": [r["pct"] for r in risk_rows[:12]]},
        "results_chart": {"labels": ["В срок", "Не в срок"], "values": [
            sum(r["question"].tracks_deadline and r["question"].outcome == "yes" for r in completed),
            sum(r["question"].tracks_deadline and r["question"].outcome == "no" for r in completed)]}}


@login_required
def dashboard(request, pk, report=False):
    trader = get_trader(request)
    group = get_object_or_404(Group.objects.filter(memberships__trader=trader, memberships__status="active"), pk=pk)
    return render(request, "market/group_report.html" if report else "market/group_risks.html", {"group": group, **analytics(group)})
