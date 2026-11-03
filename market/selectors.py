"""Read-only queries used by the market pages and API."""
from __future__ import annotations

from django.db.models import Count, Q, QuerySet, Sum, OuterRef, Subquery, DecimalField
from django.db.models.functions import Abs

from .models import Question


MARKET_SORTS = {
    "new": "-created_at",
    "deadline": "deadline",
    "activity": "-n_trades",
}


def market_list(*, search: str = "", status: str = "", source: str = "", sort: str = "new", trader=None) -> QuerySet:
    """Return market cards with their list-page aggregates."""
    from django.db.models import IntegerField
    from django.db.models.functions import Coalesce
    from .models import Trade
    family_trades = (Trade.objects.annotate(root_id=Coalesce("question__parent_id", "question_id"))
                     .filter(root_id=OuterRef("pk")).order_by().values("root_id"))
    volumes = family_trades.annotate(total=Sum(Abs("cost"))).values("total")
    trade_counts = family_trades.annotate(total=Count("id")).values("total")
    trader_counts = family_trades.annotate(total=Count("trader_id", distinct=True)).values("total")
    from .access import visible_questions
    qs = visible_questions(trader).filter(parent__isnull=True).annotate(
        n_traders=Coalesce(Subquery(trader_counts, output_field=IntegerField()), 0),
        n_trades=Coalesce(Subquery(trade_counts, output_field=IntegerField()), 0),
        n_answers=Count("submissions", distinct=True),
        volume=Subquery(volumes, output_field=DecimalField()),
    )
    if search:
        qs = qs.filter(Q(title__icontains=search) | Q(resolution_criteria__icontains=search))
    if status in Question.Status.values:
        from django.utils import timezone
        open_now = Q(status="open") & (Q(deadline__isnull=True) | Q(deadline__gt=timezone.now()))
        closed_poll = Q(kind="poll", status="closed")
        if status == "open":
            qs = qs.filter(open_now)
        elif status == "closed":
            qs = qs.filter(Q(status="closed") | Q(status="open", deadline__lte=timezone.now())).exclude(closed_poll)
        elif status == "resolved":
            qs = qs.filter(Q(status="resolved") | closed_poll)
        else:
            qs = qs.filter(status=status)
    if source in Question.Source.values:
        qs = qs.filter(source_type=source)
    return qs.order_by(MARKET_SORTS.get(sort, MARKET_SORTS["new"]), "-id")
