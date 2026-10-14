from __future__ import annotations

from .identity import get_trader


def current_trader(request):
    trader = get_trader(request)
    from .mascots import CHARACTERS
    character = next((c for c in CHARACTERS if trader and c["key"] == trader.mascot), None)
    return {"current_trader": trader, "floating_character": character,
            "daily_reward": getattr(request, "daily_reward", None)}
