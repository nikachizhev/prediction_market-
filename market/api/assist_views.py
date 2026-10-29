"""AI rules helper endpoint (spec E9.1, docs/api.md «Фаза 3a»)."""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction

from .. import assist as helper
from ..events import log_event
from ..models import CATEGORIES, Event, Trader
from . import serializers as s
from .views import BadRequest, LimitReached, Unavailable, body, endpoint

log = logging.getLogger(__name__)

OFFLINE = "Помощник сейчас не отвечает — заполни правила сам."


def reserve(trader: Trader, category: str | None) -> Event:
    """Take one of today's requests before calling the model, so parallel requests cannot exceed the limit.

    The trader lock is held only for the count, never during the HTTP call; a `pending` event already counts.
    """
    props = {"category": category} if category else {}
    with transaction.atomic():
        Trader.objects.select_for_update().get(pk=trader.pk)
        over = helper.left(trader) <= 0
        event = log_event(trader, helper.EVENT, {"result": "limit" if over else "pending", **props})
    if over:
        raise LimitReached(f"Подсказки на сегодня закончились — их {settings.ASSIST_DAILY_LIMIT} в день.")
    return event


def finish(event: Event, **props) -> None:
    event.props = {**event.props, **props}
    event.save(update_fields=["props"])


@endpoint("POST")
def assist(request):
    data = body(request)
    title = data.get("title")
    title = title.strip() if isinstance(title, str) else ""
    if not helper.TITLE_MIN <= len(title) <= helper.TITLE_MAX:
        raise BadRequest(f"Вопрос — от {helper.TITLE_MIN} до {helper.TITLE_MAX} символов.")
    category = data.get("category")
    if not isinstance(category, str) or category not in dict(CATEGORIES):
        category = None
    if not helper.enabled():
        raise Unavailable(OFFLINE)

    trader = request.trader
    event = reserve(trader, category)
    try:
        answer = helper.suggest(title, category)
    except helper.Failed as exc:
        log.warning("AI helper refused: %s", exc.reason)
        finish(event, result="error", reason=exc.reason)
        raise Unavailable(OFFLINE) from None
    except Exception:
        finish(event, result="error", reason="internal")
        raise

    if "forbidden" in answer:
        finish(event, result="warning", topic=answer["forbidden"])
        log.info("AI helper: forbidden topic %s", answer["forbidden"])
        return {"warning": "forbidden", "text": helper.WARNINGS[answer["forbidden"]], "left": helper.left(trader)}

    finish(event, result="ok", category=answer["category"], refined=answer["question"] is not None)
    log.info("AI helper: ok")
    return {"question": answer["question"], "why": answer["why"], "criteria": answer["criteria"],
            "category": answer["category"], "closes_at": s.iso(answer["closes_at"]),
            "resolves_at": s.iso(answer["resolves_at"]), "left": helper.left(trader)}
