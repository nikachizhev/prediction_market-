"""Analytics events for funnels and retention (spec E7). Never stores names or Telegram IDs in props."""
from __future__ import annotations

import json

from .models import Event, Trader

# Events the Mini App may report itself; server-side ones (app_open, trade, market_create) are logged by views.
CLIENT_EVENTS = {"share", "onboarding_done", "onboarding_skip", "open_bot", "feed_impression", "market_open"}
MAX_PROPS_BYTES = 2000


def log_event(trader: Trader | None, name: str, props: dict | None = None) -> Event:
    props = props or {}
    if len(json.dumps(props, ensure_ascii=False, default=str)) > MAX_PROPS_BYTES:
        props = {"truncated": True}
    return Event.objects.create(trader=trader, name=name[:40], props=props)
