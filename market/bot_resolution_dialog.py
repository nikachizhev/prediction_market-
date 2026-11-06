"""Outcome dialog, participants' outcomes, proof and disputes in the bot (spec E4, docs/mockups/Bot.dc.html).

The creator states the outcome in one tap; «Приложить фото» before it is optional (phase 3d). A photo
waiting for the tap lives in ResolutionDraft, as do the picks of a market with options. Participants
state the outcome for a bond when the creator stays silent. Market state and money change only via
services. The user is always the Telegram sender of the update (from.id in a private chat).
Phase 3d texts that are in neither the mockups nor the spec are marked «text outside the spec».
"""
from __future__ import annotations

import html
import logging
import os
import re
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import services, telegram
from .bot_resolution import (app_button, is_chat_market, multi_prompt_lines, outcome_keyboard, participant_keyboard,
                             participant_prompt, participants, picks_from_code)
from .models import Question, ResolutionDraft, Trader

log = logging.getLogger(__name__)

OUTCOMES = ("yes", "no", "cancel")
LABELS = {"yes": "ДА", "no": "НЕТ", "cancel": "отмена рынка"}
CAPTION_LIMIT = 1024
LINK = re.compile(r"(https?://|www\.|t\.me/)\S+", re.IGNORECASE)


# --- helpers -------------------------------------------------------------------------------------

def _trader(user_id) -> Trader | None:
    return Trader.objects.filter(telegram_chat_id=user_id).first()


def _question(question_id) -> Question | None:
    try:
        q = Question.objects.select_related("creator", "group").filter(pk=int(question_id), publication_state="approved").first()
        if q is not None and q.parent_id:
            return Question.objects.select_related("creator", "group").filter(pk=q.parent_id, publication_state="approved").first()
        return q
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


def _fee_share(question: Question) -> int:
    subs = list(question.submarkets.all())
    pool = question.fee_pool + sum((s.fee_pool for s in subs), Decimal(0))
    share = pool * Decimal(settings.CREATOR_FEE_SHARE)
    return int(share.to_integral_value(rounding=ROUND_DOWN))


def _encode_multi(subs: list[Question], picked: dict[int, str]) -> str:
    return "".join({"yes": "y", "no": "n"}.get(picked.get(sub.pk, ""), "?") for sub in subs)


def _decode_multi(subs: list[Question], code: str) -> dict[int, str] | None:
    """Picks stored in a draft; upper case letters come from drafts made before phase 3d."""
    if not subs or len(code) != len(subs) or any(c not in "ynYN?" for c in code):
        return None
    return {sub.pk: ("yes" if c in "yY" else "no") for sub, c in zip(subs, code) if c in "ynYN"}


def _subs(question: Question) -> list[Question]:
    return list(question.submarkets.all().order_by("id"))


def _creator_prompt(question: Question, picked: dict[int, str] | None = None) -> str:
    subs = _subs(question)
    if subs:
        return (f"Рынок «{question.title}»\n"
                f"Отметь ДА или НЕТ для каждого варианта:\n\n"
                f"{multi_prompt_lines(subs, picked)}")
    return f"Рынок «{question.title}»\nЧем закончилось?"


def _multi_summary_html(subs: list[Question], picked: dict[int, str]) -> str:
    return "\n".join(
        f"• {html.escape(s.option_label or s.title)}: <b>{LABELS[picked[s.pk]]}</b>"
        for s in subs if s.pk in picked
    )


def _image(message: dict) -> tuple[str, bool]:
    """The file_id of a photo, or of an image sent as a file; ("", False) when there is none."""
    if message.get("photo"):
        best = max(message["photo"], key=lambda p: (p.get("width", 0) * p.get("height", 0), p.get("file_size", 0)))
        return best["file_id"], False
    if (message.get("document") or {}).get("mime_type", "").startswith("image/"):
        return message["document"]["file_id"], True
    return "", False


def _download(chat_id, file_id: str, name: str) -> ContentFile | None:
    try:
        content, path = telegram.download_file(file_id)
    except telegram.TelegramError:
        telegram.send(chat_id, "Не удалось получить фото. Пришли его ещё раз.")
        return None
    return ContentFile(content, name=f"{name}{os.path.splitext(path)[1].lower() or '.jpg'}")


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
    telegram.send(chat_id, _creator_prompt(question), reply_markup=outcome_keyboard(question))


def start_dispute(chat_id, question_id: str) -> None:
    """/start dispute_<id> — «Оспорить итог» from the Mini App market card."""
    _on_dispute(chat_id, None, _question(question_id), None)


def handle_message(message: dict) -> bool:
    """A photo for the creator's outcome, or proof for a disputed outcome. False — not part of this dialog."""
    user_id = message.get("from", {}).get("id")
    if user_id is None or user_id != message.get("chat", {}).get("id"):
        return False
    if message.get("text", "").startswith("/"):
        return False
    draft = ResolutionDraft.objects.select_related("question", "trader").filter(trader__telegram_chat_id=user_id).first()
    if draft is not None:
        error = resolve_error(draft.trader, draft.question)
        if error is None:
            return _draft_photo(message, draft)
        _discard(draft)  # the outcome is already stated (maybe by a participant) — the photo isn't needed
    trader = _trader(user_id)
    question = _awaiting_evidence(trader) if trader else None
    if question is None:
        return False
    return _evidence(message, trader, question)


def _draft_photo(message: dict, draft: ResolutionDraft) -> bool:
    chat_id = message["chat"]["id"]
    file_id, is_document = _image(message)
    if not file_id:
        if draft.stage == ResolutionDraft.Stage.PREVIEW:
            return False  # the outcome buttons are waiting; let the rest of the bot answer
        telegram.send(chat_id, "Нажми скрепку и выбери фото")
        return True
    content = _download(chat_id, file_id, str(draft.question_id))
    if content is None:
        return True
    if draft.evidence:
        draft.evidence.delete(save=False)
    draft.evidence.save(content.name, content, save=False)
    draft.evidence_file_id = file_id
    draft.evidence_is_document = is_document
    draft.note = (message.get("caption") or "")[:500]
    draft.stage = ResolutionDraft.Stage.PREVIEW
    draft.save()
    question = draft.question
    picked = _decode_multi(_subs(question), draft.outcome) or {}
    # Text outside the spec.
    telegram.send(chat_id, "Фото приложено — его увидят все участники. Теперь нажми, чем закончилось.\n\n"
                  + _creator_prompt(question, picked), reply_markup=outcome_keyboard(question, picked))
    return True


def handle_callback(callback: dict) -> None:
    data = callback.get("data") or ""
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    user_id = (callback.get("from") or {}).get("id")
    answer = {"callback_query_id": callback.get("id")}
    if chat.get("type") != "private" or user_id is None or chat.get("id") != user_id:
        _quiet("answerCallbackQuery", **answer)
        return
    _quiet("answerCallbackQuery", **answer)
    action, _, rest = data.partition(":")
    handler = HANDLERS.get(action)
    note = None
    if handler:
        question_id, _, arg = rest.partition(":")
        note = handler(user_id, message, _question(question_id), arg)
    if note:
        telegram.send(user_id, note)


# --- creator ---------------------------------------------------------------------------------------

def _creator_check(user_id, message, question) -> Trader | None:
    trader = _trader(user_id)
    error = resolve_error(trader, question)
    if error:
        _drop_buttons(message)
        telegram.send(user_id, error)
        return None
    return trader


def _on_outcome(user_id, message, question, outcome) -> str | None:
    """`res:<id>:<yes|no|cancel>` — one tap states the outcome."""
    trader = _trader(user_id)
    if (question is not None and trader is not None and question.resolution_state
            and question.proposed_by_id == trader.pk and question.proposed_outcome == outcome):
        return None  # a repeated tap on the same button: the first one already answered
    trader = _creator_check(user_id, message, question)
    if trader is None or outcome not in OUTCOMES:
        return None
    if outcome != "cancel" and question.submarkets.exists():
        draft = _draft(user_id, question)
        picked = (_decode_multi(_subs(question), draft.outcome) if draft else None) or {}
        _drop_buttons(message)
        telegram.send(user_id, _creator_prompt(question, picked), reply_markup=outcome_keyboard(question, picked))
        return None
    _drop_buttons(message)
    return _publish(user_id, trader, question, outcome)


def _on_option(user_id, message, question, arg) -> str | None:
    """`resopt:<id>:<option>:<yes|no>` — a pick for one option of a market with options."""
    trader = _creator_check(user_id, message, question)
    if trader is None:
        return None
    subs = _subs(question)
    sub_str, _, choice = arg.partition(":")
    by_id = {s.pk: s for s in subs}
    try:
        sub_id = int(sub_str)
    except ValueError:
        return None
    if sub_id not in by_id or choice not in ("yes", "no"):
        return None
    draft = ResolutionDraft.objects.filter(trader=trader).first()
    if draft and draft.question_id != question.pk:
        _discard(draft)
        draft = None
    picked = (_decode_multi(subs, draft.outcome) if draft else None) or {}
    picked[sub_id] = choice
    if draft:
        draft.outcome = _encode_multi(subs, picked)
        draft.save(update_fields=["outcome", "updated_at"])
    else:
        ResolutionDraft.objects.create(trader=trader, question=question, outcome=_encode_multi(subs, picked),
                                       stage=ResolutionDraft.Stage.PREVIEW)
    if message.get("message_id"):
        _quiet("editMessageText", chat_id=user_id, message_id=message["message_id"],
               text=_creator_prompt(question, picked), reply_markup=outcome_keyboard(question, picked))
    return None


def _on_next(user_id, message, question, _arg) -> str | None:
    """`resnext:<id>` — «Подтвердить итоги» once every option is picked."""
    trader = _creator_check(user_id, message, question)
    if trader is None:
        return None
    subs = _subs(question)
    draft = _draft(user_id, question)
    picked = _decode_multi(subs, draft.outcome) if (subs and draft) else None
    if not picked or len(picked) < len(subs):
        return "Отметь ДА или НЕТ для каждого варианта."
    _drop_buttons(message)
    return _publish(user_id, trader, question, "yes", picked)


def _on_photo(user_id, message, question, _arg) -> str | None:
    """`resphoto:<id>` — «Приложить фото»: the next photo goes with the outcome."""
    trader = _creator_check(user_id, message, question)
    if trader is None:
        return None
    draft = ResolutionDraft.objects.filter(trader=trader).first()
    if draft and draft.question_id != question.pk:
        _discard(draft)
        draft = None
    if draft is None:
        draft = ResolutionDraft(trader=trader, question=question, outcome="")
    draft.stage = ResolutionDraft.Stage.EVIDENCE
    draft.save()
    # Text outside the spec.
    telegram.send(user_id, "Пришли фото или скриншот — его увидят все участники. Потом нажми, чем закончилось.")
    return None


def _on_old_button(user_id, message, question, _arg) -> str | None:
    """«Подтвердить» and «Изменить» from messages sent before phase 3d: show today's buttons instead."""
    trader = _creator_check(user_id, message, question)
    if trader is None:
        return None
    _drop_buttons(message)
    draft = _draft(user_id, question)
    picked = (_decode_multi(_subs(question), draft.outcome) if draft else None) or {}
    telegram.send(user_id, _creator_prompt(question, picked), reply_markup=outcome_keyboard(question, picked))
    return None


def _draft(user_id, question) -> ResolutionDraft | None:
    if question is None:
        return None
    return ResolutionDraft.objects.filter(trader__telegram_chat_id=user_id, question=question).first()


def _publish(user_id, trader: Trader, question: Question, outcome: str,
             picked: dict[int, str] | None = None) -> str | None:
    """State the creator's outcome with the draft's photo, if any; the row lock makes a double tap harmless."""
    with transaction.atomic():
        draft = ResolutionDraft.objects.select_for_update().filter(trader=trader, question=question).first()
        evidence, file_id, is_document, note = None, "", False, ""
        if draft is not None and draft.evidence:
            with draft.evidence.open("rb") as f:
                evidence = ContentFile(f.read(), name=os.path.basename(draft.evidence.name))
            file_id, is_document, note = draft.evidence_file_id, draft.evidence_is_document, draft.note
        try:
            question = services.propose_resolution(trader, question, outcome, submarket_outcomes=picked,
                                                   evidence=evidence, evidence_file_id=file_id, note=note)
        except services.MarketError as exc:
            error = str(exc)
        else:
            error = None
            notify_participants(question, is_document=is_document)
            if draft is not None:
                transaction.on_commit(lambda d=draft: d.evidence and d.evidence.delete(save=False))
                draft.delete()
    if error:
        telegram.send(user_id, error)
        return None
    _report_to_creator(user_id, question, photo=bool(file_id))
    return None


def _report_to_creator(user_id, question: Question, *, photo: bool = False) -> None:
    hours = settings.DISPUTE_WINDOW_HOURS
    markup = {"inline_keyboard": [[app_button("Посмотреть рынок", f"market/{question.pk}")]]}
    subs = _subs(question)
    with_photo = " вместе с фото" if photo else ""
    if question.proposed_outcome == "cancel":
        text = (f"Готово. Отмена рынка опубликована{with_photo}. У участников есть {hours} часа, чтобы оспорить её. "
                "Если спора не будет, всем вернутся монеты.")
    elif subs:
        picked = {s.pk: s.proposed_outcome for s in subs if s.proposed_outcome in ("yes", "no")}
        text = (f"Готово. Итоги опубликованы{with_photo}:\n{_multi_summary_html(subs, picked)}\n\n"
                f"У участников есть {hours} часа, чтобы оспорить их. Если спора не будет, выплаты придут "
                f"автоматически, а тебе — половина комиссии: <b>+{_fee_share(question)}</b>.")
    else:
        text = (f"Готово. Итог <b>{LABELS[question.proposed_outcome]}</b> опубликован{with_photo}. "
                f"У участников есть {hours} часа, чтобы оспорить его. Если спора не будет, выплаты придут "
                f"автоматически, а тебе — половина комиссии: <b>+{_fee_share(question)}</b>.")
    telegram.send(user_id, text, parse_mode="HTML", reply_markup=markup)


def notify_participants(question: Question, *, is_document: bool = False) -> int:
    """Queue the stated outcome (with its proof, if any) and a «Оспорить» button in the same transaction,
    for everyone who may dispute it: the participants, and the creator when a participant stated it."""
    from .bot_jobs import enqueue_message
    title = html.escape(question.title[:200])
    subs = _subs(question)
    by_participant = services.by_participant(question)
    picked = {s.pk: s.proposed_outcome for s in subs if s.proposed_outcome in ("yes", "no")}
    if question.proposed_outcome == "cancel":
        head = f"Создатель рынка «{title}» отменяет его."
    elif by_participant and subs:
        # Text outside the spec.
        head = f"Автор не указал итог рынка «{title}», и его предложил участник:\n{_multi_summary_html(subs, picked)}"
    elif by_participant:
        # Text outside the spec.
        head = (f"Автор не указал итог рынка «{title}», и его предложил участник: "
                f"<b>{LABELS[question.proposed_outcome]}</b>.")
    elif subs:
        head = f"Создатель рынка «{title}» указал итоги:\n{_multi_summary_html(subs, picked)}"
    else:
        head = f"Создатель рынка «{title}» указал итог: <b>{LABELS[question.proposed_outcome]}</b>."
    text = head
    if question.evidence_note:
        text += f"\n{html.escape(question.evidence_note[:300])}"
    text += (f"\nНе согласен — оспорь в течение {settings.DISPUTE_WINDOW_HOURS} часов. "
             f"Спор стоит {settings.DISPUTE_COST} монет.")
    markup = {"inline_keyboard": [[{"text": "Оспорить", "callback_data": f"disp:{question.pk}"}],
                                  [app_button("Открыть рынок", f"market/{question.pk}")]]}
    people = list(participants(question).exclude(pk=question.proposed_by_id))
    if by_participant and question.creator and question.creator.telegram_chat_id:
        people.append(question.creator)
    for trader in people:
        payload = {"parse_mode": "HTML", "reply_markup": markup}
        if question.evidence_file_id:
            method = "sendDocument" if is_document else "sendPhoto"
            payload.update({"document" if is_document else "photo": question.evidence_file_id,
                            "caption": text[:CAPTION_LIMIT]})
        else:
            method = "sendMessage"
            payload["text"] = text
        enqueue_message(f"outcome:{question.pk}:{question.proposed_at.isoformat()}:{trader.pk}",
                        trader, question, method, payload)
    return len(people)


# --- participants state the outcome (phase 3d) -----------------------------------------------------

def _taken(trader: Trader, question: Question) -> str | None:
    """The outcome is already stated: tell the participant who it was (same texts as the service)."""
    if not question.resolution_state and question.status in (Question.Status.OPEN, Question.Status.CLOSED):
        return None
    if question.proposed_by_id == trader.pk:
        return "Ты уже предложил итог этого рынка."
    if services.by_participant(question):
        return "Итог уже предложил другой участник. Не согласен — оспорь его."
    if question.proposed_by_id:
        return "Автор уже указал итог. Не согласен — оспорь его."
    return "Итог этого рынка уже указан."


def _participant_check(user_id, message, question) -> Trader | None:
    trader = _trader(user_id)
    if question is None:
        error = "Рынок не найден."
    elif trader is None:
        error = "Предложить итог может только участник рынка."
    else:
        error = _taken(trader, question)
    if error:
        _drop_buttons(message)
        telegram.send(user_id, error)
        return None
    return trader


def _participant_picks(question: Question, arg: str) -> tuple[str, dict[int, str] | None] | None:
    """`yes` / `no`, or a complete `yn…` code for a market with options."""
    subs = _subs(question)
    if not subs:
        return (arg, None) if arg in ("yes", "no") else None
    picked = picks_from_code(subs, arg)
    if picked is None or len(picked) < len(subs):
        return None
    return "yes", picked


def _on_participant_option(user_id, message, question, code) -> str | None:
    """`presopt:<id>:<code>` — a participant toggles the picks of a market with options."""
    if _participant_check(user_id, message, question) is None:
        return None
    if picks_from_code(_subs(question), code) is None:
        return None
    if message.get("message_id"):
        _quiet("editMessageText", chat_id=user_id, message_id=message["message_id"],
               text=participant_prompt(question, code), reply_markup=participant_keyboard(question, code))
    return None


def _on_participant_outcome(user_id, message, question, arg) -> str | None:
    """`pres:<id>:<yes|no|code>` — ask to confirm the bond before stating the outcome."""
    if _participant_check(user_id, message, question) is None:
        return None
    parsed = _participant_picks(question, arg)
    if parsed is None:
        return "Отметь ДА или НЕТ для каждого варианта."
    _, picked = parsed
    if picked is not None:
        summary = f"Итоги по вариантам:\n{_multi_summary_html(_subs(question), picked)}"
    else:
        summary = f"Итог: <b>{LABELS[arg]}</b>."
    bond = settings.PROPOSAL_BOND
    _drop_buttons(message)
    # Text outside the spec (the meaning is in the spec: a 20-coin bond, back when the outcome stands).
    telegram.send(user_id, f"Рынок «{html.escape(question.title)}»\n{summary}\n\n"
                           f"Предложить итог за {bond} монет — вернутся, если итог встанет. "
                           "Если итог оспорят и спор признают, залог сгорит.",
                  parse_mode="HTML",
                  reply_markup={"inline_keyboard": [[
                      {"text": f"Предложить за {bond}", "callback_data": f"presok:{question.pk}:{arg}"},
                      {"text": "Не надо", "callback_data": f"presno:{question.pk}"}]]})
    return None


def _on_participant_confirm(user_id, message, question, arg) -> str | None:
    """`presok:<id>:<yes|no|code>` — state the outcome and pay the bond; the first participant wins."""
    _drop_buttons(message)
    trader = _trader(user_id)
    if question is None or trader is None:
        telegram.send(user_id, "Предложить итог может только участник рынка.")
        return None
    parsed = _participant_picks(question, arg)
    if parsed is None:
        return None
    outcome, picked = parsed
    try:
        with transaction.atomic():
            question = services.propose_by_participant(trader, question, outcome, submarket_outcomes=picked)
            notify_participants(question)
    except services.MarketError as exc:
        telegram.send(user_id, str(exc))
        return None
    # Text outside the spec.
    telegram.send(user_id, f"Готово, итог предложен. Залог {settings.PROPOSAL_BOND} монет списан. "
                           f"У участников и автора есть {settings.DISPUTE_WINDOW_HOURS} часа, чтобы оспорить итог. "
                           "Если спора не будет, залог вернётся, а тебе — доля комиссии автора: "
                           f"<b>+{_fee_share(question)}</b>.",
                  parse_mode="HTML",
                  reply_markup={"inline_keyboard": [[app_button("Посмотреть рынок", f"market/{question.pk}")]]})
    return None


def _on_participant_decline(user_id, message, question, _arg) -> str | None:
    _drop_buttons(message)
    return "Хорошо, не предлагаем"


# --- proof for a disputed outcome (phase 3d) -------------------------------------------------------

def _awaiting_evidence(trader: Trader) -> Question | None:
    """The latest disputed outcome of this person that still waits for proof."""
    since = timezone.now() - timedelta(hours=settings.EVIDENCE_HOURS)
    return (Question.objects.select_related("group")
            .filter(parent__isnull=True, resolution_state=Question.Resolution.DISPUTED,
                    evidence_requested_at__gt=since)
            .filter(Q(proposed_by=trader) | Q(proposed_by__isnull=True, creator=trader))
            .order_by("-evidence_requested_at").first())


def _evidence(message: dict, trader: Trader, question: Question) -> bool:
    chat_id = message["chat"]["id"]
    file_id, _ = _image(message)
    text = message.get("text") or ""
    if not file_id and not LINK.search(text):
        if services.has_evidence(question):
            return False  # the proof is here already; this message is about something else
        # Text outside the spec.
        telegram.send(chat_id, f"Пришли фото, скриншот или ссылку — доказательство итога рынка «{question.title}».")
        return True
    content = None
    if file_id:
        content = _download(chat_id, file_id, str(question.pk))
        if content is None:
            return True
    try:
        question = services.attach_evidence(trader, question, evidence=content, evidence_file_id=file_id,
                                            note=message.get("caption") or text)
    except services.MarketError as exc:
        telegram.send(chat_id, str(exc))
        return True
    # Text outside the spec. The worker shows new proof to chat admins (chat_disputes.tick).
    telegram.send(chat_id, "Доказательство получено — "
                  + ("его увидят админы чата." if is_chat_market(question) else "его увидит модератор."))
    return True


# --- disputes ----------------------------------------------------------------------------------------

def _on_dispute(user_id, message, question, _arg) -> str | None:
    if question is None:
        return "Рынок не найден."
    who = "админы чата признают" if is_chat_market(question) else "модератор признает"
    telegram.send(user_id, f"Спор стоит {settings.DISPUTE_COST} монет; вернём их, если {who} спор. Оспорить?",
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
    who = "проверят админы чата" if is_chat_market(question) else "проверит модератор"
    telegram.send(user_id, f"Спор открыт, списано {settings.DISPUTE_COST} монет. Итог {who}.")
    return None


def _on_dispute_decline(user_id, message, question, _arg) -> str | None:
    _drop_buttons(message)
    return "Хорошо, не оспариваем"


HANDLERS = {
    "res": _on_outcome,
    "resopt": _on_option,
    "resnext": _on_next,
    "resphoto": _on_photo,
    "resok": _on_old_button,
    "resedit": _on_old_button,
    "resnone": _on_old_button,
    "pres": _on_participant_outcome,
    "presopt": _on_participant_option,
    "presok": _on_participant_confirm,
    "presno": _on_participant_decline,
    "disp": _on_dispute,
    "dispok": _on_dispute_confirm,
    "dispno": _on_dispute_decline,
}
