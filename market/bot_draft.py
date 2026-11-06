"""`/market` in a closed chat (spec E9.4): a market from the replied message in two taps.

The caller answers a message («спорим, что…») with /market. Its text goes to the AI helper (the same daily limit
and `assist_request` event as in the wizard) and the bot answers in the chat with a preview of a `MarketDraft`:
`Создать за <цена>` (callback `draft:create:<code>`) and `Изменить` (the Mini App with the draft). Without the
helper the draft holds only the text, and the one button leads to the Mini App.

`Создать` works only for the caller and goes through `services.create_market` with a request key derived from
the draft's code, so a second tap never charges twice. The new market passes premoderation like any other;
the announcement in the chat (a reply to the source message) is `bot_chats.fill`'s job.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import assist, chats, services, telegram
from .bot_chats import FROZEN, OPEN_CHAT_BUTTON
from .bot_notifications import MSK, coins, plural
from .bot_resolution import MONTHS
from .models import Group, MarketDraft, Question, Trader
from .services import MarketError
from .share import market_link
from .telegram import TelegramError

log = logging.getLogger(__name__)

CALLBACK_PREFIX = "draft:"
# Request keys of markets made from drafts: uuid5 of the draft's code in this namespace.
KEY_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "prediction-market/market-draft")

# Texts outside the spec (phase 3d has no mockups) — shown to the user before merging.
USAGE = ("Ответь командой /market на сообщение, из которого сделать рынок, — например, на «спорим, что…». "
         "Я подготовлю рынок, а создать его можно в два нажатия.")
NO_TEXT = "В этом сообщении нет текста. Ответь /market на сообщение с текстом."
ANONYMOUS = ("Ты пишешь анонимно, от имени чата, — так я не пойму, чей это рынок. "
             "Напиши /market от своего имени.")
NO_ACCOUNT = "Чтобы создавать рынки, сначала открой приложение — кнопка ниже. Потом повтори /market."
NOT_MEMBER = "Рынок чата создаёт только участник чата."
WARNING = ("Как это работает: текст сообщения, на которое ответили /market, уйдёт ИИ-помощнику — он предложит "
           "вопрос, условия и сроки. Кто написал сообщение, помощник не узнает. Админ может выключить "
           "помощника для чата в /settings.\n"
           "Ответь /market на сообщение ещё раз — и я подготовлю рынок.")
PREVIEW_TITLE = "Рынок из сообщения:"
NO_HELPER_TAIL = "Правила, сроки и цену заполни в приложении — кнопка ниже."
LIMIT = "Подсказки ИИ на сегодня закончились — их {limit} в день."
OFFLINE = "Помощник сейчас не отвечает."
CREATE_BUTTON = "Создать за {cost}"
EDIT_BUTTON = "Изменить"
FINISH_BUTTON = "Доделать рынок"
OPEN_MARKET_BUTTON = "Открыть рынок"
CREATED = "Рынок создан:\n«{title}»"
CREATED_PENDING = "Рынок создан, проверяем…\n«{title}»\nКогда проверка пройдёт, он появится в рынках чата."
ALERT_ONLY_CALLER = "Создать этот рынок может только тот, кто вызвал /market."
ALERT_NOT_FOUND = "Черновик не найден."
ALERT_EXPIRED = "Черновик устарел: он живёт 24 часа. Ответь /market на сообщение ещё раз."
ALERT_NO_HELPER = "Этот рынок нужно доделать в приложении — кнопка «Доделать рынок»."
ALERT_CREATED = "Рынок создан"
ALERT_ALREADY = "Рынок уже создан"
ALERT_FAILED = "Не получилось — попробуй ещё раз."


def owns(data: str) -> bool:
    return data.startswith(CALLBACK_PREFIX)


def request_key(code: str) -> uuid.UUID:
    """`Trade.request_key`-style idempotency for `Создать`: the same draft always gives the same key."""
    return uuid.uuid5(KEY_NAMESPACE, code)


def draft_link(draft: MarketDraft) -> str:
    """`Изменить` / `Доделать рынок`: the Mini App opens the creation preview with the draft (docs/api.md «Фаза 3d»)."""
    return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}/{settings.TELEGRAM_APP_NAME}?startapp=draft_{draft.code}"


def new_code() -> str:
    while True:
        code = secrets.token_urlsafe(8).replace("-", "a").replace("_", "b")[:10]
        if not MarketDraft.objects.filter(code=code).exists():
            return code


# --- /market ------------------------------------------------------------------------------------

def _source(message: dict) -> dict | None:
    """The message /market answered; None without a real reply (in a forum topic every message «replies» to the
    topic's first service message)."""
    source = message.get("reply_to_message")
    if not source or source.get("forum_topic_created"):
        return None
    if message.get("is_topic_message") and source.get("message_id") == message.get("message_thread_id"):
        return None
    return source


def _trader(user: dict) -> Trader | None:
    if not user.get("id") or user.get("is_bot"):
        return None
    return Trader.objects.filter(telegram_chat_id=user["id"]).first()


def _moment(value: datetime) -> str:
    local = timezone.localtime(value, MSK)
    return f"{local.day} {MONTHS[local.month - 1]}, {local:%H:%M}"


def _cost_text(cost: int) -> str:
    return f"{cost} {plural(cost, 'монета', 'монеты', 'монет')}"


def preview_text(draft: MarketDraft, cost: int, note: str = "") -> str:
    lines = [note] if note else []
    lines += [PREVIEW_TITLE, f"«{draft.title}»"]
    if not draft.assisted:
        lines.append(NO_HELPER_TAIL)
        return "\n".join(lines)
    if draft.options:
        lines.append("Варианты: " + " · ".join(draft.options))
    lines += ["", "Как определяем результат:", draft.criteria, "",
              f"Приём прогнозов — до {_moment(draft.closes_at)}",
              f"Итог — {_moment(draft.resolves_at)}",
              f"Создание в этом чате — {_cost_text(cost)}"]
    return "\n".join(lines)


def preview_markup(draft: MarketDraft, cost: int) -> dict:
    link = draft_link(draft)
    if not draft.assisted:
        return {"inline_keyboard": [[{"text": FINISH_BUTTON, "url": link}]]}
    return {"inline_keyboard": [[
        {"text": CREATE_BUTTON.format(cost=coins(cost)), "callback_data": f"{CALLBACK_PREFIX}create:{draft.code}"},
        {"text": EDIT_BUTTON, "url": link},
    ]]}


def _ask(trader: Trader, text: str) -> tuple[dict | None, str]:
    """The helper's answer for the message text, or None and a note why the draft holds only the text."""
    event, ok = assist.reserve(trader, {"mode": "chat"})
    if not ok:
        return None, LIMIT.format(limit=settings.ASSIST_DAILY_LIMIT)
    try:
        answer = assist.suggest(text, None)
    except assist.Failed as exc:
        log.warning("AI helper refused in a chat: %s", exc.reason)
        assist.finish(event, result="error", reason=exc.reason)
        return None, OFFLINE
    except Exception:  # noqa: BLE001 - a broken helper must still leave the caller a draft
        log.exception("AI helper crashed in a chat")
        assist.finish(event, result="error", reason="internal")
        return None, OFFLINE
    if "forbidden" in answer:
        assist.finish(event, result="warning", topic=answer["forbidden"])
        return None, assist.WARNINGS[answer["forbidden"]] + "."
    assist.finish(event, result="ok", category=answer["category"], refined=answer["question"] is not None)
    return answer, ""


def handle_command(message: dict, group: Group) -> None:
    """`/market` sent in a known chat (`group` may be frozen). Called by `bot_chats._command`."""
    chat_id = message["chat"]["id"]
    thread = message.get("message_thread_id") if message.get("is_topic_message") else None

    def say(text: str, markup: dict | None = None) -> dict | None:
        extra = {"reply_parameters": {"message_id": message.get("message_id"), "allow_sending_without_reply": True}}
        if thread:
            extra["message_thread_id"] = thread
        if markup:
            extra["reply_markup"] = markup
        try:
            return telegram.api("sendMessage", chat_id=chat_id, text=text, **extra)
        except TelegramError:
            log.warning("/market answer to chat %s failed", chat_id, exc_info=True)
            return None

    if not group.bot_active:
        say(FROZEN)
        return
    source = _source(message)
    if source is None or (source.get("from") or {}).get("is_bot"):
        say(USAGE)
        return
    sender = message.get("from") or {}
    if sender.get("id") == chats.ANONYMOUS_ADMIN_ID or (message.get("sender_chat") or {}).get("id") == chat_id:
        say(ANONYMOUS)
        return
    trader = _trader(sender)
    if trader is None:
        say(NO_ACCOUNT, {"inline_keyboard": [[{"text": OPEN_CHAT_BUTTON, "url": chats.link(group)}]]})
        return
    if not chats.check_member(trader, group):
        say(NOT_MEMBER)
        return
    original = (source.get("text") or source.get("caption") or "").strip()
    text = " ".join(original.split())[:assist.SOURCE_MAX]
    if not text:
        say(NO_TEXT)
        return

    helper_on = assist.enabled() and group.assist_enabled
    if helper_on and group.assist_warned_at is None:
        # Before the first call in the chat nothing goes to the model: the caller repeats /market knowingly.
        if say(WARNING) is not None:
            Group.objects.filter(pk=group.pk, assist_warned_at__isnull=True).update(assist_warned_at=timezone.now())
        return

    answer, note = _ask(trader, text) if helper_on else (None, "")
    now = timezone.now()
    draft = MarketDraft(code=new_code(), trader=trader, group=group, source_text=original,
                        source_message_id=source.get("message_id"), thread_id=thread,
                        expires_at=now + timedelta(hours=settings.CHAT_DRAFT_HOURS))
    if answer is not None:
        draft.assisted = True
        draft.title = answer["question"] or text
        draft.criteria, draft.category = answer["criteria"], answer["category"]
        draft.closes_at, draft.resolves_at = answer["closes_at"], answer["resolves_at"]
        draft.options = answer["options"] or []
    else:
        draft.title = text if len(text) <= assist.TITLE_MAX else text[:assist.TITLE_MAX - 1].rstrip() + "…"
    draft.save()
    cost = chats.creation_cost(group)
    draft.shown_cost = cost
    sent = say(preview_text(draft, cost, note), preview_markup(draft, cost))
    if sent and sent.get("message_id"):
        draft.preview_message_id = sent["message_id"]
    draft.save(update_fields=["shown_cost", "preview_message_id"])


# --- Создать ------------------------------------------------------------------------------------

def _answer(callback: dict, text: str = "", alert: bool = False) -> None:
    try:
        telegram.api("answerCallbackQuery", callback_query_id=callback["id"], text=text, show_alert=alert)
    except TelegramError:
        pass


def create_from(draft: MarketDraft) -> tuple[Question, bool]:
    """The draft's chat market through `services.create_market`; (market, created now).

    The draft row is locked, so the button and the Mini App cannot make two markets of one draft; the request
    key from the code makes a repeat return the first market without a second charge. Raises MarketError.
    """
    with transaction.atomic():
        draft = MarketDraft.objects.select_for_update().get(pk=draft.pk)
        if draft.question_id:
            return draft.question, False
        question = services.create_market(
            draft.trader, title=draft.title, criteria=draft.criteria, category=draft.category,
            closes_at=draft.closes_at, resolves_at=draft.resolves_at, request_key=request_key(draft.code),
            group=draft.group, options=draft.options or None, announce=True, expected_cost=draft.shown_cost)
        draft.question = question
        draft.save(update_fields=["question"])
    return question, not question.replayed


def _edit_preview(draft: MarketDraft, question: Question, message: dict) -> None:
    if question.publication_state == "approved":
        text = CREATED.format(title=question.title)
        markup = {"inline_keyboard": [[{"text": OPEN_MARKET_BUTTON, "url": market_link(None, question)}]]}
    else:
        text, markup = CREATED_PENDING.format(title=question.title), {"inline_keyboard": []}
    message_id = message.get("message_id") or draft.preview_message_id
    if not message_id:
        return
    try:
        telegram.api("editMessageText", chat_id=draft.group.telegram_chat_id, message_id=message_id,
                     text=text, reply_markup=markup)
    except TelegramError:
        log.warning("draft preview in chat %s not updated", draft.group.telegram_chat_id, exc_info=True)


def _show_new_price(draft: MarketDraft, cost: int, message: dict) -> bool:
    """The button shows the new price; only then a press may charge it."""
    try:
        telegram.api("editMessageText", chat_id=draft.group.telegram_chat_id,
                     message_id=message.get("message_id") or draft.preview_message_id,
                     text=preview_text(draft, cost), reply_markup=preview_markup(draft, cost))
    except TelegramError:
        log.warning("draft price in chat %s not updated", draft.group.telegram_chat_id, exc_info=True)
        return False
    MarketDraft.objects.filter(pk=draft.pk).update(shown_cost=cost)
    return True


def handle_callback(callback: dict) -> None:
    """`Создать за <цена>` under the preview in the chat."""
    try:
        _press(callback)
    except Exception:  # noqa: BLE001 - never let a button crash the poller
        log.exception("draft button failed")
        _answer(callback, ALERT_FAILED, alert=True)


def _press(callback: dict) -> None:
    parts = (callback.get("data") or "").split(":")
    message = callback.get("message") or {}
    draft = None
    if len(parts) == 3 and parts[1] == "create" and parts[2]:
        draft = MarketDraft.objects.select_related("group", "trader", "question").filter(code=parts[2]).first()
    if draft is None or (message.get("chat") or {}).get("id") != draft.group.telegram_chat_id:
        _answer(callback, ALERT_NOT_FOUND, alert=True)
        return
    if (callback.get("from") or {}).get("id") != draft.trader.telegram_chat_id:
        _answer(callback, ALERT_ONLY_CALLER, alert=True)
        return
    if draft.question_id:
        _answer(callback, ALERT_ALREADY)
        _edit_preview(draft, draft.question, message)
        return
    if draft.expires_at <= timezone.now():
        _answer(callback, ALERT_EXPIRED, alert=True)
        return
    if not draft.assisted:
        _answer(callback, ALERT_NO_HELPER, alert=True)
        return
    group, trader = draft.group, draft.trader
    if not group.bot_active:
        _answer(callback, FROZEN, alert=True)
        return
    if not chats.check_member(trader, group):
        _answer(callback, NOT_MEMBER, alert=True)
        return
    # The price by chat size is taken now and fixed in the market (spec E5), as in POST /api/markets.
    chats.refresh_member_count(group)
    try:
        question, created = create_from(draft)
    except services.PriceChanged as exc:
        _answer(callback, str(exc) if _show_new_price(draft, exc.cost, message) else ALERT_FAILED, alert=True)
        return
    except MarketError as exc:
        _answer(callback, str(exc), alert=True)
        return
    if created:
        from .bot_resolution import announce_created
        from .events import log_event
        # Only the facts: no chat ID or link code in analytics.
        log_event(trader, "market_create", {"market": question.pk, "category": question.category, "chat": True,
                                            "draft": True, "announce": True, "via": "chat"})
        announce_created(question)
    _answer(callback, ALERT_CREATED if created else ALERT_ALREADY)
    _edit_preview(draft, question, message)
