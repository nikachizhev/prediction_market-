"""«Мои рынки» in the bot (spec E6.1) and the 7-day animation of one market (E6.2).

Texts follow docs/mockups/Bot.dc.html. Callback data: `my:new` opens the list in a new message,
`my:<page>` turns pages in place, `mk:<id>` sends the chart of a market.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal

from django.conf import settings
from django.utils import timezone

from . import charts, telegram
from .access import visible_questions
from .bot_resolution import app_button, app_url
from .models import MarketSubscription, Position, Question, Trader

log = logging.getLogger(__name__)

OPEN_LIST = "my:new"
TITLE_LIMIT = 30
HEADER = "Мои рынки\nНажми на рынок, чтобы увидеть график за неделю."
EMPTY = ("Пока ты ни за чем не следишь. Сделай прогноз или нажми звёздочку на рынке в приложении — "
         "он появится здесь.")


def owns(data: str) -> bool:
    return data.startswith(("my:", "mk:"))


def _quiet(method: str, **data) -> dict | None:
    try:
        return telegram.api(method, **data)
    except telegram.TelegramError:
        log.warning("Telegram %s failed", method)
        return None


def tracked_markets(trader: Trader) -> list[Question]:
    """Markets the trader follows (a trade or the star subscribes), newest subscription first."""
    order = list(MarketSubscription.objects.filter(trader=trader)
                 .order_by("-created_at", "-id").values_list("question_id", flat=True))
    markets = visible_questions(trader).filter(pk__in=order, kind=Question.Kind.LMSR).exclude(
        status__in=[Question.Status.RESOLVED, Question.Status.ANNULLED])
    by_id = {q.pk: q for q in markets}
    return [by_id[pk] for pk in order if pk in by_id]


def find_market(trader: Trader, pk) -> Question | None:
    """Someone else's private market is the same as a missing one."""
    try:
        return visible_questions(trader).filter(kind=Question.Kind.LMSR).get(pk=int(pk))
    except (Question.DoesNotExist, ValueError, TypeError):
        return None


def change_24h(question: Question, now=None) -> float:
    from .api.serializers import History
    now = now or timezone.now()
    day_ago = now - timedelta(days=1)
    return (question.prob - History(question, since=day_ago).at(day_ago)) * 100


def short_title(title: str, limit: int = TITLE_LIMIT) -> str:
    title = " ".join(title.split())
    return title if len(title) <= limit else title[:limit - 1].rstrip() + "…"


def button_label(question: Question, now=None) -> str:
    label = f"{short_title(question.title)} · {round(question.prob * 100)}%"
    points = round(change_24h(question, now))
    if points:
        label += f" {'▲' if points > 0 else '▼'} {abs(points)}"
    return label


def list_view(trader: Trader, page: int = 0) -> tuple[str, dict]:
    markets = tracked_markets(trader)
    if not markets:
        return EMPTY, {"inline_keyboard": [[app_button()]]}
    size = settings.BOT_MARKETS_PAGE
    pages = (len(markets) + size - 1) // size
    page = min(max(page, 0), pages - 1)
    now = timezone.now()
    rows = [[{"text": button_label(q, now), "callback_data": f"mk:{q.pk}"}]
            for q in markets[page * size:(page + 1) * size]]
    nav = []
    if page > 0:
        nav.append({"text": "←", "callback_data": f"my:{page - 1}"})
    if page < pages - 1:
        nav.append({"text": "→", "callback_data": f"my:{page + 1}"})
    if nav:
        rows.append(nav)
    return HEADER, {"inline_keyboard": rows}


def _trader(chat_id) -> Trader | None:
    return Trader.objects.filter(telegram_chat_id=chat_id).first()


def show(chat_id: int) -> None:
    """/my: the list as a new message; strangers get the welcome."""
    trader = _trader(chat_id)
    if trader is None:
        from .bot_resolution import welcome
        welcome(chat_id)
        return
    text, markup = list_view(trader)
    telegram.send(chat_id, text, reply_markup=markup)


def position_line(trader: Trader, question: Question) -> str:
    pos = Position.objects.filter(trader=trader, question=question).first()
    yes = pos.yes_shares if pos else Decimal(0)
    no = pos.no_shares if pos else Decimal(0)
    if yes <= 0 and no <= 0:
        return "Позиции нет — только слежу"
    side, shares = ("ДА", yes) if yes >= no else ("НЕТ", no)
    # One share pays one coin if the side wins.
    return f"Твоя позиция: {side} · выплата {shares.quantize(Decimal(1), rounding=ROUND_FLOOR)}"


def caption(trader: Trader, question: Question, now=None) -> str:
    delta = change_24h(question, now)
    return (f"{question.title}\n{round(question.prob * 100)}% · {charts.delta_text(delta)}\n"
            f"{position_line(trader, question)}")


def market_keyboard(question: Question) -> dict:
    return {"inline_keyboard": [[{"text": "Открыть в приложении", "web_app": {"url": app_url(f"market/{question.pk}")}}]]}


def send_chart(chat_id: int, trader: Trader, question: Question) -> None:
    """The animation with caption; reuses the Telegram file_id within the hour; text if rendering fails."""
    text = caption(trader, question)
    markup = market_keyboard(question)
    try:
        animation = charts.animation_for(question)
    except (charts.ChartError, OSError) as exc:
        log.warning("Chart for market %s not rendered: %s", question.pk, exc)
        telegram.send(chat_id, text, reply_markup=markup)
        return
    try:
        if animation.file_id:
            telegram.api("sendAnimation", chat_id=chat_id, animation=animation.file_id,
                         caption=text, reply_markup=markup)
            return
        with animation.file.open("rb") as fh:
            data = fh.read()
        message = telegram.upload(
            "sendAnimation", {"animation": (f"market-{question.pk}.mp4", data, "video/mp4")},
            chat_id=chat_id, caption=text, reply_markup=markup,
            width=charts.WIDTH, height=charts.HEIGHT, duration=3,
        )
    except (telegram.TelegramError, OSError) as exc:
        log.warning("Animation for market %s not sent: %s", question.pk, exc)
        telegram.send(chat_id, text, reply_markup=markup)
        return
    file_id = ((message or {}).get("animation") or (message or {}).get("document") or {}).get("file_id")
    if file_id:
        animation.file_id = file_id
        animation.save(update_fields=["file_id"])


def handle_callback(callback: dict) -> None:
    data = callback.get("data") or ""
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    user_id = (callback.get("from") or {}).get("id")
    answer = {"callback_query_id": callback.get("id")}
    trader = _trader(user_id) if user_id is not None else None
    if chat.get("type") != "private" or chat.get("id") != user_id or trader is None:
        _quiet("answerCallbackQuery", **answer)
        if trader is None and chat.get("type") == "private" and chat.get("id") == user_id:
            from .bot_resolution import welcome
            welcome(user_id)
        return
    action, _, arg = data.partition(":")
    if action == "my":
        _quiet("answerCallbackQuery", **answer)
        if arg == "new":
            show(user_id)
            return
        try:
            page = int(arg)
        except ValueError:
            page = 0
        text, markup = list_view(trader, page)
        # Paging edits the same message; «message is not modified» and the like are harmless.
        _quiet("editMessageText", chat_id=user_id, message_id=message.get("message_id"),
               text=text, reply_markup=markup)
        return
    question = find_market(trader, arg)
    if question is None:
        _quiet("answerCallbackQuery", **answer, text="Рынок не найден.")
        return
    _quiet("answerCallbackQuery", **answer)
    try:
        send_chart(user_id, trader, question)
    except telegram.TelegramError:
        log.warning("Chart message for market %s failed", question.pk)
