"""Bot messages around market resolution (spec E4, MVP bot). Texts follow docs/mockups/Bot.dc.html."""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db.models import Q, Sum
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
    if question.publication_state != "approved":
        return False
    creator = question.creator
    if not creator or not creator.telegram_chat_id:
        return False
    when = day_month(question.resolves_at) if question.resolves_at else "В дату итога"
    how = ("Отметишь ДА или НЕТ по каждому варианту и пришлёшь фото или скриншот — его увидят все участники."
           if question.options or question.submarkets.exists()
           else "Нажмёшь ДА или НЕТ и пришлёшь фото или скриншот — его увидят все участники.")
    text = (f"Рынок опубликован:\n«{question.title}»\n"
            f"{when} напишу тебе здесь и спрошу, чем закончилось. {how}")
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


def outcome_keyboard(question: Question, picked: dict[int, str] | None = None) -> dict:
    subs = list(question.submarkets.all().order_by("id"))
    if subs:
        chosen = picked or {}
        rows = []
        for sub in subs:
            label = (sub.option_label or sub.title)[:24]
            val = chosen.get(sub.pk)
            rows.append([
                {"text": f"{'✅ ' if val == 'yes' else ''}ДА · {label}",
                 "callback_data": f"resopt:{question.pk}:{sub.pk}:yes"},
                {"text": f"{'❌ ' if val == 'no' else ''}НЕТ · {label}",
                 "callback_data": f"resopt:{question.pk}:{sub.pk}:no"},
            ])
        if all(chosen.get(s.pk) in ("yes", "no") for s in subs):
            rows.append([{"text": "Продолжить →", "callback_data": f"resnext:{question.pk}"}])
        rows.append([{"text": "Отменить рынок", "callback_data": f"res:{question.pk}:cancel"}])
        return {"inline_keyboard": rows}
    return {"inline_keyboard": [
        [{"text": "ДА", "callback_data": f"res:{question.pk}:yes"}, {"text": "НЕТ", "callback_data": f"res:{question.pk}:no"}],
        [{"text": "Отменить рынок", "callback_data": f"res:{question.pk}:cancel"}],
    ]}


def _waiting(question: Question) -> int:
    if question.submarkets.exists():
        return (Position.objects.filter(Q(question=question) | Q(question__parent=question))
                .values("trader_id").distinct().count())
    return Position.objects.filter(question=question).count()


def multi_prompt_lines(subs: list[Question], picked: dict[int, str] | None = None) -> str:
    chosen = picked or {}
    lines = []
    for idx, sub in enumerate(subs, start=1):
        val = chosen.get(sub.pk)
        mark = "ДА" if val == "yes" else ("НЕТ" if val == "no" else "не выбрано")
        lines.append(f"{idx}. {sub.option_label or sub.title} — {mark}")
    return "\n".join(lines)


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
    subs = list(question.submarkets.all().order_by("id"))
    if subs:
        text = (f"Приём прогнозов на твоём рынке закрыт:\n«{question.title}»\n"
                f"Отметь ДА или НЕТ для каждого варианта. Участников ждут итог: {_waiting(question)}.\n\n"
                f"{multi_prompt_lines(subs)}")
    else:
        text = (f"Приём прогнозов на твоём рынке закрыт:\n«{question.title}»\n"
                f"Чем закончилось? Участников ждут итог: {_waiting(question)}.")
    telegram.send(creator.telegram_chat_id, text, reply_markup=outcome_keyboard(question))


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
    root = question.parent or question
    subs = list(root.submarkets.all().order_by("id"))
    family_ids = [root.pk, *(s.pk for s in subs)]
    markup = {"inline_keyboard": [[app_button("Открыть рынок", f"market/{root.pk}")]]}
    if subs:
        summary = ", ".join(f"{s.option_label or s.title}: {'ДА' if s.outcome == 'yes' else 'НЕТ'}" for s in subs)
        for trader in Trader.objects.filter(positions__question_id__in=family_ids).distinct():
            paid = Payout.objects.filter(question_id__in=family_ids, trader=trader).aggregate(t=Sum("amount"))["t"] or Decimal(0)
            payload = {"reply_markup": markup}
            if root.status == Question.Status.ANNULLED:
                title, text = "Рынок отменён:", f"«{root.title}». Возврат: +{coins(paid)}."
                bot_text = f"Рынок «{root.title}» отменён. Возврат: +{paid:.2f}."
            elif paid > 0:
                title, text = f"Ты угадал! +{coins(paid)}", f"«{root.title}» завершён ({summary})."
                bot_text = None
                payload["message_effect_id"] = CONFETTI_EFFECT_ID
            else:
                title, text = "В этот раз мимо:", f"«{root.title}» завершён ({summary})."
                bot_text = f"Рынок «{root.title}» завершён ({summary}). В этот раз мимо."
            enqueue(trader, "results", f"market-result:{root.pk}:{trader.pk}", title, text,
                    question=root, payload=payload, bot_text=bot_text)
        return
    label = {"yes": "ДА", "no": "НЕТ"}.get(root.outcome or "", "")
    for trader in Trader.objects.filter(positions__question=root).distinct():
        paid = Payout.objects.filter(question=root, trader=trader).aggregate(t=Sum("amount"))["t"] or Decimal(0)
        payload = {"reply_markup": markup}
        if root.status == Question.Status.ANNULLED:
            title, text = "Рынок отменён:", f"«{root.title}». Возврат: +{coins(paid)}."
            bot_text = f"Рынок «{root.title}» отменён. Возврат: +{paid:.2f}."
        elif paid > 0:
            title, text = f"Ты угадал! +{coins(paid)}", f"«{root.title}» закрылся с итогом {label}."
            bot_text = None
            payload["message_effect_id"] = CONFETTI_EFFECT_ID
        else:
            title, text = "В этот раз мимо:", f"«{root.title}» закрылся с итогом {label}."
            bot_text = f"Рынок «{root.title}» завершён: итог {label}. В этот раз мимо."
        enqueue(trader, "results", f"market-result:{root.pk}:{trader.pk}", title, text,
                question=root, payload=payload, bot_text=bot_text)
