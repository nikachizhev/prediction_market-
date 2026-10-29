"""Closed Telegram chats (spec E8): price, depth, link code and membership checks shared by API and bot.

A chat is a `Group` with kind="chat". Membership comes only from Telegram: `getChatMember` answers are
trusted for CHAT_MEMBER_CHECK_MINUTES, `chat_member` updates close access at once. A frozen chat
(bot removed or not an admin) keeps its last known members.
"""
from __future__ import annotations

import math
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import telegram
from .models import Group, GroupMembership, Trader
from .telegram import TelegramError

MEMBER_STATUSES = {"creator", "administrator", "member"}
# Telegram sends this user as the author of messages from an anonymous chat admin.
ANONYMOUS_ADMIN_ID = 1087968824


def size_cost(member_count: int) -> int:
    for limit, cost in settings.CHAT_CREATION_TIERS:
        if member_count <= limit:
            return cost
    return settings.MARKET_CREATION_COST


def creation_cost(group: Group) -> int:
    """What a market in this chat costs: the admin's own price or the price by chat size."""
    return group.creation_cost or size_cost(group.member_count)


def depth(cost: int) -> float:
    """LMSR b for a subsidy: b * ln 2 <= cost, so the market maker's loss is always covered."""
    return float(math.floor(cost / math.log(2)))


def new_code() -> str:
    while True:
        code = secrets.token_urlsafe(8).replace("-", "a").replace("_", "b")[:10]
        if not Group.objects.filter(code=code).exists():
            return code


def link(group: Group) -> str:
    """«Открыть рынки чата»: a t.me link, because Telegram has no web_app buttons in groups."""
    return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}/{settings.TELEGRAM_APP_NAME}?startapp=chat_{group.code}"


def post_enabled(group: Group, kind: str) -> bool:
    return bool(group.post_settings.get(kind, True))


def chats_of(trader: Trader):
    """Chats where the trader is a member by the last known answer (no Telegram calls)."""
    return Group.objects.filter(kind="chat", memberships__trader=trader, memberships__status="active")


def by_code(code: str) -> Group | None:
    return Group.objects.filter(kind="chat", code=code).first() if code else None


def is_member_status(info: dict) -> bool:
    status = info.get("status")
    return status in MEMBER_STATUSES or (status == "restricted" and bool(info.get("is_member")))


def set_membership(group: Group, trader: Trader, active: bool, now=None) -> None:
    """Record what Telegram said; used by the API check and by `chat_member` updates in the bot."""
    now = now or timezone.now()
    status = "active" if active else "left"
    updated = GroupMembership.objects.filter(group=group, trader=trader).update(status=status, checked_at=now)
    if not updated and active:
        GroupMembership.objects.get_or_create(group=group, trader=trader,
                                              defaults={"status": status, "checked_at": now})


def check_member(trader: Trader, group: Group, now=None) -> bool:
    """Is the trader in the chat now? Asks Telegram at most once per CHAT_MEMBER_CHECK_MINUTES."""
    now = now or timezone.now()
    membership = GroupMembership.objects.filter(group=group, trader=trader).first()
    known = bool(membership and membership.status == "active")
    if not group.is_chat:
        return known
    fresh = membership and membership.checked_at and \
        membership.checked_at > now - timedelta(minutes=settings.CHAT_MEMBER_CHECK_MINUTES)
    if fresh or not group.bot_active or not trader.telegram_chat_id:
        return known
    try:
        info = telegram.api("getChatMember", http_timeout=10, chat_id=group.telegram_chat_id,
                            user_id=trader.telegram_chat_id)
    except TelegramError:
        # Telegram is unreachable: keep the last known answer rather than lock everyone out.
        return known
    active = is_member_status(info)
    set_membership(group, trader, active, now)
    return active


def refresh_member_count(group: Group) -> int:
    """Chat size at market creation (spec E5: price by size is fixed in the market)."""
    if group.bot_active and group.telegram_chat_id:
        try:
            count = int(telegram.api("getChatMemberCount", http_timeout=10, chat_id=group.telegram_chat_id))
        except (TelegramError, TypeError, ValueError):
            return group.member_count
        if count != group.member_count:
            group.member_count = count
            group.save(update_fields=["member_count"])
    return group.member_count


def admins(group: Group) -> list[dict]:
    """`getChatAdministrators` as is: ChatMember objects with `user` and `status`. Raises TelegramError."""
    return telegram.api("getChatAdministrators", http_timeout=10, chat_id=group.telegram_chat_id)


def is_admin(group: Group, user_id: int) -> bool:
    """Checked against Telegram on every settings change and admin decision (spec E8.5, E8.6)."""
    try:
        return any(int(m["user"]["id"]) == int(user_id) for m in admins(group))
    except (TelegramError, KeyError, TypeError, ValueError):
        return False
