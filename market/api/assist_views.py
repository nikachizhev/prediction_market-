"""AI rules helper endpoint (spec E9.1, E9.5; docs/api.md «Фаза 3a», «Фаза 3d»)."""
from __future__ import annotations

import logging

from django.conf import settings

from .. import assist as helper
from ..models import CATEGORIES, Event, Trader
from . import serializers as s
from .views import BadRequest, LimitReached, Unavailable, body, chat_for, endpoint

log = logging.getLogger(__name__)

OFFLINE = "Помощник сейчас не отвечает — заполни правила сам."
MODES = ("oneline",)   # the «О чём спорим?» screen (spec E9.5); written to the event's props


def limit_text() -> str:
    return f"Подсказки на сегодня закончились — их {settings.ASSIST_DAILY_LIMIT} в день."


def reserve(trader: Trader, category: str | None, mode: str | None = None) -> Event:
    """One of today's requests (`assist.reserve`); LimitReached (429) when none is left."""
    props = {"category": category} if category else {}
    if mode:
        props["mode"] = mode
    event, ok = helper.reserve(trader, props)
    if not ok:
        raise LimitReached(limit_text())
    return event


finish = helper.finish


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
    mode = data.get("mode") if data.get("mode") in MODES else None
    trader = request.trader
    if data.get("chat") not in (None, ""):
        # A chat admin may switch the helper off for markets of their chat (spec E8.5).
        if not chat_for(trader, data["chat"]).assist_enabled:
            raise Unavailable(OFFLINE)
    if not helper.enabled():
        raise Unavailable(OFFLINE)

    event = reserve(trader, category, mode)
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
    return {"question": answer["question"], "why": answer["why"], "options": answer["options"],
            "criteria": answer["criteria"], "category": answer["category"],
            "closes_at": s.iso(answer["closes_at"]), "resolves_at": s.iso(answer["resolves_at"]),
            "left": helper.left(trader)}
