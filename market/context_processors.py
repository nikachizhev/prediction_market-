from __future__ import annotations

from .models import Trader


def current_trader(request):
    trader = None
    tid = request.session.get("trader_id") if hasattr(request, "session") else None
    if tid:
        trader = Trader.objects.filter(pk=tid).first()
    return {"current_trader": trader}
