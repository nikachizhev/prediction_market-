"""Sharing a market or challenge card into any chat (spec E6.4, E11).

Two ways, both built on the same inline result:
- the Mini App asks `POST /api/markets/{id}/share` for a prepared message (Bot API `savePreparedInlineMessage`)
  and shows it with `Telegram.WebApp.shareMessage`;
- inline mode: `@bot <query>` in any chat lists the user's markets.
The card's button is a plain URL (web_app buttons work only in the chat with the bot): the sharer's
referral link that opens the market in the Mini App.
"""
from __future__ import annotations

import logging
import hashlib
import re
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from . import charts, telegram
from .access import visible_questions
from .models import Challenge, Group, Question, Trader

log = logging.getLogger(__name__)

INLINE_LIMIT = 10
INLINE_CACHE_SECONDS = 30
CHALLENGE_QUERY = re.compile(r"^ch_([A-Za-z0-9_-]{1,24})$")
CHALLENGE_TEXT_MAX = 120   # the question's limit: `services.CHALLENGE_TITLE_MAX`
START_PREFIX = "newch"     # /start newch_<key> from the button above inline results
INLINE_TEXT_SECONDS = 600  # what was written after @bot is kept this long, for the form


def market_link(trader: Trader | None, question: Question) -> str:
    target_id = question.parent_id or question.pk
    base = f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}/{settings.TELEGRAM_APP_NAME}?startapp="
    if trader is not None and trader.referral_code:
        return f"{base}ref_{trader.referral_code}-m{target_id}"
    return f"{base}m{target_id}"


def card_text(question: Question) -> str:
    subs = list(question.submarkets.all().order_by("id")) if question.pk else []
    if subs:
        opts = " · ".join(f"{s.option_label or s.title} {round(s.prob * 100)}%" for s in subs[:4])
        if len(subs) > 4:
            opts += f" (+{len(subs) - 4})"
        return f"{question.title}\n{opts}"
    _, delta = charts.week_series(question)
    return f"{question.title}\nВероятность ДА: {round(question.prob * 100)}% · {charts.delta_text(delta)}"


def mini_chart_url(question: Question) -> str | None:
    """Public URL of a JPEG mini chart (cached per market and hour), or None if it cannot be rendered."""
    if question.publication_state != "approved":
        return None
    hour = charts.hour_of(timezone.now())
    folder = Path(settings.MEDIA_ROOT) / "share"
    name = f"market-{question.pk}-{hour:%Y%m%d%H}.jpg"
    path = folder / name
    if not path.exists():
        try:
            probs, delta = charts.week_series(question)
            data = charts.render_still(probs, delta)
            folder.mkdir(parents=True, exist_ok=True)
            for old in folder.glob(f"market-{question.pk}-*.jpg"):
                old.unlink(missing_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        except Exception:  # matplotlib or the disk; the text card still works
            log.exception("Mini chart for market %s not rendered", question.pk)
            return None
    return f"{settings.SITE_URL}{settings.MEDIA_URL}share/{name}"


def inline_result(trader: Trader | None, question: Question) -> dict:
    text = card_text(question)
    markup = {"inline_keyboard": [[{"text": "Открыть в приложении", "url": market_link(trader, question)}]]}
    result_id = f"m{question.pk}"
    # Telegram fetches photo_url itself, so the photo card needs a public https site.
    photo = mini_chart_url(question) if settings.SITE_URL.startswith("https://") else None
    if photo:
        return {"type": "photo", "id": result_id, "photo_url": photo, "thumbnail_url": photo,
                "photo_width": charts.WIDTH, "photo_height": charts.HEIGHT,
                "title": question.title[:100], "description": text.split("\n", 1)[1],
                "caption": text, "reply_markup": markup}
    return {"type": "article", "id": result_id, "title": question.title[:100],
            "description": text.split("\n", 1)[1],
            "input_message_content": {"message_text": text}, "reply_markup": markup}


def prepare(trader: Trader, question: Question) -> str:
    """Prepared inline message for WebApp.shareMessage; raises TelegramError."""
    if not trader.telegram_chat_id:
        raise telegram.TelegramError("Нет Telegram-аккаунта.")
    prepared = telegram.api(
        "savePreparedInlineMessage", user_id=trader.telegram_chat_id, result=inline_result(trader, question),
        allow_user_chats=True, allow_bot_chats=False, allow_group_chats=True, allow_channel_chats=True,
    )
    return str(prepared["id"])


def challenge_link(challenge: Challenge) -> str:
    return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}/{settings.TELEGRAM_APP_NAME}?startapp=ch_{challenge.code}"


def challenge_result(challenge: Challenge) -> dict:
    """The friend's card. The button is a plain URL: web_app buttons work only in the chat with the bot."""
    from .services import challenge_prize
    from .bot_notifications import coins
    terms = f"Ставка каждого {coins(challenge.stake)} · победитель получит {coins(challenge_prize(challenge.stake))}"
    text = f"Вызывает: {challenge.author.display_name}\n{challenge.title}\n{terms}"
    return {"type": "article", "id": f"ch{challenge.pk}", "title": f"Вызов: {challenge.title}"[:100], "description": terms,
            "input_message_content": {"message_text": text},
            "reply_markup": {"inline_keyboard": [[{"text": "Принять вызов", "url": challenge_link(challenge)}]]}}


def prepare_challenge(trader: Trader, challenge: Challenge) -> str:
    """Prepared inline message for WebApp.shareMessage: a friend or a group; raises TelegramError."""
    if not trader.telegram_chat_id:
        raise telegram.TelegramError("Нет Telegram-аккаунта.")
    prepared = telegram.api(
        "savePreparedInlineMessage", user_id=trader.telegram_chat_id, result=challenge_result(challenge),
        allow_user_chats=True, allow_bot_chats=False, allow_group_chats=True, allow_channel_chats=False,
    )
    return str(prepared["id"])


def own_open_challenges(trader: Trader, query: str) -> list[Challenge]:
    """`ch_<code>` or an empty query: only the author's cards, and only while they can be accepted."""
    found = (Challenge.objects.filter(author=trader, status=Challenge.Status.OPEN, expires_at__gt=timezone.now())
             .exclude(check_state="rejected").select_related("author").order_by("-created_at", "-id"))
    match = CHALLENGE_QUERY.match(query)
    if match:
        return list(found.filter(code=match.group(1))[:1])
    return list(found[:INLINE_LIMIT]) if not query else []


def _text_key(user_id: int, key: str) -> str:
    return f"inline-challenge:{user_id}:{key}"


def create_challenge_button(user_id: int | None, query: str) -> dict:
    """«Создать вызов» above the results.

    It leads to the chat with the bot, not straight to the Mini App: Telegram gives a Mini App opened from inline
    mode no signed initData, so nobody could log in. The start parameter holds 64 characters at most, so the text
    waits here; both this query and that /start reach the same bot process. Telegram may answer a repeated query
    from its own cache, so the key comes from the text: an old button still finds its own text.
    """
    text = "" if CHALLENGE_QUERY.match(query) else query[:CHALLENGE_TEXT_MAX]
    if not text or not user_id:
        return {"text": "Создать вызов", "start_parameter": START_PREFIX}
    key = hashlib.sha256(text.encode()).hexdigest()[:16]
    cache.set(_text_key(user_id, key), text, INLINE_TEXT_SECONDS)
    return {"text": "Создать вызов", "start_parameter": f"{START_PREFIX}_{key}"}


def owns_start(param: str) -> bool:
    return param == START_PREFIX or param.startswith(f"{START_PREFIX}_")


def start_create_challenge(chat_id: int, param: str) -> None:
    """/start newch_<key>: the button opens the form with a signed initData. Nothing is charged until the form."""
    from .bot_resolution import app_button
    _, _, key = param.partition("_")
    text = cache.get(_text_key(chat_id, key)) if key else None
    path = "challenge/new" + (f"?{urlencode({'text': text})}" if text else "")
    # Text outside the spec's wording.
    lead = f"Вызов другу:\n«{text}»\n" if text else "Вызов другу.\n"
    telegram.send(chat_id, f"{lead}Проверь условия, выбери ставку и отправь другу.",
                  reply_markup={"inline_keyboard": [[app_button("Создать вызов", path)]]})


def search(trader: Trader, query: str) -> list[Question]:
    """Closed chats' markets stay inside their chat (spec E8.3): inline mode never offers them."""
    from .bot_markets import tracked_markets
    query = query.strip()
    if not query:
        tracked = tracked_markets(trader)
        chat_ids = set(Group.objects.filter(pk__in={q.group_id for q in tracked if q.group_id}, kind="chat")
                       .values_list("pk", flat=True))
        return [q for q in tracked if q.group_id not in chat_ids][:INLINE_LIMIT]
    markets = (visible_questions(trader).filter(kind=Question.Kind.LMSR, parent__isnull=True, hidden_at__isnull=True)
               .exclude(group__kind="chat"))
    if query[:1] in "mм" and query[1:].isdigit():
        return list(markets.filter(pk=int(query[1:])))
    return list(markets.filter(status=Question.Status.OPEN, title__icontains=query)
                .order_by("-created_at")[:INLINE_LIMIT])


def handle_inline_query(inline_query: dict) -> None:
    user_id = (inline_query.get("from") or {}).get("id")
    trader = Trader.objects.filter(telegram_chat_id=user_id).first() if user_id else None
    query = (inline_query.get("query") or "").strip()
    results = []
    if trader is not None:
        results = [challenge_result(c) for c in own_open_challenges(trader, query)]
        if not CHALLENGE_QUERY.match(query):
            results += [inline_result(trader, q) for q in search(trader, query)]
    try:
        telegram.api("answerInlineQuery", inline_query_id=inline_query.get("id"), results=results,
                     is_personal=True, cache_time=INLINE_CACHE_SECONDS, button=create_challenge_button(user_id, query))
    except telegram.TelegramError:
        log.warning("answerInlineQuery failed")
