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


def market_list(*, search: str = "", status: str = "", source: str = "", sort: str = "new") -> QuerySet:
    """Return market cards with their list-page aggregates."""
    from .models import Trade
    volumes = Trade.objects.filter(question_id=OuterRef("pk")).order_by().values("question_id").annotate(total=Sum(Abs("cost"))).values("total")
    qs = Question.objects.annotate(
        n_traders=Count("trades__trader", distinct=True),
        n_trades=Count("trades", distinct=True),
        n_answers=Count("submissions", distinct=True),
        volume=Subquery(volumes, output_field=DecimalField()),
    )
    if search:
        qs = qs.filter(Q(title__icontains=search) | Q(resolution_criteria__icontains=search))
    if status in Question.Status.values:
        qs = qs.filter(status=status)
    if source in Question.Source.values:
        qs = qs.filter(source_type=source)
    return qs.order_by(MARKET_SORTS.get(sort, MARKET_SORTS["new"]), "-id")
