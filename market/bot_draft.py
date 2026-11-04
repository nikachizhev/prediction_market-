"""`/market` in a closed chat (spec E9.4): a market from the replied message in two taps.

Stub of phase 3d base (T25); T30 fills it in. Callback data: `draft:…`.
"""
from __future__ import annotations

CALLBACK_PREFIX = "draft:"


def owns(data: str) -> bool:
    return data.startswith(CALLBACK_PREFIX)


def handle_command(message: dict, group) -> None:
    """`/market` sent in a known chat (`group` may be frozen). Called by `bot_chats._command`."""


def handle_callback(callback: dict) -> None:
    """`Создать за <цена>` under the preview in the chat."""
