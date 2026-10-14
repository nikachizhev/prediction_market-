from __future__ import annotations

from .identity import get_trader


def current_trader(request):
    trader = get_trader(request)
    from .mascots import CHARACTERS
    character = next((c for c in CHARACTERS if trader and c["key"] == trader.mascot), None)
    from .notifications import visible_notifications
    notifications = visible_notifications(trader) if trader else None
    return {"current_trader": trader, "floating_character": character,
            "notification_unread": notifications.filter(read_at__isnull=True).count() if trader else 0,
            "recent_notifications": notifications[:8] if trader else [],
            "daily_reward": getattr(request, "daily_reward", None)}
