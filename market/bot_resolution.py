"""Bot messages around market resolution (spec E4, MVP bot). Texts follow docs/mockups/Bot.dc.html."""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from . import telegram
from .models import Payout, Position, Question, Trader

# Telegram "🎉" message effect, allowed in private chats.
CONFETTI_EFFECT_ID = "5046509860389126442"


def app_url(path: str = "") -> str:
    return f"{settings.SITE_URL}/app/{path}"


def app_button(text: str = "Открыть приложение", path: str = "") -> dict:
    return {"text": text, "web_app": {"url": app_url(path)}}


MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря")


def day_month(moment) -> str:
    """«16 ноября» in the project's time zone."""
    local = timezone.localtime(moment)
    return f"{local.day} {MONTHS[local.month - 1]}"


def announce_created(question: Question) -> bool:
    """Right after creation: the creator learns in the bot when and how the outcome will be asked.

    Also tells whether the bot can write to them at all: False when Telegram refuses or is unreachable.
    The request waits for Telegram, so the timeout is short.
    """
    creator = question.creator
    if not creator or not creator.telegram_chat_id:
        return False
    when = day_month(question.resolves_at) if question.resolves_at else "В дату итога"
    text = (f"Рынок опубликован:\n«{question.title}»\n"
            f"{when} напишу тебе здесь и спрошу, чем закончилось. "
            "Нажмёшь ДА или НЕТ и пришлёшь фото или скриншот — его увидят все участники.")
    try:
        telegram.send(creator.telegram_chat_id, text, http_timeout=5,
                      reply_markup={"inline_keyboard": [[app_button("Открыть рынок", f"market/{question.pk}")]]})
    except telegram.TelegramError:
        return False
    return True


def welcome(chat_id: int) -> None:
    """/start: everything happens in the Mini App; people with an account also get «Мои рынки»."""
    keyboard = [[app_button()]]
    if Trader.objects.filter(telegram_chat_id=chat_id).exists():
        from .bot_markets import OPEN_LIST
        keyboard.append([{"text": "Мои рынки", "callback_data": OPEN_LIST}])
    telegram.send(chat_id, "Привет! Здесь рынок прогнозов на виртуальные монеты: делай прогнозы, "
                           "создавай свои рынки и следи за итогами. Всё — в приложении.",
                  reply_markup={"inline_keyboard": keyboard})


def outcome_keyboard(question: Question) -> dict:
    return {"inline_keyboard": [
        [{"text": "ДА", "callback_data": f"res:{question.pk}:yes"}, {"text": "НЕТ", "callback_data": f"res:{question.pk}:no"}],
        [{"text": "Отменить рынок", "callback_data": f"res:{question.pk}:cancel"}],
    ]}


def _waiting(question: Question) -> int:
    return Position.objects.filter(question=question).count()


def _resolve_notice(question: Question, key: str) -> None:
    """Inbox item with «Указать итог в боте»; the bot message itself is mandatory and sent directly."""
    from .notifications import notify
    notice = notify(question.creator, f"«{question.title}» Участников ждут итог: {_waiting(question)}.",
                    kind="resolve", question=question, key=key)
    if not notice.title:
        notice.title = "Пора указать итог:"
        notice.save(update_fields=["title"])


def ask_creator(question: Question) -> None:
    creator = question.creator
    if not creator:
        return
    _resolve_notice(question, f"resolve-ask:{question.pk}")
    if not creator.telegram_chat_id:
        return
    telegram.send(creator.telegram_chat_id,
                  f"Приём прогнозов на твоём рынке закрыт:\n«{question.title}»\n"
                  f"Чем закончилось? Участников ждут итог: {_waiting(question)}.",
                  reply_markup=outcome_keyboard(question))


def remind_creator(question: Question, number: int) -> None:
    creator = question.creator
    if not creator:
        return
    _resolve_notice(question, f"resolve-remind:{question.pk}:{number}")
    if not creator.telegram_chat_id:
        return
    left = settings.RESOLUTION_TIMEOUT_HOURS - 24 * number
    telegram.send(creator.telegram_chat_id,
                  f"Напоминание: рынок «{question.title}» ждёт итог. "
                  f"Если не указать его за {left} ч, рынок отменится и всем вернутся монеты.",
                  reply_markup=outcome_keyboard(question))


def send_results(question: Question) -> None:
    """Result and payout for every participant through the notification outbox; confetti for winners.

    Keyed per market and trader, so a repeated call never sends twice.
    """
    from .bot_notifications import coins, enqueue
    label = {"yes": "ДА", "no": "НЕТ"}.get(question.outcome or "", "")
    markup = {"inline_keyboard": [[app_button("Открыть рынок", f"market/{question.pk}")]]}
    for trader in Trader.objects.filter(positions__question=question).distinct():
        paid = Payout.objects.filter(question=question, trader=trader).aggregate(t=Sum("amount"))["t"] or Decimal(0)
        payload = {"reply_markup": markup}
        if question.status == Question.Status.ANNULLED:
            title, text = "Рынок отменён:", f"«{question.title}». Возврат: +{coins(paid)}."
            bot_text = f"Рынок «{question.title}» отменён. Возврат: +{paid:.2f}."
        elif paid > 0:
            title, text = f"Ты угадал! +{coins(paid)}", f"«{question.title}» закрылся с итогом {label}."
            bot_text = None
            payload["message_effect_id"] = CONFETTI_EFFECT_ID
        else:
            title, text = "В этот раз мимо:", f"«{question.title}» закрылся с итогом {label}."
            bot_text = f"Рынок «{question.title}» завершён: итог {label}. В этот раз мимо."
        enqueue(trader, "results", f"market-result:{question.pk}:{trader.pk}", title, text,
                question=question, payload=payload, bot_text=bot_text)
