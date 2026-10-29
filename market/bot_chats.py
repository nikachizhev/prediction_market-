"""The bot in closed chats (spec E8.1, E8.4, E8.5): joining a chat, membership updates, commands,
admin settings and the chat outbox (`ChatPost`).

Privacy mode stays on: in a chat the bot sees only commands, service messages and replies to itself.
Buttons in groups are plain URLs (Telegram has no web_app buttons there). Texts never say who bet how:
only the question, the probability, the number of participants and the outcome.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from . import chats, rating, telegram
from .bot_notifications import MSK, _day_bounds, _quiet_until, coins, plural
from .models import CHAT_POST_KINDS, ChatPost, Group, GroupMembership, Question, Trade, Trader
from .share import market_link
from .telegram import TelegramError

log = logging.getLogger(__name__)

CALLBACK_PREFIX = "chat:"
GROUP_TYPES = ("group", "supergroup")
COMMANDS = ("markets", "top", "settings")
# Own creation price buttons in /settings (spec E5, decisions of phase 3b); clipped to CHAT_CREATION_COST_MIN..MAX.
COST_CHOICES = (30, 40, 50, 70, 100, 150, 200)
MARKETS_SHOWN = 10
TOP_SHOWN = 10
WEEKLY_TOP = 3
MAX_ATTEMPTS = 3
LEASE = timedelta(minutes=5)

OPEN_CHAT_BUTTON = "Открыть рынки чата"
WELCOME = ("Привет! Теперь у чата есть свои рынки прогнозов на виртуальные монеты. Их видят только участники "
           "чата: создавайте рынки, делайте прогнозы и соревнуйтесь в рейтинге чата.\n"
           "/markets — открытые рынки, /top — рейтинг чата, /settings — настройки для админов.")
REVIVED = "Я снова администратор — рынки чата работают, можно создавать новые."
NOT_ADMIN = ("Чтобы у чата появились свои рынки, сделай меня администратором — особые права не нужны. "
             "Только админам Telegram сообщает, кто в чате и кто из него вышел: без этого я не смогу "
             "пускать на рынки чата только своих.")
FROZEN = "Пока я не администратор, новые рынки в чате создать нельзя. Открытые доживут до итога."
UNKNOWN_CHAT = "У этого чата пока нет своих рынков: сначала сделай меня администратором чата."
SETTINGS_ADMINS_ONLY = "Настройки бота в чате меняют только админы."
ANONYMOUS = ("Ты пишешь анонимно, от имени чата, — так я не могу проверить, что ты админ. "
             "Напиши /settings от своего имени.")
ALERT_ADMINS_ONLY = "Настройки меняют только админы чата."
SAVED = "Сохранено"
STALE = "Эти настройки устарели. Набери /settings ещё раз."
NO_SUCH = "Такой настройки нет."
PRICE_HINT = ("Ставка размером с цену создания сдвигает вероятность с 50% до 75%: "
              "чем дешевле рынок, тем легче он двигается.")


def owns(data: str) -> bool:
    return data.startswith(CALLBACK_PREFIX)


# --- helpers ------------------------------------------------------------------------------------

def _open_chat_markup(group: Group) -> dict:
    return {"inline_keyboard": [[{"text": OPEN_CHAT_BUTTON, "url": chats.link(group)}]]}


def _market_markup(question: Question) -> dict:
    return {"inline_keyboard": [[{"text": "Открыть рынок", "url": market_link(None, question)}]]}


def _participants(question: Question) -> int:
    return Trade.objects.filter(question=question).values("trader").distinct().count()


def _people(n: int) -> str:
    return f"{n} {plural(n, 'участник', 'участника', 'участников')}"


def _percent(question: Question) -> int:
    return round(question.prob * 100)


def _signed(value: str) -> str:
    amount = Decimal(value)
    return f"+{coins(amount)}" if amount > 0 else coins(amount)


def _say(chat_id: int, text: str, *, thread: int | None = None, markup: dict | None = None) -> dict | None:
    """A direct answer outside the outbox (greeting, command replies): not counted in the daily cap."""
    extra = {}
    if thread:
        extra["message_thread_id"] = thread
    if markup:
        extra["reply_markup"] = markup
    try:
        return telegram.api("sendMessage", chat_id=chat_id, text=text, **extra)
    except TelegramError:
        log.warning("message to chat %s failed", chat_id, exc_info=True)
        return None


def _chat_of(chat_id: int) -> Group | None:
    return Group.objects.filter(kind="chat", telegram_chat_id=chat_id).first()


def _trader(user: dict | None) -> Trader | None:
    if not user or user.get("is_bot") or not user.get("id"):
        return None
    return Trader.objects.filter(telegram_chat_id=user["id"]).first()


# --- updates ------------------------------------------------------------------------------------

def handle_update(update: dict) -> bool:
    """Updates from groups and supergroups (`my_chat_member`, `chat_member`, group messages).

    Returns True when the update belonged to a group chat and was handled here.
    """
    for key, handler in (("my_chat_member", _bot_status), ("chat_member", _member_status), ("message", _message)):
        if key in update:
            event = update[key] or {}
            break
    else:
        return False
    if (event.get("chat") or {}).get("type") not in GROUP_TYPES:
        return False
    try:
        handler(event)
    except Exception:  # noqa: BLE001 - one broken group update must not stop the poller or repeat forever
        log.exception("group update failed: %s", key)
    return True


def _bot_status(event: dict) -> None:
    """The bot itself was added, promoted, demoted or removed."""
    chat = event["chat"]
    status = (event.get("new_chat_member") or {}).get("status")
    old_status = (event.get("old_chat_member") or {}).get("status")
    group = _chat_of(chat["id"])
    if status == "administrator":
        _activate(chat, group, event.get("from"))
        return
    was_active = bool(group and group.bot_active)
    if was_active:
        # Frozen: no new markets, open ones live to their outcome, members stay as last known.
        group.bot_active = False
        group.save(update_fields=["bot_active"])
    if status in ("member", "restricted") and old_status not in ("member", "restricted"):
        _say(chat["id"], f"{NOT_ADMIN}\n{FROZEN}" if was_active else NOT_ADMIN)


def _activate(chat: dict, group: Group | None, adder: dict | None) -> None:
    title = (chat.get("title") or "Чат")[:120]
    is_forum = bool(chat.get("is_forum"))
    greeting = None
    if group is None:
        try:
            with transaction.atomic():
                group = Group.objects.create(name=title, kind="chat", telegram_chat_id=chat["id"],
                                             code=chats.new_code(), bot_active=True, is_forum=is_forum)
        except IntegrityError:
            # The same update twice: the space already exists.
            group = Group.objects.get(telegram_chat_id=chat["id"])
        else:
            greeting = WELCOME
    if not group.bot_active:
        greeting = REVIVED
    group.name, group.bot_active, group.is_forum = title, True, is_forum
    fields = ["name", "bot_active", "is_forum"]
    if not group.code:
        group.code = chats.new_code()
        fields.append("code")
    group.save(update_fields=fields)
    chats.refresh_member_count(group)
    trader = _trader(adder)
    if trader is not None:
        chats.set_membership(group, trader, True)
    if greeting:
        _greet(group, greeting)


def _greet(group: Group, text: str) -> None:
    """Greeting with «Открыть рынки чата», pinned silently if the bot may pin (optional right)."""
    sent = _say(group.telegram_chat_id, text, markup=_open_chat_markup(group))
    if not sent or not sent.get("message_id"):
        return
    try:
        telegram.api("pinChatMessage", chat_id=group.telegram_chat_id, message_id=sent["message_id"],
                     disable_notification=True)
    except TelegramError:
        return
    group.pinned_message_id = sent["message_id"]
    group.save(update_fields=["pinned_message_id"])


def _member_status(event: dict) -> None:
    """`chat_member`: left or removed -> access closed at once; joined -> a member."""
    group = _chat_of(event["chat"]["id"])
    new = event.get("new_chat_member") or {}
    trader = _trader(new.get("user"))
    if group is not None and trader is not None:
        chats.set_membership(group, trader, chats.is_member_status(new))


def _message(message: dict) -> None:
    chat_id = message["chat"]["id"]
    if message.get("migrate_to_chat_id"):
        _migrate(chat_id, message["migrate_to_chat_id"])
        return
    if message.get("migrate_from_chat_id"):
        _migrate(message["migrate_from_chat_id"], chat_id)
        return
    # Service messages are a fallback for `chat_member`, which Telegram may not send in every chat.
    group = None
    if message.get("left_chat_member") or message.get("new_chat_members"):
        group = _chat_of(chat_id)
    if group is not None:
        left = _trader(message.get("left_chat_member"))
        if left is not None:
            chats.set_membership(group, left, False)
        for user in message.get("new_chat_members") or []:
            joined = _trader(user)
            if joined is not None:
                chats.set_membership(group, joined, True)
        return
    _command(message)


def _migrate(old_id: int, new_id: int) -> None:
    """A group became a supergroup: the space moves to the new chat ID without losses. Idempotent."""
    with transaction.atomic():
        source = Group.objects.select_for_update().filter(telegram_chat_id=old_id).first()
        if source is None:
            return
        target = Group.objects.select_for_update().filter(telegram_chat_id=new_id).first()
        if target is not None:
            if target.questions.exists():
                log.warning("chat %s migrated to %s, both have markets; left as is", old_id, new_id)
                return
            # A space made for the new supergroup before the migration message: the old one wins.
            for membership in target.memberships.filter(status="active"):
                GroupMembership.objects.get_or_create(group=source, trader_id=membership.trader_id, defaults={
                    "status": "active", "checked_at": membership.checked_at})
            source.bot_active = source.bot_active or target.bot_active
            target.delete()
        source.telegram_chat_id = new_id
        source.save(update_fields=["telegram_chat_id", "bot_active"])


# --- commands -----------------------------------------------------------------------------------

def _parse_command(text: str) -> str | None:
    if not text.startswith("/"):
        return None
    name, _, bot = text.split(maxsplit=1)[0][1:].partition("@")
    if bot and bot.lower() != settings.TELEGRAM_BOT_USERNAME.lower():
        return None  # addressed to another bot
    name = name.lower()
    return name if name in COMMANDS else None


def _command(message: dict) -> None:
    name = _parse_command(message.get("text") or "")
    if name is None:
        return
    chat_id = message["chat"]["id"]
    thread = message.get("message_thread_id") if message.get("is_topic_message") else None
    group = _chat_of(chat_id)
    if group is None:
        _say(chat_id, UNKNOWN_CHAT, thread=thread)
        return
    if name == "markets":
        text, markup = markets_text(group), _open_chat_markup(group)
    elif name == "top":
        text, markup = top_text(group), _open_chat_markup(group)
    else:
        sender = message.get("from") or {}
        if sender.get("id") == chats.ANONYMOUS_ADMIN_ID or (message.get("sender_chat") or {}).get("id") == chat_id:
            text, markup = ANONYMOUS, None
        elif not chats.is_admin(group, sender.get("id") or 0):
            text, markup = SETTINGS_ADMINS_ONLY, None
        else:
            text, markup = settings_text(group), settings_keyboard(group)
    _say(chat_id, text, thread=thread, markup=markup)


def open_markets(group: Group, now: datetime | None = None):
    now = now or timezone.now()
    return (Question.objects.filter(group=group, status=Question.Status.OPEN, hidden_at__isnull=True)
            .filter(Q(deadline__isnull=True) | Q(deadline__gt=now))
            .annotate(people=Count("trades__trader", distinct=True)).order_by("-people", "-created_at", "-id"))


def markets_text(group: Group) -> str:
    markets = list(open_markets(group)[:MARKETS_SHOWN + 1])
    if not markets:
        return "Открытых рынков в чате пока нет — создай первый в приложении."
    lines = ["Открытые рынки чата:"]
    for q in markets[:MARKETS_SHOWN]:
        lines.append(f"• «{q.title}» — {_percent(q)}%, {_people(q.people)}")
    if len(markets) > MARKETS_SHOWN:
        rest = open_markets(group).count() - MARKETS_SHOWN
        lines.append(f"И ещё {rest} — в приложении.")
    return "\n".join(lines)


def _ranked(board: dict, limit: int) -> list[dict]:
    """Only people with a result: a settled prediction or a non-zero profit on the chat's markets."""
    rows = [row for row in board["items"] if row["predictions"] or Decimal(row["profit"]) != 0]
    return rows[:limit]


def top_text(group: Group) -> str:
    rows = _ranked(rating.leaderboard(None, group=group), TOP_SHOWN)
    if not rows:
        return "В рейтинге чата пока пусто: он появится после первых итогов."
    lines = ["Рейтинг чата по прибыли:"]
    lines += [f"{i}. {row['display_name']} — {_signed(row['profit'])}" for i, row in enumerate(rows, start=1)]
    return "\n".join(lines)


# --- settings -----------------------------------------------------------------------------------

def _cost_choices() -> list[int]:
    return [c for c in COST_CHOICES if settings.CHAT_CREATION_COST_MIN <= c <= settings.CHAT_CREATION_COST_MAX]


def settings_text(group: Group) -> str:
    cost = chats.creation_cost(group)
    if group.creation_cost:
        how = "своя цена чата"
    else:
        how = f"по размеру чата ({_people(group.member_count)})"
    return (f"Настройки бота в чате «{group.name}».\n\n"
            f"Цена создания рынка: {cost} {plural(cost, 'монета', 'монеты', 'монет')} — {how}.\n"
            f"{PRICE_HINT}\n\n"
            "Кнопками ниже включай и выключай сообщения бота и ИИ-помощника и выбирай цену.")


def settings_keyboard(group: Group) -> dict:
    base = f"{CALLBACK_PREFIX}{group.pk}"

    def switch(label: str, on: bool, data: str) -> list[dict]:
        return [{"text": f"{label}: {'вкл' if on else 'выкл'}", "callback_data": data}]

    rows = [switch(label, chats.post_enabled(group, kind), f"{base}:post:{kind}") for kind, label in CHAT_POST_KINDS]
    rows.append(switch("ИИ-помощник", group.assist_enabled, f"{base}:assist"))
    prices = [{"text": f"✓ {c}" if group.creation_cost == c else str(c), "callback_data": f"{base}:cost:{c}"}
              for c in _cost_choices()]
    rows += [prices[i:i + 4] for i in range(0, len(prices), 4)]
    rows.append([{"text": ("✓ " if not group.creation_cost else "") + "По размеру чата",
                  "callback_data": f"{base}:cost:auto"}])
    return {"inline_keyboard": rows}


def _answer(callback: dict, text: str = "", alert: bool = False) -> None:
    try:
        telegram.api("answerCallbackQuery", callback_query_id=callback["id"], text=text, show_alert=alert)
    except TelegramError:
        pass


def _change(group_id: int, action: str, value: str) -> Group | None:
    """Apply one settings button; None when the button is unknown or the price is out of bounds."""
    kinds = {kind for kind, _ in CHAT_POST_KINDS}
    with transaction.atomic():
        group = Group.objects.select_for_update().get(pk=group_id)
        if action == "post" and value in kinds:
            group.post_settings = {**group.post_settings, value: not chats.post_enabled(group, value)}
            group.save(update_fields=["post_settings"])
        elif action == "assist":
            group.assist_enabled = not group.assist_enabled
            group.save(update_fields=["assist_enabled"])
        elif action == "cost" and value == "auto":
            group.creation_cost = None
            group.save(update_fields=["creation_cost"])
        elif action == "cost" and value.isdigit() and int(value) in _cost_choices():
            group.creation_cost = int(value)
            group.save(update_fields=["creation_cost"])
        else:
            return None
    return group


def handle_callback(callback: dict) -> None:
    """`chat:<group_id>:<action>[:<value>]` from the /settings message; admin rights checked on every press."""
    try:
        _settings_press(callback)
    except Exception:  # noqa: BLE001 - never let a button crash the poller
        log.exception("chat settings button failed")


def _settings_press(callback: dict) -> None:
    parts = (callback.get("data") or "").split(":")
    message = callback.get("message") or {}
    group = None
    if len(parts) >= 3 and parts[1].isdigit():
        group = Group.objects.filter(kind="chat", pk=int(parts[1])).first()
    if group is None or (message.get("chat") or {}).get("id") != group.telegram_chat_id:
        _answer(callback, STALE)
        return
    if not chats.is_admin(group, (callback.get("from") or {}).get("id") or 0):
        _answer(callback, ALERT_ADMINS_ONLY, alert=True)
        return
    group = _change(group.pk, parts[2], parts[3] if len(parts) > 3 else "")
    if group is None:
        _answer(callback, NO_SUCH)
        return
    _answer(callback, SAVED)
    try:
        telegram.api("editMessageText", chat_id=group.telegram_chat_id, message_id=message.get("message_id"),
                     text=settings_text(group), reply_markup=settings_keyboard(group))
    except TelegramError:
        log.warning("settings message in chat %s not updated", group.telegram_chat_id, exc_info=True)


# --- outbox -------------------------------------------------------------------------------------

def new_market_text(question: Question) -> str:
    n = _participants(question)
    tail = f" · {_people(n)}" if n else ""
    return f"Новый рынок в чате:\n«{question.title}»\nВероятность ДА: {_percent(question)}%{tail}"


def result_text(question: Question) -> str:
    if question.status == Question.Status.ANNULLED:
        return f"Рынок чата отменён:\n«{question.title}»\nМонеты вернули участникам."
    label = "ДА" if question.outcome == Question.Outcome.YES else "НЕТ"
    return (f"Итог рынка чата:\n«{question.title}» — {label}.\n"
            f"Участников: {_participants(question)}. Выплаты уже на балансах.")


def weekly_top_text(rows: list[dict]) -> str:
    lines = ["Топ-3 недели в чате по прибыли:"]
    lines += [f"{i}. {row['display_name']} — {_signed(row['profit'])}" for i, row in enumerate(rows, start=1)]
    lines.append("Считаем только рынки этого чата.")
    return "\n".join(lines)


def _post(group: Group, kind: str, key: str, text: str, now: datetime, *, question: Question | None = None,
          method: str = "sendMessage", payload: dict | None = None, status: str = ChatPost.Status.QUEUED) -> bool:
    _, created = ChatPost.objects.get_or_create(key=key, defaults={
        "group": group, "kind": kind, "text": text, "question": question, "method": method,
        "payload": payload or {}, "send_after": now, "status": status})
    return created and status == ChatPost.Status.QUEUED


def fill(now: datetime) -> int:
    """Queue new markets, results and the weekly top-3 for active chats. Idempotent by `ChatPost.key`."""
    queued = 0
    groups = {g.pk: g for g in Group.objects.filter(kind="chat", bot_active=True, telegram_chat_id__isnull=False)}
    if not groups:
        return 0
    fresh = (Question.objects.filter(group_id__in=groups, created_at__gte=now - timedelta(days=1),
                                     status=Question.Status.OPEN, hidden_at__isnull=True)
             .exclude(chat_posts__kind="new_market"))
    for q in fresh:
        group = groups[q.group_id]
        if chats.post_enabled(group, "new_market"):
            queued += _post(group, "new_market", f"new:{q.pk}", new_market_text(q), now, question=q,
                            payload={"reply_markup": _market_markup(q)})
    finished = (Question.objects.filter(group_id__in=groups, resolution_state=Question.Resolution.FINAL,
                                        resolved_at__gte=now - timedelta(days=7), moderation_hidden=False)
                .exclude(chat_posts__kind="results"))
    for q in finished:
        group = groups[q.group_id]
        if not chats.post_enabled(group, "results"):
            continue
        payload = {"reply_markup": _market_markup(q)}
        method = "sendMessage"
        if q.evidence_file_id and q.status == Question.Status.RESOLVED:
            method, payload["photo"] = "sendPhoto", q.evidence_file_id
        queued += _post(group, "results", f"result:{q.pk}", result_text(q), now, question=q, method=method,
                        payload=payload)
    queued += _fill_weekly_top(groups.values(), now)
    return queued


def _fill_weekly_top(groups, now: datetime) -> int:
    local = timezone.localtime(now, MSK)
    if local.weekday() != 0 or local.hour < settings.NOTIFY_QUIET_HOURS[1]:
        return 0
    year, week, _ = local.isocalendar()
    queued = 0
    for group in groups:
        key = f"top:{group.pk}:{year}-W{week:02d}"
        if not chats.post_enabled(group, "weekly_top") or ChatPost.objects.filter(key=key).exists():
            continue
        rows = _ranked(rating.leaderboard(None, group=group, period="week"), WEEKLY_TOP)
        if not rows:
            # Nobody has a result this week: remember that, so the board is not recounted every minute.
            _post(group, "weekly_top", key, "", now, status=ChatPost.Status.SKIPPED)
            continue
        queued += _post(group, "weekly_top", key, weekly_top_text(rows), now,
                        payload={"reply_markup": _open_chat_markup(group)})
    return queued


def sent_today(group_id: int, now: datetime) -> int:
    start, end = _day_bounds(now)
    return ChatPost.objects.filter(group_id=group_id, status=ChatPost.Status.SENT,
                                   sent_at__gte=start, sent_at__lt=end).count()


def _next_morning(now: datetime) -> datetime:
    day = timezone.localtime(now, MSK).date() + timedelta(days=1)
    return datetime.combine(day, time(settings.NOTIFY_QUIET_HOURS[1]), tzinfo=MSK)


def _claim(post: ChatPost, now: datetime) -> bool:
    """Lease a queued row so a second worker process cannot send it too."""
    claimed = ChatPost.objects.filter(pk=post.pk, status=ChatPost.Status.QUEUED,
                                      send_after=post.send_after).update(send_after=now + LEASE)
    if claimed:
        post.send_after = now + LEASE
    return bool(claimed)


def _finish(post: ChatPost, status: str, now: datetime | None = None) -> None:
    post.status, post.sent_at = status, now
    post.save(update_fields=["status", "sent_at", "attempts", "send_after", "text"])


def _send(post: ChatPost) -> None:
    payload = dict(post.payload or {})
    chat_id = post.group.telegram_chat_id
    # In a chat with topics the outbox goes to the general topic: no message_thread_id.
    if post.method == "sendPhoto":
        telegram.api("sendPhoto", chat_id=chat_id, caption=post.text, **payload)
    else:
        telegram.api("sendMessage", chat_id=chat_id, text=post.text, **payload)


def flush(now: datetime) -> dict:
    """Send due chat posts: quiet hours, CHAT_DAILY_POSTS per chat and Moscow day, three tries."""
    done = {"sent": 0, "skipped": 0, "failed": 0, "postponed": 0}
    due = ChatPost.objects.filter(status=ChatPost.Status.QUEUED, send_after__lte=now)
    wake = _quiet_until(now)
    if wake is not None:
        done["postponed"] = due.update(send_after=wake)
        return done
    for post in due.select_related("group", "question").order_by("send_after", "id"):
        if not _claim(post, now):
            continue
        group, question = post.group, post.question
        if not group.bot_active or not group.telegram_chat_id or not chats.post_enabled(group, post.kind):
            _finish(post, ChatPost.Status.SKIPPED)
            done["skipped"] += 1
            continue
        if post.kind == "new_market" and question is not None:
            if question.status != Question.Status.OPEN or question.hidden_at:
                _finish(post, ChatPost.Status.SKIPPED)
                done["skipped"] += 1
                continue
            post.text = new_market_text(question)  # the probability and people as of sending
        if sent_today(group.pk, now) >= settings.CHAT_DAILY_POSTS:
            if post.kind == "results":
                # A result is worth waiting for; a new market and the weekly top are dropped.
                post.send_after = _next_morning(now)
                post.save(update_fields=["send_after"])
                done["postponed"] += 1
            else:
                _finish(post, ChatPost.Status.SKIPPED)
                done["skipped"] += 1
            continue
        try:
            _send(post)
        except Exception:  # noqa: BLE001 - Telegram may be down; retried next tick
            log.warning("chat post %s to group %s failed", post.pk, group.pk, exc_info=True)
            post.attempts += 1
            if post.attempts >= MAX_ATTEMPTS:
                _finish(post, ChatPost.Status.FAILED)
                done["failed"] += 1
            else:
                post.send_after = now + timedelta(minutes=post.attempts)
                post.save(update_fields=["attempts", "send_after"])
            continue
        _finish(post, ChatPost.Status.SENT, now)
        done["sent"] += 1
    return done


def tick(now) -> dict:
    """Worker step: fill the chat outbox (new markets, results, weekly top-3) and send due posts."""
    now = now or timezone.now()
    queued = fill(now)
    return {"queued": queued, **flush(now)}
