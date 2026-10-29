"""The bot in closed chats (spec E8.1, E8.4, E8.5): joining a chat, membership updates, commands,
admin settings and the chat outbox (`ChatPost`). Stub from the e8-base branch; filled by e8-bot."""
from __future__ import annotations

CALLBACK_PREFIX = "chat:"


def handle_update(update: dict) -> bool:
    """Updates from groups and supergroups (`my_chat_member`, `chat_member`, group messages).

    Returns True when the update belonged to a group chat and was handled here.
    """
    return False


def owns(data: str) -> bool:
    return data.startswith(CALLBACK_PREFIX)


def handle_callback(callback: dict) -> None:
    pass


def tick(now) -> dict:
    """Worker step: fill the chat outbox (new markets, results, weekly top-3) and send due posts."""
    return {}
