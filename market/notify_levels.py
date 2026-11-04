"""Per-market notification level (spec E6.3, phase 3d): the bell on the market card.

`all` — moves, «24 hours left» and the result; `result` — only the result; `none` — the event lands only
in the in-app inbox. An explicit choice is a `MarketNotifyLevel` row; without one the level is derived:
starred → all, a position → result, otherwise none. Resolution steps (the creator's question, the
participants' question, reminders, disputes) never depend on the level.
"""
from __future__ import annotations

from django.db.models import Q

from .models import MarketNotifyLevel, MarketSubscription, Question, Trade, Trader

LEVELS = tuple(MarketNotifyLevel.Level.values)


def _root(question: Question) -> Question:
    return question.parent or question


def default_level(trader: Trader, question: Question) -> str:
    root = _root(question)
    family = Q(question=root) | Q(question__parent=root)
    if MarketSubscription.objects.filter(trader=trader).filter(family).exists():
        return MarketNotifyLevel.Level.ALL
    if Trade.objects.filter(trader=trader).filter(family).exists():
        return MarketNotifyLevel.Level.RESULT
    return MarketNotifyLevel.Level.NONE


def level(trader: Trader, question: Question) -> str:
    chosen = (MarketNotifyLevel.objects.filter(trader=trader, question=_root(question))
              .values_list("level", flat=True).first())
    return chosen or default_level(trader, question)


def set_level(trader: Trader, question: Question, value: str) -> str:
    if value not in LEVELS:
        raise ValueError(f"unknown notify level: {value!r}")
    MarketNotifyLevel.objects.update_or_create(trader=trader, question=_root(question), defaults={"level": value})
    return value
