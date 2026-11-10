"""«Колесо дня» (spec E12.2; docs/api.md «Фаза 3f»). The server picks the prize; the app only shows it.

Not routed since 10.11.2026 (market/api/urls.py): the wheel is out of the product for now.
"""
from __future__ import annotations

from django.conf import settings
from django.http import JsonResponse

from .. import services
from ..events import log_event
from . import serializers as s
from .views import endpoint


def _state(trader) -> dict:
    state = services.wheel_state(trader)
    prizes = services.wheel_prizes()
    total = sum(weight for _, weight in prizes)
    return {
        "unlocked": state["unlocked"],
        "spun": s.money(state["spun"]) if state["spun"] is not None else None,
        "prizes": [{"prize": s.money(prize), "chance": weight / total} for prize, weight in prizes],
    }


def _off() -> JsonResponse:
    return JsonResponse({"error": "Колесо дня сейчас выключено."}, status=404)


@endpoint("GET")
def wheel(request):
    if not settings.WHEEL_ENABLED:
        return _off()
    return _state(request.trader)


@endpoint("POST")
def spin(request):
    if not settings.WHEEL_ENABLED:
        return _off()
    spun = services.spin_wheel(request.trader)
    log_event(request.trader, "wheel_spin", {"prize": str(spun.prize)})
    request.trader.refresh_from_db(fields=["balance"])
    return {"prize": s.money(spun.prize), "balance": s.money(request.trader.balance), "wheel": _state(request.trader)}
