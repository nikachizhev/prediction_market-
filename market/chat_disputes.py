"""Disputes on chat markets decided by the chat's admins (spec E8.6).

The worker (`tick`) opens a `ChatDisputeReview` for every disputed chat market and sends it to the chat's
admins in private: not bots, not the market's creator, only people with an account and no trades in the
market. The first decision is final and goes through `services.moderator_decide`. No eligible or reachable
admin, or no decision within CHAT_DISPUTE_HOURS -> `escalated_at` is set and the project team decides in
the admin site (`team_decide`). The bot never says who disputed or who bet what.
"""
from __future__ import annotations

import html
import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import chats, services, telegram
from .bot_resolution import app_button
from .models import ChatDisputeReview, Dispute, Position, Question, Trade, Trader
from .telegram import TelegramError

log = logging.getLogger(__name__)

CALLBACK_PREFIX = "cdis:"
LABELS = {"yes": "ДА", "no": "НЕТ", "cancel": "отмена рынка"}
DECISIONS = ("yes", "no", "cancel")
CRITERIA_LIMIT = 1500
COMMENT_LIMIT = 300
COMMENTS_SHOWN = 5
CAPTION_LIMIT = 1024


def owns(data: str) -> bool:
    return data.startswith(CALLBACK_PREFIX)


# --- texts -----------------------------------------------------------------------------------------

def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def verdict(decision: str, proposed: str) -> str:
    if decision == proposed:
        return f"итог создателя верный ({LABELS[decision]})"
    if decision == "cancel":
        return "итог неверный, рынок отменён"
    return f"итог неверный, правильный — {LABELS[decision]}"


def card(review: ChatDisputeReview, *, photo: bool = False) -> str:
    """What an admin sees: the market, its rules, the creator's outcome and proof, the disputers' comments."""
    question = review.question
    lines = [f"<b>Спор в чате «{html.escape(review.group.name)}»</b>",
             "Итог рынка оспорили. Ты админ чата и не делал прогнозов в этом рынке — реши, кто прав.",
             "",
             f"Рынок: <b>{html.escape(question.title)}</b>",
             f"Как определяем результат: {html.escape(question.resolution_criteria[:CRITERIA_LIMIT])}",
             ""]
    if question.proposed_outcome == "cancel":
        lines.append("Создатель отменяет рынок.")
    else:
        lines.append(f"Итог создателя: <b>{LABELS.get(question.proposed_outcome, '—')}</b>")
    if question.evidence_note:
        lines.append(f"Пояснение создателя: {html.escape(question.evidence_note)}")
    if photo:
        lines.append("Доказательство создателя — выше.")
    elif not question.evidence_file_id:
        lines.append("Доказательства создатель не приложил.")
    disputes = list(Dispute.objects.filter(question=question).order_by("created_at", "pk"))
    n = len(disputes)
    lines.append("")
    lines.append(f"Оспорили: {n} {_plural(n, 'участник', 'участника', 'участников')}.")
    comments = [d.reason.strip() for d in disputes if d.reason.strip()][:COMMENTS_SHOWN]
    if comments:
        lines.append("Комментарии:" if len(comments) > 1 else "Комментарий:")
        lines.extend(f"«{html.escape(c[:COMMENT_LIMIT])}»" for c in comments)
    else:
        lines.append("Комментариев нет.")
    lines.append("")
    lines.append(f"Первое решение админа окончательное. Если за {settings.CHAT_DISPUTE_HOURS} часов никто не решит, "
                 "спор перейдёт команде проекта.")
    return "\n".join(lines)


def _decided_line(review: ChatDisputeReview, trader_id: int | None) -> str:
    text = verdict(review.decision, review.question.proposed_outcome)
    if review.decided_by_id and review.decided_by_id == trader_id:
        return f"Ты решил: {text}. Решение окончательное."
    if review.decided_by is None:
        return f"Спор решила команда проекта: {text}."
    return f"Спор решён. {review.decided_by.display_name}: {text}."


TIMEOUT_LINE = "Время вышло — спор решит команда проекта."


def _status_note(review: ChatDisputeReview, trader_id: int | None = None) -> str | None:
    """Why a button no longer works: already decided or handed to the project team."""
    if review.decided_at:
        return _decided_line(review, trader_id)
    if review.escalated_at:
        return "Спор уже у команды проекта."
    return None


# --- keyboards -------------------------------------------------------------------------------------

def _market_row(review: ChatDisputeReview) -> list:
    return [app_button("Открыть рынок", f"market/{review.question_id}")]


def _first_keyboard(review: ChatDisputeReview) -> dict:
    data = f"{CALLBACK_PREFIX}{review.pk}"
    return {"inline_keyboard": [[{"text": "Итог верный", "callback_data": f"{data}:ok"},
                                 {"text": "Итог неверный", "callback_data": f"{data}:wrong"}],
                                _market_row(review)]}


def _wrong_keyboard(review: ChatDisputeReview) -> dict:
    data = f"{CALLBACK_PREFIX}{review.pk}"
    labels = {"yes": "ДА", "no": "НЕТ", "cancel": "Отменить рынок"}
    options = [{"text": labels[d], "callback_data": f"{data}:{d}"}
               for d in DECISIONS if d != review.question.proposed_outcome]
    return {"inline_keyboard": [options, [{"text": "Назад", "callback_data": f"{data}:back"}]]}


def _done_keyboard(review: ChatDisputeReview) -> dict:
    return {"inline_keyboard": [_market_row(review)]}


# --- Telegram calls --------------------------------------------------------------------------------

def _quiet(method: str, **data) -> None:
    try:
        telegram.api(method, **data)
    except TelegramError:
        log.warning("Telegram %s failed", method)


def _send_evidence(chat_id: int, question: Question) -> bool:
    """The creator's photo; an image sent as a file is only accepted by sendDocument."""
    caption = f"Доказательство создателя рынка «{html.escape(question.title)}»"[:CAPTION_LIMIT]
    for method, field in (("sendPhoto", "photo"), ("sendDocument", "document")):
        try:
            telegram.api(method, chat_id=chat_id, caption=caption, parse_mode="HTML",
                         **{field: question.evidence_file_id})
            return True
        except TelegramError:
            continue
    return False


def _edit_all(review: ChatDisputeReview, line_for) -> None:
    """Replace every admin's card with the final state; `line_for(trader_id)` gives the status line."""
    for sent in review.messages:
        text = card(review, photo=bool(sent.get("photo"))) + "\n\n" + line_for(sent.get("trader"))
        _quiet("editMessageText", chat_id=sent["chat_id"], message_id=sent["message_id"], text=text,
               parse_mode="HTML", reply_markup=_done_keyboard(review))


# --- who decides -----------------------------------------------------------------------------------

def _has_stake(trader: Trader, question: Question) -> bool:
    return (Trade.objects.filter(trader=trader, question=question).exists()
            or Position.objects.filter(trader=trader, question=question).exists())


def eligible_admins(review: ChatDisputeReview) -> list[Trader]:
    """Chat admins who may decide. Raises TelegramError when Telegram doesn't answer."""
    question = review.question
    ids = []
    for member in chats.admins(review.group):
        user = member.get("user") or {}
        if user.get("is_bot") or user.get("id") is None:
            continue
        ids.append(int(user["id"]))
    people = Trader.objects.filter(telegram_chat_id__in=ids).exclude(pk=question.creator_id).order_by("pk")
    return [t for t in people if not _has_stake(t, question)]


def _decide_error(review: ChatDisputeReview, trader: Trader | None, user_id: int) -> str | None:
    """Checked again on every decision: the admin list may have changed since the card was sent."""
    question = review.question
    if trader is None:
        return "Решать этот спор могут только админы чата."
    if trader.pk == question.creator_id:
        return "Создатель рынка не решает спор по своему рынку."
    if _has_stake(trader, question):
        return "У тебя есть прогнозы в этом рынке — спор решат другие админы."
    if not chats.is_admin(review.group, user_id):
        return "Ты больше не админ этого чата — спор решат другие админы."
    return None


# --- worker ----------------------------------------------------------------------------------------

def _open_review(question: Question, now) -> ChatDisputeReview | None:
    try:
        with transaction.atomic():
            question = Question.objects.select_for_update().get(pk=question.pk)
            if (question.resolution_state != Question.Resolution.DISPUTED
                    or ChatDisputeReview.objects.filter(question=question).exists()):
                return None
            return ChatDisputeReview.objects.create(
                question=question, group_id=question.group_id,
                deadline=now + timedelta(hours=settings.CHAT_DISPUTE_HOURS))
    except IntegrityError:
        return None  # another worker opened it first


def _escalate(review: ChatDisputeReview, now) -> bool:
    with transaction.atomic():
        locked = ChatDisputeReview.objects.select_for_update().get(pk=review.pk)
        if locked.decided_at or locked.escalated_at:
            return False
        locked.escalated_at = now
        locked.save(update_fields=["escalated_at"])
    review.escalated_at = now
    return True


def _send_to_admins(review: ChatDisputeReview) -> list[dict]:
    try:
        people = eligible_admins(review)
    except (TelegramError, KeyError, TypeError, ValueError):
        log.warning("getChatAdministrators failed for chat %s", review.group_id)
        return []
    sent = []
    for trader in people:
        chat_id = trader.telegram_chat_id
        photo = bool(review.question.evidence_file_id) and _send_evidence(chat_id, review.question)
        try:
            message = telegram.api("sendMessage", chat_id=chat_id, text=card(review, photo=photo),
                                   parse_mode="HTML", reply_markup=_first_keyboard(review))
        except TelegramError:
            # The bot can write only to people who started it.
            log.info("chat dispute %s: admin %s is unreachable", review.pk, trader.pk)
            continue
        sent.append({"trader": trader.pk, "chat_id": chat_id, "message_id": message.get("message_id"),
                     "photo": photo})
    return sent


def tick(now) -> dict:
    """Worker step: send new chat disputes to admins, hand silent ones to the project team after 48 h."""
    done = {"sent": 0, "escalated": 0}
    disputed = Question.objects.filter(group__kind="chat", resolution_state=Question.Resolution.DISPUTED,
                                       chat_review__isnull=True)
    for question in disputed:
        review = _open_review(question, now)
        if review is None:
            continue
        review = ChatDisputeReview.objects.select_related("question", "group").get(pk=review.pk)
        sent = _send_to_admins(review)
        if sent:
            ChatDisputeReview.objects.filter(pk=review.pk).update(messages=sent)
            done["sent"] += 1
        elif _escalate(review, now):
            done["escalated"] += 1

    overdue = (ChatDisputeReview.objects.select_related("question", "group")
               .filter(decided_at__isnull=True, escalated_at__isnull=True, deadline__lte=now,
                       question__resolution_state=Question.Resolution.DISPUTED))
    for review in overdue:
        if _escalate(review, now):
            done["escalated"] += 1
            _edit_all(review, lambda _trader: TIMEOUT_LINE)
    return done


# --- buttons ---------------------------------------------------------------------------------------

def _answer(callback: dict, text: str | None = None) -> None:
    _quiet("answerCallbackQuery", callback_query_id=callback.get("id"), **({"text": text[:200]} if text else {}))


def handle_callback(callback: dict) -> None:
    """`cdis:<review_id>:<ok|wrong|back|yes|no|cancel>` from an admin's private chat."""
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    user_id = (callback.get("from") or {}).get("id")
    if chat.get("type") != "private" or user_id is None or chat.get("id") != user_id:
        _answer(callback)
        return
    review_id, _, action = (callback.get("data") or "")[len(CALLBACK_PREFIX):].partition(":")
    try:
        review = (ChatDisputeReview.objects.select_related("question", "group", "decided_by")
                  .filter(pk=int(review_id)).first())
    except ValueError:
        review = None
    if review is None:
        _answer(callback, "Спор не найден.")
        return
    trader = Trader.objects.filter(telegram_chat_id=user_id).first()
    note = _status_note(review, trader.pk if trader else None)
    if note:
        _answer(callback, note)
        return
    place = {"chat_id": chat["id"], "message_id": message.get("message_id")}
    if action == "wrong":
        _quiet("editMessageReplyMarkup", **place, reply_markup=_wrong_keyboard(review))
        _answer(callback, "Какой итог правильный?")
        return
    if action == "back":
        _quiet("editMessageReplyMarkup", **place, reply_markup=_first_keyboard(review))
        _answer(callback)
        return
    if action != "ok" and action not in DECISIONS:
        _answer(callback)
        return
    error = _decide_error(review, trader, user_id)
    if error:
        _answer(callback, error)
        return
    decision = review.question.proposed_outcome if action == "ok" else action
    with transaction.atomic():
        review = ChatDisputeReview.objects.select_for_update().select_related("decided_by").get(pk=review.pk)
        note = _status_note(review, trader.pk)
        if note is None:
            try:
                services.moderator_decide(review.question, decision)
            except services.MarketError as exc:
                note = str(exc)
            else:
                review.decided_by, review.decision, review.decided_at = trader, decision, timezone.now()
                review.save(update_fields=["decided_by", "decision", "decided_at"])
    if note:
        _answer(callback, note)
        return
    review = ChatDisputeReview.objects.select_related("question", "group", "decided_by").get(pk=review.pk)
    _edit_all(review, lambda trader_id: _decided_line(review, trader_id))
    _answer(callback, "Решение принято")


# --- the project team ------------------------------------------------------------------------------

def team_decide(question: Question, decision: str) -> Question:
    """The admin site's «Решение по спору». A chat market reaches the team only after escalation."""
    if not (question.group_id and question.group.is_chat):
        return services.moderator_decide(question, decision)
    with transaction.atomic():
        review = ChatDisputeReview.objects.select_for_update().filter(question=question).first()
        if review is None or not (review.escalated_at or review.decided_at):
            raise services.MarketError("Спор в рынке чата сначала решают админы чата — команде он придёт, "
                                       f"если за {settings.CHAT_DISPUTE_HOURS} часов они не решат.")
        question = services.moderator_decide(question, decision)
        review.decision, review.decided_at = decision, timezone.now()
        review.save(update_fields=["decision", "decided_at"])
    return question
