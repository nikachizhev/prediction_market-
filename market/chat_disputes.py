"""Disputes on chat markets decided by the chat's admins (spec E8.6). Stub from the e8-base branch;
filled by e8-disputes."""
from __future__ import annotations

CALLBACK_PREFIX = "cdis:"


def owns(data: str) -> bool:
    return data.startswith(CALLBACK_PREFIX)


def handle_callback(callback: dict) -> None:
    pass


def tick(now) -> dict:
    """Worker step: send new chat disputes to admins, hand silent ones to the project team after 48 h."""
    return {}
