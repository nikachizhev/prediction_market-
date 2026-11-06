"""Bot messages around market resolution (spec E4, MVP bot). Texts follow docs/mockups/Bot.dc.html.

Phase 3d texts that are in neither the mockups nor the spec are marked «text outside the spec».
Resolution questions go straight to Telegram: they don't depend on notify levels or the quiet mode.
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db.models import Q, Sum
from django.utils import timezone

from . import services, telegram
from .models import LedgerEntry, Payout, Position, Question, Trader

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
    # Text outside the spec (phase 3d: a photo is optional).
    how = ("Отметишь ДА или НЕТ по каждому варианту — фото по желанию."
           if question.options or question.submarkets.exists()
           else "Нажмёшь ДА или НЕТ — фото по желанию.")
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
    """«Чем закончилось?»: one tap states the outcome; «Приложить фото» before it is optional (phase 3d)."""
    subs = list(question.submarkets.all().order_by("id"))
    photo = [{"text": "Приложить фото", "callback_data": f"resphoto:{question.pk}"}]
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
            rows.append([{"text": "Подтвердить итоги", "callback_data": f"resnext:{question.pk}"}])
        rows.append(photo)
        rows.append([{"text": "Отменить рынок", "callback_data": f"res:{question.pk}:cancel"}])
        return {"inline_keyboard": rows}
    return {"inline_keyboard": [
        [{"text": "ДА", "callback_data": f"res:{question.pk}:yes"}, {"text": "НЕТ", "callback_data": f"res:{question.pk}:no"}],
        photo,
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
    # Text outside the spec (phase 3d: participants may state the outcome too).
    telegram.send(creator.telegram_chat_id,
                  f"Напоминание: рынок «{question.title}» ждёт итог. Участников я тоже спросил: первый ответивший "
                  "предложит итог и получит долю комиссии вместо тебя. "
                  f"Если итог не укажет никто за {left} ч, рынок отменится и всем вернутся монеты.",
                  reply_markup=outcome_keyboard(question))


# --- participants state the outcome (phase 3d) ---------------------------------------------------

def picks_from_code(subs: list[Question], code: str) -> dict[int, str] | None:
    """`y?n` — one letter per option in id order; None when the code doesn't fit the market."""
    if not subs or len(code) != len(subs) or any(c not in "yn?" for c in code):
        return None
    return {s.pk: ("yes" if c == "y" else "no") for s, c in zip(subs, code) if c != "?"}


def participant_keyboard(question: Question, code: str | None = None) -> dict:
    """ДА / НЕТ; for options — a toggle per option, the picks live in the callback data itself."""
    subs = list(question.submarkets.all().order_by("id"))
    if not subs:
        return {"inline_keyboard": [[{"text": "ДА", "callback_data": f"pres:{question.pk}:yes"},
                                     {"text": "НЕТ", "callback_data": f"pres:{question.pk}:no"}]]}
    code = code if code and picks_from_code(subs, code) is not None else "?" * len(subs)
    rows = []
    for idx, sub in enumerate(subs):
        label = (sub.option_label or sub.title)[:24]
        yes, no = (code[:idx] + c + code[idx + 1:] for c in "yn")
        rows.append([
            {"text": f"{'✅ ' if code[idx] == 'y' else ''}ДА · {label}", "callback_data": f"presopt:{question.pk}:{yes}"},
            {"text": f"{'❌ ' if code[idx] == 'n' else ''}НЕТ · {label}", "callback_data": f"presopt:{question.pk}:{no}"},
        ])
    if "?" not in code:
        rows.append([{"text": "Продолжить →", "callback_data": f"pres:{question.pk}:{code}"}])
    return {"inline_keyboard": rows}


def participant_prompt(question: Question, code: str | None = None) -> str:
    subs = list(question.submarkets.all().order_by("id"))
    text = f"Рынок «{question.title}»\nАвтор не указал итог. Знаешь, чем закончилось?"
    if subs:
        picked = picks_from_code(subs, code or "") or {}
        text += f"\nОтметь ДА или НЕТ для каждого варианта:\n\n{multi_prompt_lines(subs, picked)}"
    return text


def participants(question: Question):
    """Everyone with Telegram who traded in the market or its options, except the creator."""
    return (Trader.objects.filter(Q(trades__question=question) | Q(trades__question__parent=question),
                                  telegram_chat_id__isnull=False)
            .exclude(pk=question.creator_id).distinct())


def ask_participants(question: Question) -> int:
    """24 hours of silence: «Автор не указал итог. Знаешь, чем закончилось?» queued for every participant."""
    from .bot_jobs import enqueue_message
    text, markup = participant_prompt(question), participant_keyboard(question)
    people = list(participants(question))
    for trader in people:
        enqueue_message(f"ask-participants:{question.pk}:{trader.pk}", trader, question, "sendMessage",
                        {"text": text, "reply_markup": markup})
    return len(people)


# --- proof for a disputed outcome (phase 3d) -----------------------------------------------------

def is_chat_market(question: Question) -> bool:
    return bool(question.group_id and question.group.is_chat)


def _outcome_author(question: Question) -> Trader | None:
    return question.proposed_by or question.creator


def _market_markup(question: Question) -> dict:
    return {"inline_keyboard": [[app_button("Открыть рынок", f"market/{question.pk}")]]}


def request_evidence(question: Question) -> None:
    """The first dispute: queue a request to whoever stated the outcome, in the dispute's own transaction."""
    from .bot_jobs import enqueue_message
    author = _outcome_author(question)
    if author is None or not author.telegram_chat_id:
        return
    who = "админы чата" if is_chat_market(question) else "модератор"
    # Text outside the spec (the spec gives the meaning: a photo, a screenshot or a link within 24 hours).
    text = (f"Итог рынка «{question.title}» оспорили. Пришли доказательство — фото, скриншот или ссылку "
            f"сообщением сюда. На это {settings.EVIDENCE_HOURS} часа. Без доказательства спор всё равно "
            f"решат {who}, но это будет доводом против итога.")
    enqueue_message(f"evidence-request:{question.pk}:{question.proposed_at.isoformat()}", author, question,
                    "sendMessage", {"text": text, "reply_markup": _market_markup(question)})


def remind_evidence(question: Question) -> None:
    """The only reminder, halfway through EVIDENCE_HOURS."""
    author = _outcome_author(question)
    if author is None or not author.telegram_chat_id:
        return
    deadline = services.evidence_deadline(question)
    left = max(1, int((deadline - timezone.now()).total_seconds() // 3600)) if deadline else settings.EVIDENCE_HOURS
    # Text outside the spec.
    telegram.send(author.telegram_chat_id,
                  f"Напоминание: итог рынка «{question.title}» оспорили, а доказательства пока нет. "
                  f"Пришли фото, скриншот или ссылку — осталось {left} ч.",
                  reply_markup=_market_markup(question))


def participant_outcome_decided(question_id: int, decision: str, upheld: bool) -> None:
    """A dispute over a participant's outcome is decided: the creator learns they keep the subsidy."""
    from .bot_notifications import _chat_dispute_stage, enqueue, market_button
    question = Question.objects.select_related("creator", "group").get(pk=question_id)
    if not question.creator:
        return
    if decision == "cancel":
        verdict = "рынок отменён"
    elif question.submarkets.exists():
        verdict = "итоги по вариантам обновлены" if upheld else "итоги по вариантам подтверждены"
    else:
        verdict = f"итог {'ДА' if decision == 'yes' else 'НЕТ'}"
    # Text outside the spec.
    if upheld:
        text = (f"«{question.title}» — спор признан: {verdict}. Итог предлагал участник, "
                "монеты за создание рынка возвращаются как обычно.")
    else:
        text = f"«{question.title}» — спор отклонён: {verdict}, как предложил участник."
    title = {"moderator": "Решение модератора:", "admins": "Решение админов чата:",
             "team": "Решение команды проекта:"}[_chat_dispute_stage(question)]
    enqueue(question.creator, "creator", f"moderator:{question.pk}", title, text,
            question=question, payload={"reply_markup": market_button(question)})


def _proposer_result(root: Question, markup: dict) -> None:
    """A participant who stated the outcome learns about the bond and the creator's share of fees."""
    from .bot_notifications import coins, enqueue
    if not services.by_participant(root):
        return
    proposer = root.proposed_by
    entries = LedgerEntry.objects.filter(trader=proposer, question=root)
    refunded = entries.filter(kind=LedgerEntry.Kind.PROPOSAL_BOND_REFUND).exists()
    fee = entries.filter(kind=LedgerEntry.Kind.PROPOSER_FEE).aggregate(t=Sum("amount"))["t"] or Decimal(0)
    bond = coins(root.proposal_bond)
    # Text outside the spec.
    if refunded and root.status == Question.Status.ANNULLED:
        title, text = "Рынок отменён:", f"«{root.title}». Залог за итог +{bond} вернулся."
    elif refunded:
        title = "Твой итог встал:"
        text = f"«{root.title}». Залог +{bond} вернулся" + (f", доля комиссии +{coins(fee)}." if fee > 0 else ".")
    else:
        title, text = "Твой итог не подтвердился:", f"«{root.title}» — спор признан, залог {bond} не возвращается."
    # "creator" — a resolution step: not muted by the quiet mode or a market's notify level.
    enqueue(proposer, "creator", f"proposer-result:{root.pk}", title, text, question=root,
            payload={"reply_markup": markup})


def send_results(question: Question) -> None:
    """Result and payout for every participant through the notification outbox; confetti for winners.

    Keyed per market and trader, so a repeated call never sends twice.
    """
    from .bot_notifications import coins, enqueue
    root = question.parent or question
    subs = list(root.submarkets.all().order_by("id"))
    family_ids = [root.pk, *(s.pk for s in subs)]
    markup = {"inline_keyboard": [[app_button("Открыть рынок", f"market/{root.pk}")]]}
    _proposer_result(root, markup)
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
