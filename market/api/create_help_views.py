"""Creation wizard helpers without AI: templates and similar markets (spec E9.2, E9.3)."""
from __future__ import annotations

from django.utils import timezone

from ..market_templates import market_templates
from ..similar import similar_markets
from . import serializers as s
from .views import endpoint


@endpoint("GET")
def templates(request):
    return market_templates()


@endpoint("GET")
def similar(request):
    trader = request.trader
    items = similar_markets(trader, request.GET.get("q", ""))
    now = timezone.now()
    starred = s.starred_ids(trader)
    sides = s.my_sides(trader, [q.pk for q in items])
    return {"items": [s.card(q, starred=starred, now=now, my_side=sides.get(q.pk)) for q in items]}
