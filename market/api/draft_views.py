"""Drafts of markets from a chat message (spec E9.4; docs/api.md «Фаза 3d»): `GET /api/drafts/{code}`.

A draft opens only for the trader who called /market, while it lives (CHAT_DRAFT_HOURS) and while they are in its
chat; anything else looks like a missing draft.
"""
from __future__ import annotations

from django.http import JsonResponse
from django.utils import timezone

from .. import chats
from ..models import MarketDraft, Trader
from . import serializers as s
from .views import endpoint

NOT_FOUND = "Черновик не найден."


def find_draft(trader: Trader, code) -> MarketDraft | None:
    """The trader's own live draft by its code; the chat membership is the caller's check."""
    if not isinstance(code, str) or not code:
        return None
    return (MarketDraft.objects.select_related("group")
            .filter(code=code, trader=trader, expires_at__gt=timezone.now()).first())


def not_found() -> JsonResponse:
    return JsonResponse({"error": NOT_FOUND}, status=404)


def draft_view(draft: MarketDraft) -> dict:
    """`MarketDraftView`."""
    return {
        "code": draft.code,
        "chat": {"code": draft.group.code, "title": draft.group.name},
        "title": draft.title,
        "criteria": draft.criteria,
        "category": draft.category or None,
        "closes_at": s.iso(draft.closes_at),
        "resolves_at": s.iso(draft.resolves_at),
        "options": list(draft.options or []),
        "assisted": draft.assisted,
        "market_id": draft.question_id,
    }


@endpoint("GET")
def draft(request, code):
    found = find_draft(request.trader, code)
    if found is None or not found.group.is_chat or not chats.check_member(request.trader, found.group):
        return not_found()
    return draft_view(found)
