"""1-on-1 challenges in the bot (spec E11): accepted, «Чем закончилось?», the rival's answer, proof, the end.

Messages go through the notification outbox (kind `challenge`): no switch, the quiet mode or the daily cap
loses them. Money moves only through `services`. Texts are from docs/mockups/challenge/Bot.dc.html.
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.db.models import Q
from django.utils import timezone

from . import services, telegram
from .bot_notifications import coins, enqueue
from .bot_resolution import app_button, day_month
from .events import log_event
from .models import Challenge, Trader

log = logging.getLogger(__name__)

CALLBACK_PREFIX = "chal:"
LABELS = {"yes": "ДА", "no": "НЕТ", "cancel": "Отмена"}


def owns(data: str) -> bool:
    return data.startswith(CALLBACK_PREFIX)


def _open_button(challenge: Challenge) -> dict:
    return app_button("Открыть вызов", f"challenge/{challenge.code}")


def _rival_name(challenge: Challenge, trader: Trader) -> str:
    rival = challenge.opponent if trader.pk == challenge.author_id else challenge.author
    return rival.display_name if rival else "соперник"


def _people(challenge: Challenge) -> list[Trader]:
    return [t for t in (challenge.author, challenge.opponent) if t is not None]


def _load(pk: int) -> Challenge:
    return Challenge.objects.select_related("author", "opponent", "winner").get(pk=pk)


def _answer_buttons(challenge: Challenge, rival_answer: str = "") -> list[list[dict]]:
    """ДА, НЕТ, Отменить; a rival's answer is free to repeat, any other opens a dispute."""
    def label(key: str, text: str) -> str:
        return f"{text} за {settings.DISPUTE_COST}" if rival_answer and key != rival_answer else text
    return [[{"text": label(key, text), "callback_data": f"{CALLBACK_PREFIX}{challenge.code}:{key}"}
             for key, text in (("yes", "ДА"), ("no", "НЕТ"), ("cancel", "Отменить"))]]


def challenge_accepted(pk: int) -> None:
    c = _load(pk)
    enqueue(c.author, "challenge", f"challenge-accepted:{c.pk}", f"{c.opponent.display_name} принял твой вызов:",
            f"«{c.title}»\nСтавка каждого {coins(c.stake)}. Итог спрошу {day_month(c.resolves_at)}.",
            payload={"reply_markup": {"inline_keyboard": [[_open_button(c)]]}})


def ask_both(c: Challenge) -> None:
    for trader in _people(c):
        enqueue(trader, "challenge", f"challenge-ask:{c.pk}:{trader.pk}",
                f"Пора решить вызов с {_rival_name(c, trader)}:", f"«{c.title}»\nЧем закончилось?",
                payload={"reply_markup": {"inline_keyboard": _answer_buttons(c) + [[_open_button(c)]]}})


def challenge_rival_answered(pk: int, answered_by: int) -> None:
    c = _load(pk)
    rival = c.opponent if answered_by == c.author_id else c.author
    answer = c.author_answer if answered_by == c.author_id else c.opponent_answer
    label = LABELS[answer]
    enqueue(rival, "challenge", f"challenge-rival:{c.pk}", f"Соперник указал: {label}",
            f"«{c.title}»\nЕсли согласен, нажми {label}. Не ответишь за {settings.CHALLENGE_ANSWER_HOURS} часа — "
            f"его ответ встанет. Другой ответ откроет спор за {settings.DISPUTE_COST} монет.",
            payload={"reply_markup": {"inline_keyboard": _answer_buttons(c, answer) + [[_open_button(c)]]}})


def challenge_disputed(pk: int) -> None:
    c = _load(pk)
    for trader in _people(c):
        enqueue(trader, "challenge", f"challenge-disputed:{c.pk}:{trader.pk}",
                f"Спор по вызову с {_rival_name(c, trader)}:",
                f"«{c.title}»\nВы ответили по-разному. Пришли доказательство — фото, скриншот или ссылку "
                f"сообщением сюда. На это {settings.EVIDENCE_HOURS} часа.",
                payload={"reply_markup": {"inline_keyboard": [[_open_button(c)]]}})


def remind_proof(c: Challenge) -> None:
    for trader in _people(c):
        side = "author" if trader.pk == c.author_id else "opponent"
        if getattr(c, f"{side}_proof") or getattr(c, f"{side}_proof_note"):
            continue
        enqueue(trader, "challenge", f"challenge-proof:{c.pk}:{trader.pk}", "Доказательство по вызову:",
                f"«{c.title}»\nПришли его сообщением сюда. Без него модератор всё равно решит спор, "
                "но это будет доводом против тебя.")


def challenge_closed(pk: int) -> None:
    c = _load(pk)
    for trader in _people(c):
        key = f"challenge-closed:{c.pk}:{trader.pk}"
        markup = {"reply_markup": {"inline_keyboard": [[_open_button(c)]]}}
        if c.closed_reason == "rejected":
            title, text = "Вызов снят:", f"«{c.title}» нарушает правила. Твои монеты вернулись."
            markup = None
        elif c.status == Challenge.Status.CANCELLED:
            if c.closed_reason == "expired":
                title, text = "Вызов никто не принял:", f"«{c.title}». Ставка {coins(c.stake)} вернулась."
            else:
                title, text = "Вызов отменён:", f"«{c.title}». Ставка {coins(c.stake)} вернулась."
        elif c.winner_id == trader.pk:
            title, text = f"Ты выиграл вызов: +{coins(c.payout)}", f"«{c.title}»"
        else:
            title, text = "Ты проиграл вызов:", f"«{c.title}». Итог: {LABELS[c.outcome]}."
        enqueue(trader, "challenge", key, title, text, payload=markup)


def tick(now=None) -> dict:
    """What the worker does every minute; a failed step is retried next time."""
    now = now or timezone.now()
    done = {"expired": 0, "asked": 0, "timeout": 0, "silent": 0, "proof_reminded": 0}
    for c in Challenge.objects.filter(status=Challenge.Status.OPEN, expires_at__lte=now):
        try:
            services.expire_challenge(c)
            done["expired"] += 1
        except services.MarketError:
            continue
    active = Challenge.objects.filter(status=Challenge.Status.ACTIVE).select_related("author", "opponent")
    for c in active.filter(asked_at__isnull=True, resolves_at__lte=now):
        ask_both(c)
        Challenge.objects.filter(pk=c.pk).update(asked_at=now)
        done["asked"] += 1
    for c in active.filter(answer_until__lte=now):
        try:
            services.settle_unanswered_challenge(c)
            done["timeout"] += 1
        except services.MarketError:
            continue
    silence = timedelta(hours=settings.CHALLENGE_SILENCE_HOURS)
    for c in active.filter(resolves_at__lte=now - silence, author_answer="", opponent_answer=""):
        try:
            services.cancel_silent_challenge(c)
            done["silent"] += 1
        except services.MarketError:
            continue
    half = timedelta(hours=settings.EVIDENCE_HOURS) / 2
    for c in (Challenge.objects.filter(status=Challenge.Status.DISPUTED, proof_until__gt=now,
                                       proof_until__lte=now + half, proof_reminded_at__isnull=True)
              .select_related("author", "opponent")):
        remind_proof(c)
        Challenge.objects.filter(pk=c.pk).update(proof_reminded_at=now)
        done["proof_reminded"] += 1
    return done


def handle_callback(callback: dict) -> None:
    data = callback.get("data") or ""
    user_id = (callback.get("from") or {}).get("id")
    answer = {"callback_query_id": callback.get("id")}
    try:
        telegram.api("answerCallbackQuery", **answer)
    except telegram.TelegramError:
        log.warning("Callback for a challenge was not acknowledged")
    _, code, choice = (data.split(":") + ["", ""])[:3]
    trader = Trader.objects.filter(telegram_chat_id=user_id).first()
    challenge = Challenge.objects.filter(code=code).first()
    if trader is None or challenge is None or trader.pk not in (challenge.author_id, challenge.opponent_id):
        telegram.send(user_id, "Кнопка устарела.")
        return
    try:
        challenge = services.answer_challenge(trader, challenge, choice)
    except services.MarketError as exc:
        telegram.send(user_id, str(exc))
        return
    message = callback.get("message") or {}
    try:
        telegram.api("editMessageReplyMarkup", chat_id=user_id, message_id=message.get("message_id"),
                     reply_markup={"inline_keyboard": [[_open_button(challenge)]]})
    except telegram.TelegramError:
        log.info("Buttons of a challenge message stayed")
    log_event(trader, "challenge_answer", {"status": challenge.status})
    if challenge.status == Challenge.Status.ACTIVE:
        telegram.send(user_id, "Ответ принят. Ждём соперника: если он не ответит за "
                               f"{settings.CHALLENGE_ANSWER_HOURS} часа, твой ответ встанет.")


def _awaiting_proof(trader: Trader) -> Challenge | None:
    return (Challenge.objects.filter(Q(author=trader) | Q(opponent=trader), status=Challenge.Status.DISPUTED,
                                     proof_until__gt=timezone.now()).order_by("-proof_until").first())


def handle_message(message: dict) -> bool:
    """Proof for a disputed challenge. Returns True if the message was that."""
    from .bot_resolution_dialog import _image
    user_id = (message.get("from") or {}).get("id")
    text = (message.get("text") or message.get("caption") or "").strip()
    if text.startswith("/"):
        return False
    trader = Trader.objects.filter(telegram_chat_id=user_id).first()
    challenge = _awaiting_proof(trader) if trader else None
    if challenge is None:
        return False
    file_id, _ = _image(message)
    proof = None
    if file_id:
        try:
            content, path = telegram.download_file(file_id)
        except telegram.TelegramError:
            telegram.send(user_id, "Не удалось получить фото. Пришли его ещё раз.")
            return True
        proof = ContentFile(content, name=f"{challenge.code}{os.path.splitext(path)[1].lower() or '.jpg'}")
    if proof is None and not text:
        return False
    try:
        services.attach_challenge_proof(trader, challenge, proof=proof, note=text)
    except services.MarketError as exc:
        telegram.send(user_id, str(exc))
        return True
    telegram.send(user_id, "Доказательство получено. Его увидит модератор.")
    return True
