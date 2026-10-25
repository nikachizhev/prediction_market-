"""Mini App authentication: the signed Telegram `initData` is the only proof of identity."""
from __future__ import annotations

import hashlib
import hmac
import json
import random
import secrets
import time
from dataclasses import dataclass
from functools import wraps
from urllib.parse import parse_qsl

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import JsonResponse

from .models import Trader

PSEUDONYMS = [
    "Капибара", "Енот", "Сова", "Лис", "Барсук", "Выдра", "Ёж", "Пингвин", "Панда", "Кит",
    "Филин", "Бобр", "Хомяк", "Тюлень", "Лемур", "Коала", "Сурикат", "Альпака", "Ласка", "Белка",
]


class InvalidInitData(Exception):
    pass


@dataclass
class TelegramSession:
    user: dict
    start_param: str
    auth_date: int


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def sign_init_data(fields: dict[str, str], bot_token: str) -> str:
    """Build a signed initData string (dev tooling and tests)."""
    from urllib.parse import urlencode
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    digest = hmac.new(_secret_key(bot_token), check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


def verify_init_data(init_data: str, bot_token: str | None = None, *, max_age: int | None = None,
                     now: float | None = None) -> TelegramSession:
    """Check the HMAC per https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app."""
    bot_token = settings.TELEGRAM_BOT_TOKEN if bot_token is None else bot_token
    max_age = settings.TELEGRAM_INIT_DATA_MAX_AGE if max_age is None else max_age
    if not bot_token:
        raise InvalidInitData("Бот не настроен.")
    if not init_data:
        raise InvalidInitData("Откройте приложение из Telegram.")
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise InvalidInitData("Неверная подпись Telegram.")
    received = fields.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    expected = hmac.new(_secret_key(bot_token), check.encode(), hashlib.sha256).hexdigest()
    if not received or not hmac.compare_digest(expected, received):
        raise InvalidInitData("Неверная подпись Telegram.")
    try:
        auth_date = int(fields.get("auth_date", ""))
        user = json.loads(fields.get("user", ""))
        user_id = int(user["id"])
    except (ValueError, KeyError, TypeError):
        raise InvalidInitData("Неверные данные Telegram.") from None
    if (now if now is not None else time.time()) - auth_date > max_age:
        raise InvalidInitData("Сессия устарела. Откройте приложение заново.")
    if user_id <= 0 or user.get("is_bot"):
        raise InvalidInitData("Неверные данные Telegram.")
    return TelegramSession(user=user, start_param=fields.get("start_param", ""), auth_date=auth_date)


def _full_name(user: dict) -> str:
    name = " ".join(part for part in (user.get("first_name", ""), user.get("last_name", "")) if part).strip()
    return (name or "Участник")[:100]


def _new_referral_code() -> str:
    while True:
        code = secrets.token_urlsafe(6).replace("-", "a").replace("_", "b")[:8]
        if not Trader.objects.filter(referral_code=code).exists():
            return code


def _referrer(start_param: str) -> Trader | None:
    if not start_param.startswith("ref_"):
        return None
    return Trader.objects.filter(referral_code=start_param[4:]).first()


def get_or_create_trader(session: TelegramSession) -> tuple[Trader, bool]:
    """One account per Telegram ID; the starter pack and the inviter are set only on creation."""
    from .services import grant_starter_pack
    user = session.user
    telegram_id = int(user["id"])
    trader = Trader.objects.filter(telegram_chat_id=telegram_id).first()
    if trader is None:
        try:
            with transaction.atomic():
                trader = Trader.objects.create(
                    telegram_chat_id=telegram_id, name=_full_name(user),
                    pseudonym=f"{random.choice(PSEUDONYMS)} {telegram_id % 1000:03d}",
                    telegram_username=(user.get("username") or "")[:64],
                    photo_url=(user.get("photo_url") or "")[:500],
                    referral_code=_new_referral_code(), referred_by=_referrer(session.start_param),
                    balance=0,
                )
                grant_starter_pack(trader)
        except IntegrityError:
            # A parallel first open created the account; use it.
            trader = Trader.objects.get(telegram_chat_id=telegram_id)
        else:
            trader.refresh_from_db()
            return trader, True
    changed = []
    for field, value in (("name", _full_name(user)), ("telegram_username", (user.get("username") or "")[:64]),
                         ("photo_url", (user.get("photo_url") or "")[:500])):
        if getattr(trader, field) != value:
            setattr(trader, field, value)
            changed.append(field)
    if not trader.referral_code:
        trader.referral_code = _new_referral_code()
        changed.append("referral_code")
    if changed:
        trader.save(update_fields=changed)
    return trader, False


def authenticate(request) -> tuple[Trader, TelegramSession, bool]:
    header = request.headers.get("Authorization", "")
    scheme, _, init_data = header.partition(" ")
    if scheme.lower() != "tma":
        raise InvalidInitData("Откройте приложение из Telegram.")
    session = verify_init_data(init_data.strip())
    trader, created = get_or_create_trader(session)
    return trader, session, created


def api_view(view):
    """JSON endpoint authenticated by initData; sets request.trader and request.telegram."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            trader, session, created = authenticate(request)
        except InvalidInitData as exc:
            return JsonResponse({"error": str(exc)}, status=401)
        request.trader = trader
        request.telegram = session
        request.trader_created = created
        return view(request, *args, **kwargs)
    return wrapper
