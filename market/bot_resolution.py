"""Bot messages around market resolution (spec E4, MVP bot). Texts follow docs/mockups/Bot.dc.html."""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db.models import Sum

from . import telegram
from .models import Payout, Position, Question, Trader

# Telegram "🎉" message effect, allowed in private chats.
CONFETTI_EFFECT_ID = "5046509860389126442"


def app_url(path: str = "") -> str:
    return f"{settings.SITE_URL}/app/{path}"


def outcome_keyboard(question: Question) -> dict:
    return {"inline_keyboard": [
        [{"text": "ДА", "callback_data": f"res:{question.pk}:yes"}, {"text": "НЕТ", "callback_data": f"res:{question.pk}:no"}],
        [{"text": "Отменить рынок", "callback_data": f"res:{question.pk}:cancel"}],
    ]}


def _waiting(question: Question) -> int:
    return Position.objects.filter(question=question).count()


def ask_creator(question: Question) -> None:
    creator = question.creator
    if not creator or not creator.telegram_chat_id:
        return
    telegram.send(creator.telegram_chat_id,
                  f"Приём прогнозов на твоём рынке закрыт:\n«{question.title}»\n"
                  f"Чем закончилось? Участников ждут итог: {_waiting(question)}.",
                  reply_markup=outcome_keyboard(question))


def remind_creator(question: Question, number: int) -> None:
    creator = question.creator
    if not creator or not creator.telegram_chat_id:
        return
    left = settings.RESOLUTION_TIMEOUT_HOURS - 24 * number
    telegram.send(creator.telegram_chat_id,
                  f"Напоминание: рынок «{question.title}» ждёт итог. "
                  f"Если не указать его за {left} ч, рынок отменится и всем вернутся монеты.",
                  reply_markup=outcome_keyboard(question))


def send_results(question: Question) -> None:
    """Result and payout for every participant; confetti for winners."""
    label = {"yes": "ДА", "no": "НЕТ"}.get(question.outcome or "", "")
    for trader in Trader.objects.filter(positions__question=question, telegram_chat_id__isnull=False).distinct():
        paid = Payout.objects.filter(question=question, trader=trader).aggregate(t=Sum("amount"))["t"] or Decimal(0)
        markup = {"inline_keyboard": [[{"text": "Открыть рынок", "web_app": {"url": app_url(f"market/{question.pk}")}}]]}
        if question.status == Question.Status.ANNULLED:
            text = f"Рынок «{question.title}» отменён. Возврат: +{paid:.2f}."
            telegram.send(trader.telegram_chat_id, text, reply_markup=markup)
        elif paid > 0:
            text = f"Рынок «{question.title}» завершён: итог {label}. Ты угадал! Выплата: +{paid:.2f}."
            telegram.send(trader.telegram_chat_id, text, reply_markup=markup, message_effect_id=CONFETTI_EFFECT_ID)
        else:
            text = f"Рынок «{question.title}» завершён: итог {label}. В этот раз мимо."
            telegram.send(trader.telegram_chat_id, text, reply_markup=markup)
