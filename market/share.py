"""Sharing a market card into any chat (spec E6.4).

Two ways, both built on the same inline result:
- the Mini App asks `POST /api/markets/{id}/share` for a prepared message (Bot API `savePreparedInlineMessage`)
  and shows it with `Telegram.WebApp.shareMessage`;
- inline mode: `@bot <query>` in any chat lists the user's markets.
The card's button is a plain URL (web_app buttons work only in the chat with the bot): the sharer's
referral link that opens the market in the Mini App.
"""
from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from . import charts, telegram
from .access import visible_questions
from .models import Question, Trader

log = logging.getLogger(__name__)

INLINE_LIMIT = 10
INLINE_CACHE_SECONDS = 30


def market_link(trader: Trader | None, question: Question) -> str:
    base = f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}/{settings.TELEGRAM_APP_NAME}?startapp="
    if trader is not None and trader.referral_code:
        return f"{base}ref_{trader.referral_code}-m{question.pk}"
    return f"{base}m{question.pk}"


def card_text(question: Question) -> str:
    _, delta = charts.week_series(question)
    return f"{question.title}\nВероятность ДА: {round(question.prob * 100)}% · {charts.delta_text(delta)}"


def mini_chart_url(question: Question) -> str | None:
    """Public URL of a JPEG mini chart (cached per market and hour), or None if it cannot be rendered."""
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


def search(trader: Trader, query: str) -> list[Question]:
    from .bot_markets import tracked_markets
    query = query.strip()
    if not query:
        return tracked_markets(trader)[:INLINE_LIMIT]
    markets = visible_questions(trader).filter(kind=Question.Kind.LMSR, hidden_at__isnull=True)
    if query[:1] in "mм" and query[1:].isdigit():
        return list(markets.filter(pk=int(query[1:])))
    return list(markets.filter(status=Question.Status.OPEN, title__icontains=query)
                .order_by("-created_at")[:INLINE_LIMIT])


def handle_inline_query(inline_query: dict) -> None:
    user_id = (inline_query.get("from") or {}).get("id")
    trader = Trader.objects.filter(telegram_chat_id=user_id).first() if user_id else None
    results = []
    if trader is not None:
        results = [inline_result(trader, q) for q in search(trader, inline_query.get("query") or "")]
    try:
        telegram.api("answerInlineQuery", inline_query_id=inline_query.get("id"), results=results,
                     is_personal=True, cache_time=INLINE_CACHE_SECONDS)
    except telegram.TelegramError:
        log.warning("answerInlineQuery failed")
