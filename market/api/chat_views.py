"""Closed chat spaces in the Mini App (spec E8.2, E8.3; docs/api.md «Фаза 3b»).

A chat is found by its link code; an unknown code and a chat the trader is not in both answer 404.
"""
from __future__ import annotations

from django.db.models import Case, F, IntegerField, Value, When
from django.utils import timezone

from .. import chats
from ..access import visible_questions
from ..models import Question
from . import serializers as s
from .views import PAGE, chat_for, endpoint

ACTIVE = [Question.Status.OPEN, Question.Status.CLOSED]


@endpoint("GET")
def chat_list(request):
    """Chats by the last known membership, without asking Telegram; chats with open markets first."""
    trader = request.trader
    spaces = [s.chat_space(group, trader) for group in chats.chats_of(trader)]
    spaces.sort(key=lambda space: (space["markets"] == 0, space["title"].lower(), space["code"]))
    return {"items": spaces}


@endpoint("GET")
def chat(request, code):
    return s.chat_space(chat_for(request.trader, code), request.trader)


@endpoint("GET")
def chat_markets(request, code):
    trader = request.trader
    group = chat_for(trader, code)
    qs = (visible_questions(trader).filter(group=group, kind=Question.Kind.LMSR, hidden_at__isnull=True)
          .select_related("group")
          .annotate(finished=Case(When(status__in=ACTIVE, then=Value(0)), default=Value(1),
                                  output_field=IntegerField()))
          # Open and awaiting an outcome first, newest on top; then finished ones, latest outcome on top.
          .order_by("finished", F("resolved_at").desc(nulls_last=True), "-created_at", "-id"))
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1
    total = qs.count()
    items = list(qs[(page - 1) * PAGE: page * PAGE])
    now = timezone.now()
    starred = s.starred_ids(trader)
    sides = s.my_sides(trader, [q.pk for q in items])
    return {"items": [s.card(q, starred=starred, now=now, my_side=sides.get(q.pk)) for q in items],
            "next_page": page + 1 if page * PAGE < total else None, "total": total}


@endpoint("GET")
def chat_rating(request, code):
    from ..rating import leaderboard
    trader = request.trader
    group = chat_for(trader, code)
    return leaderboard(trader, group=group, by=request.GET.get("by", "profit"),
                       period=request.GET.get("period", "all"))
