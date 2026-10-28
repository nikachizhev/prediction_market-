"""Creator's outcome dialog and participants' disputes in the bot (spec E4, docs/mockups/Bot.dc.html).

The dialog state lives in ResolutionDraft; market state and money change only via services.
The user is always the Telegram sender of the update (from.id in a private chat).
"""
from __future__ import annotations

import html
import logging
import os
from decimal import ROUND_DOWN, Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction

from . import services, telegram
from .bot_resolution import app_button, outcome_keyboard
from .models import Question, ResolutionDraft, Trader

log = logging.getLogger(__name__)

OUTCOMES = ("yes", "no", "cancel")
LABELS = {"yes": "ДА", "no": "НЕТ", "cancel": "отмена рынка"}
CAPTION_LIMIT = 1024


# --- helpers -------------------------------------------------------------------------------------

def _trader(user_id) -> Trader | None:
    return Trader.objects.filter(telegram_chat_id=user_id).first()


def _question(question_id) -> Question | None:
    try:
        return Question.objects.select_related("creator").filter(pk=int(question_id)).first()
    except (TypeError, ValueError):
        return None


def resolve_error(trader: Trader | None, question: Question | None) -> str | None:
    """Why this person can't state the outcome now (same rules as services.propose_resolution)."""
    if question is None:
        return "Рынок не найден."
    if trader is None or question.creator_id != trader.pk:
        return "Итог указывает только создатель рынка."
    if question.status not in (Question.Status.OPEN, Question.Status.CLOSED) or question.resolution_state:
        return "Итог этого рынка уже указан."
    if question.accepting_entries:
        return "Приём прогнозов ещё идёт — итог можно указать после его окончания."
    return None


def _quiet(method: str, **data) -> None:
    """Best-effort UI calls (answering a button, removing a keyboard) must not break the dialog."""
    try:
        telegram.api(method, **data)
    except telegram.TelegramError:
        log.warning("Telegram %s failed", method)


def _drop_buttons(message: dict) -> None:
    if message.get("message_id"):
        _quiet("editMessageReplyMarkup", chat_id=message["chat"]["id"], message_id=message["message_id"],
               reply_markup={"inline_keyboard": []})


def _discard(draft: ResolutionDraft) -> None:
    if draft.evidence:
        draft.evidence.delete(save=False)
    draft.delete()


def _creator_fee(question: Question) -> int:
    share = question.fee_pool * Decimal(settings.CREATOR_FEE_SHARE)
    return int(share.to_integral_value(rounding=ROUND_DOWN))


def _send_evidence(chat_id, file_id: str, is_document: bool, caption: str, markup: dict) -> None:
    kind, field = ("sendDocument", "document") if is_document else ("sendPhoto", "photo")
    telegram.api(kind, chat_id=chat_id, caption=caption[:CAPTION_LIMIT], parse_mode="HTML",
                 reply_markup=markup, **{field: file_id})


# --- entry points --------------------------------------------------------------------------------

def start(chat_id, question_id: str) -> None:
    """/start resolve_<id> — the «Указать итог» link from the Mini App."""
    trader, question = _trader(chat_id), _question(question_id)
    if trader is None:
        telegram.send(chat_id, "Сначала открой приложение — там создаётся аккаунт.",
                      reply_markup={"inline_keyboard": [[app_button()]]})
        return
    error = resolve_error(trader, question)
    if error:
        telegram.send(chat_id, error)
        return
    telegram.send(chat_id, f"Рынок «{question.title}»\nЧем закончилось?", reply_markup=outcome_keyboard(question))


def start_dispute(chat_id, question_id: str) -> None:
    """/start dispute_<id> — «Оспорить итог» from the Mini App market card."""
    _on_dispute(chat_id, None, _question(question_id), None)


def handle_message(message: dict) -> bool:
    """Evidence for an active draft. Returns False when the message isn't part of this dialog."""
    user_id = message.get("from", {}).get("id")
    if user_id is None or user_id != message.get("chat", {}).get("id"):
        return False
    text = message.get("text", "")
    if text.startswith("/"):
        return False
    draft = ResolutionDraft.objects.select_related("question", "trader").filter(trader__telegram_chat_id=user_id).first()
    if draft is None:
        return False
    chat_id = message["chat"]["id"]
    error = resolve_error(draft.trader, draft.question)
    if error:
        _discard(draft)
        telegram.send(chat_id, error)
        return True

    file_id, is_document = "", False
    if message.get("photo"):
        file_id = max(message["photo"], key=lambda p: (p.get("width", 0) * p.get("height", 0), p.get("file_size", 0)))["file_id"]
    elif (message.get("document") or {}).get("mime_type", "").startswith("image/"):
        file_id, is_document = message["document"]["file_id"], True
    if not file_id:
        if draft.stage == ResolutionDraft.Stage.PREVIEW:
            return False  # the preview buttons are waiting; let the rest of the bot answer
        telegram.send(chat_id, "Нажми скрепку и выбери фото")
        return True

    try:
        content, path = telegram.download_file(file_id)
    except telegram.TelegramError:
        telegram.send(chat_id, "Не удалось получить фото. Пришли его ещё раз.")
        return True
    if draft.evidence:
        draft.evidence.delete(save=False)
    ext = os.path.splitext(path)[1].lower() or ".jpg"
    draft.evidence.save(f"{draft.question_id}{ext}", ContentFile(content), save=False)
    draft.evidence_file_id = file_id
    draft.evidence_is_document = is_document
    draft.note = (message.get("caption") or "")[:500]
    draft.stage = ResolutionDraft.Stage.PREVIEW
    draft.save()
    _preview(chat_id, draft)
    return True


def _preview_keyboard(question_id: int) -> dict:
    return {"inline_keyboard": [[{"text": "Подтвердить", "callback_data": f"resok:{question_id}"},
                                 {"text": "Изменить", "callback_data": f"resedit:{question_id}"}]]}


def _preview(chat_id, draft: ResolutionDraft) -> None:
    caption = f"Итог: <b>{LABELS[draft.outcome]}</b>"
    if draft.note:
        caption += f"\n{html.escape(draft.note)}"
    markup = _preview_keyboard(draft.question_id)
    if draft.evidence_file_id:
        _send_evidence(chat_id, draft.evidence_file_id, draft.evidence_is_document, caption, markup)
    else:
        telegram.send(chat_id, caption + "\nБез доказательства.", parse_mode="HTML", reply_markup=markup)


def handle_callback(callback: dict) -> None:
    data = callback.get("data") or ""
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    user_id = (callback.get("from") or {}).get("id")
    answer = {"callback_query_id": callback.get("id")}
    if chat.get("type") != "private" or user_id is None or chat.get("id") != user_id:
        _quiet("answerCallbackQuery", **answer)
        return
    action, _, rest = data.partition(":")
    handler = HANDLERS.get(action)
    note = None
    if handler:
        question_id, _, arg = rest.partition(":")
        note = handler(user_id, message, _question(question_id), arg)
    _quiet("answerCallbackQuery", **answer, **({"text": note} if note else {}))


# --- creator ---------------------------------------------------------------------------------------

def _on_outcome(user_id, message, question, outcome) -> str | None:
    trader = _trader(user_id)
    error = resolve_error(trader, question)
    if error:
        _drop_buttons(message)
        telegram.send(user_id, error)
        return None
    if outcome not in OUTCOMES:
        return None
    old = ResolutionDraft.objects.filter(trader=trader).first()
    if old:
        _discard(old)
    ResolutionDraft.objects.create(trader=trader, question=question, outcome=outcome)
    _drop_buttons(message)
    if outcome == "cancel":
        telegram.send(user_id, f"Итог: <b>{LABELS[outcome]}</b>. Можешь прислать фото или скриншот, который это "
                               "объясняет, — его увидят все участники. Или подтверди без доказательства.",
                      parse_mode="HTML",
                      reply_markup={"inline_keyboard": [[{"text": "Без доказательства",
                                                          "callback_data": f"resnone:{question.pk}"}]]})
    else:
        telegram.send(user_id, f"Итог: <b>{LABELS[outcome]}</b>. Пришли фото или скриншот, который это "
                               "подтверждает — его увидят все участники.", parse_mode="HTML")
    return LABELS[outcome]


def _draft(user_id, question) -> ResolutionDraft | None:
    if question is None:
        return None
    return ResolutionDraft.objects.filter(trader__telegram_chat_id=user_id, question=question).first()


def _on_no_evidence(user_id, message, question, _arg) -> str | None:
    draft = _draft(user_id, question)
    _drop_buttons(message)
    if draft is None or draft.outcome != "cancel":
        return "Этот шаг уже пройден."
    draft.stage = ResolutionDraft.Stage.PREVIEW
    draft.save(update_fields=["stage", "updated_at"])
    _preview(user_id, draft)
    return None


def _on_edit(user_id, message, question, _arg) -> str | None:
    draft = _draft(user_id, question)
    _drop_buttons(message)
    if draft is None:
        return "Этот шаг уже пройден."
    _discard(draft)
    telegram.send(user_id, f"Рынок «{question.title}»\nЧем закончилось?", reply_markup=outcome_keyboard(question))
    return None


def _on_confirm(user_id, message, question, _arg) -> str | None:
    _drop_buttons(message)
    if question is None:
        return None
    with transaction.atomic():
        # The lock makes a double tap on «Подтвердить» publish only once.
        draft = (ResolutionDraft.objects.select_for_update().select_related("trader")
                 .filter(trader__telegram_chat_id=user_id, question=question,
                         stage=ResolutionDraft.Stage.PREVIEW).first())
        if draft is None:
            question.refresh_from_db()
            return "Итог уже опубликован." if question.resolution_state else "Этот шаг уже пройден."
        evidence = None
        if draft.evidence:
            with draft.evidence.open("rb") as f:
                evidence = ContentFile(f.read(), name=os.path.basename(draft.evidence.name))
        try:
            question = services.propose_resolution(draft.trader, question, draft.outcome, evidence=evidence,
                                                   evidence_file_id=draft.evidence_file_id, note=draft.note)
        except services.MarketError as exc:
            error = str(exc)
        else:
            error = None
        is_document = draft.evidence_is_document
        transaction.on_commit(lambda d=draft: d.evidence and d.evidence.delete(save=False))
        draft.delete()
    if error:
        telegram.send(user_id, error)
        return None
    _report_to_creator(user_id, question)
    notify_participants(question, is_document=is_document)
    return "Опубликовано"


def _report_to_creator(user_id, question: Question) -> None:
    hours = settings.DISPUTE_WINDOW_HOURS
    markup = {"inline_keyboard": [[app_button("Посмотреть рынок", f"market/{question.pk}")]]}
    if question.proposed_outcome == "cancel":
        text = (f"Готово. Отмена рынка опубликована. У участников есть {hours} часа, чтобы оспорить её. "
                "Если спора не будет, всем вернутся монеты.")
    else:
        text = (f"Готово. Итог <b>{LABELS[question.proposed_outcome]}</b> опубликован вместе с фото. "
                f"У участников есть {hours} часа, чтобы оспорить его. Если спора не будет, выплаты придут "
                f"автоматически, а тебе — половина комиссии: <b>+{_creator_fee(question)}</b>.")
    telegram.send(user_id, text, parse_mode="HTML", reply_markup=markup)


def notify_participants(question: Question, *, is_document: bool = False) -> int:
    """The stated outcome with its proof and a «Оспорить» button for everyone who traded."""
    title = html.escape(question.title[:200])
    if question.proposed_outcome == "cancel":
        head = f"Создатель рынка «{title}» отменяет его."
    else:
        head = f"Создатель рынка «{title}» указал итог: <b>{LABELS[question.proposed_outcome]}</b>."
    text = head
    if question.evidence_note:
        text += f"\n{html.escape(question.evidence_note[:300])}"
    text += (f"\nНе согласен — оспорь в течение {settings.DISPUTE_WINDOW_HOURS} часов. "
             f"Спор стоит {settings.DISPUTE_COST} монет.")
    markup = {"inline_keyboard": [[{"text": "Оспорить", "callback_data": f"disp:{question.pk}"}],
                                  [app_button("Открыть рынок", f"market/{question.pk}")]]}
    sent = 0
    people = (Trader.objects.filter(trades__question=question, telegram_chat_id__isnull=False)
              .exclude(pk=question.creator_id).distinct())
    for trader in people:
        try:
            if question.evidence_file_id:
                _send_evidence(trader.telegram_chat_id, question.evidence_file_id, is_document, text, markup)
            else:
                telegram.send(trader.telegram_chat_id, text, parse_mode="HTML", reply_markup=markup)
            sent += 1
        except telegram.TelegramError:
            log.warning("could not notify trader %s about market %s", trader.pk, question.pk)
    return sent


# --- participants ----------------------------------------------------------------------------------

def _on_dispute(user_id, message, question, _arg) -> str | None:
    if question is None:
        return "Рынок не найден."
    telegram.send(user_id, f"Спор стоит {settings.DISPUTE_COST} монет; вернём их, если модератор признает спор. Оспорить?",
                  reply_markup={"inline_keyboard": [[{"text": "Оспорить", "callback_data": f"dispok:{question.pk}"},
                                                     {"text": "Не надо", "callback_data": f"dispno:{question.pk}"}]]})
    return None


def _on_dispute_confirm(user_id, message, question, _arg) -> str | None:
    _drop_buttons(message)
    trader = _trader(user_id)
    if question is None or trader is None:
        telegram.send(user_id, "Оспорить итог может только участник рынка.")
        return None
    try:
        services.open_dispute(trader, question)
    except services.MarketError as exc:
        telegram.send(user_id, str(exc))
        return None
    telegram.send(user_id, f"Спор открыт, списано {settings.DISPUTE_COST} монет. Итог проверит модератор.")
    return "Спор открыт"


def _on_dispute_decline(user_id, message, question, _arg) -> str | None:
    _drop_buttons(message)
    return "Хорошо, не оспариваем"


HANDLERS = {
    "res": _on_outcome,
    "resnone": _on_no_evidence,
    "resedit": _on_edit,
    "resok": _on_confirm,
    "disp": _on_dispute,
    "dispok": _on_dispute_confirm,
    "dispno": _on_dispute_decline,
}
