"""The bot in closed chats (spec E8.1, E8.4, E8.5): joining, membership, commands, settings, chat outbox."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from market import bot_chats, chats, services
from market.bot_notifications import MSK
from market.models import ChatPost, Group, GroupMembership, Question, Trader
from market.telegram import TelegramError, handle_update

D = Decimal
ADMIN, ANYA, BORIS, OUTSIDER = 3001, 3002, 3003, 3004
CHAT = -100500


@pytest.fixture(autouse=True)
def env():
    with override_settings(SITE_URL="https://example.test", TELEGRAM_BOT_TOKEN="T",
                           TELEGRAM_BOT_USERNAME="PredBot", TELEGRAM_APP_NAME="app"):
        yield


class Bot:
    """Records Bot API calls instead of going to the network."""

    def __init__(self):
        self.calls = []
        self.admins = [ADMIN]
        self.fail = set()
        self.member_count = 12

    def __call__(self, method, **data):
        self.calls.append((method, data))
        if method in self.fail:
            raise TelegramError("nope")
        if method == "getChatMemberCount":
            return self.member_count
        if method == "getChatAdministrators":
            return [{"status": "administrator", "user": {"id": uid}} for uid in self.admins]
        if method in ("pinChatMessage", "answerCallbackQuery"):
            return True
        return {"message_id": len(self.calls)}

    def sent(self, method=None, chat_id=None):
        return [d for m, d in self.calls
                if (method is None or m == method) and (chat_id is None or d.get("chat_id") == chat_id)]

    def methods(self):
        return [m for m, _ in self.calls]


@pytest.fixture
def bot():
    fake = Bot()
    with patch("market.telegram.api", side_effect=fake):
        yield fake


def make(name, tg=None, balance=1000):
    return Trader.objects.create(name=name, pseudonym=name, balance=D(balance), telegram_chat_id=tg)


def chat(**kw):
    fields = dict(name="Группа ПИ-24", kind="chat", telegram_chat_id=CHAT, code=chats.new_code(),
                  bot_active=True, member_count=12)
    fields.update(kw)
    return Group.objects.create(**fields)


def join(group, trader):
    return GroupMembership.objects.create(group=group, trader=trader, status="active", checked_at=timezone.now())


def chat_market(group, creator, title="Перенесут ли коллоквиум?"):
    if not GroupMembership.objects.filter(group=group, trader=creator).exists():
        join(group, creator)
    now = timezone.now()
    return services.create_market(creator, title=title, criteria="По объявлению в чате", category="study",
                                  closes_at=now + timedelta(days=3), resolves_at=now + timedelta(days=4), group=group)


def finish(question, outcome="yes", evidence=""):
    if outcome == "cancel":
        services.annul(question)
    else:
        services.resolve(question, outcome)
    Question.objects.filter(pk=question.pk).update(resolution_state=Question.Resolution.FINAL,
                                                   evidence_file_id=evidence)
    question.refresh_from_db()
    return question


def bot_status(status, old="left", by=ADMIN, chat_id=CHAT, **chat_extra):
    return {"update_id": 1, "my_chat_member": {
        "chat": {"id": chat_id, "type": "supergroup", "title": "Группа ПИ-24", **chat_extra},
        "from": {"id": by, "first_name": "Админ"}, "date": 1,
        "old_chat_member": {"status": old, "user": {"id": 1, "is_bot": True}},
        "new_chat_member": {"status": status, "user": {"id": 1, "is_bot": True}}}}


def member_status(user_id, status, chat_id=CHAT):
    return {"update_id": 2, "chat_member": {
        "chat": {"id": chat_id, "type": "supergroup", "title": "Группа ПИ-24"}, "from": {"id": user_id}, "date": 1,
        "old_chat_member": {"status": "member", "user": {"id": user_id}},
        "new_chat_member": {"status": status, "user": {"id": user_id, "is_bot": False}}}}


def message(text, by=ADMIN, chat_id=CHAT, **extra):
    return {"update_id": 3, "message": {"message_id": 10, "chat": {"id": chat_id, "type": "supergroup"},
                                        "from": {"id": by}, "text": text, **extra}}


def press(group, data, by=ADMIN, chat_id=CHAT):
    return {"update_id": 4, "callback_query": {"id": "cb1", "from": {"id": by}, "data": data,
                                               "message": {"message_id": 77, "chat": {"id": chat_id}}}}


def msk(hour, minute=0, days=0, base=None):
    day = (base or timezone.localtime(timezone.now(), MSK).date()) + timedelta(days=days)
    return datetime.combine(day, time(hour, minute), tzinfo=MSK)


def next_monday():
    today = timezone.localtime(timezone.now(), MSK).date()
    return today + timedelta(days=(7 - today.weekday()) % 7)


# --- joining a chat -------------------------------------------------------------------------------

@pytest.mark.django_db
def test_added_as_admin_creates_space_greets_pins_and_adds_the_adder(bot):
    admin = make("Админ", ADMIN)
    handle_update(bot_status("administrator", is_forum=True))
    group = Group.objects.get(telegram_chat_id=CHAT)
    assert group.kind == "chat" and group.bot_active and group.is_forum
    assert group.code and str(CHAT) not in group.code
    assert group.name == "Группа ПИ-24" and group.member_count == 12
    greeting = bot.sent("sendMessage", CHAT)[0]
    assert "Привет" in greeting["text"]
    assert "message_thread_id" not in greeting
    assert greeting["reply_markup"] == {"inline_keyboard": [[
        {"text": "Открыть рынки чата", "url": f"https://t.me/PredBot/app?startapp=chat_{group.code}"}]]}
    pin = bot.sent("pinChatMessage")[0]
    assert pin["disable_notification"] is True
    assert group.pinned_message_id == pin["message_id"]
    assert GroupMembership.objects.get(group=group, trader=admin).status == "active"


@pytest.mark.django_db
def test_pin_is_optional(bot):
    bot.fail = {"pinChatMessage"}
    handle_update(bot_status("administrator"))
    group = Group.objects.get(telegram_chat_id=CHAT)
    assert group.bot_active and group.pinned_message_id is None
    assert bot.sent("sendMessage", CHAT)


@pytest.mark.django_db
def test_added_as_plain_member_explains_and_creates_nothing(bot):
    handle_update(bot_status("member"))
    assert not Group.objects.exists()
    (reply,) = bot.sent("sendMessage", CHAT)
    assert "администратором" in reply["text"] and "вышел" in reply["text"]


@pytest.mark.django_db
def test_removed_freezes_and_return_revives_the_same_space(bot):
    handle_update(bot_status("administrator"))
    group = Group.objects.get()
    code, market = group.code, chat_market(group, make("Автор", ADMIN))
    handle_update(bot_status("left", old="administrator"))
    group.refresh_from_db()
    assert not group.bot_active
    market.refresh_from_db()
    assert market.status == Question.Status.OPEN  # open markets live to their outcome
    with pytest.raises(services.MarketError):
        chat_market(group, make("Другой"))
    calls = len(bot.calls)
    handle_update(bot_status("administrator", old="member"))
    group.refresh_from_db()
    assert Group.objects.count() == 1 and group.bot_active and group.code == code
    assert any("снова" in d.get("text", "") for m, d in bot.calls[calls:] if m == "sendMessage")


@pytest.mark.django_db
def test_demoted_to_member_freezes_and_explains(bot):
    handle_update(bot_status("administrator"))
    handle_update(bot_status("member", old="administrator"))
    assert not Group.objects.get().bot_active
    assert "до итога" in bot.sent("sendMessage", CHAT)[-1]["text"]


@pytest.mark.django_db
def test_rights_change_of_active_admin_sends_nothing(bot):
    handle_update(bot_status("administrator"))
    sent = len(bot.sent("sendMessage"))
    handle_update(bot_status("administrator", old="administrator"))
    assert len(bot.sent("sendMessage")) == sent


@pytest.mark.django_db
def test_private_updates_are_not_ours(bot):
    assert not bot_chats.handle_update({"message": {"chat": {"id": 5, "type": "private"}, "text": "/top"}})
    assert not bot_chats.handle_update({"my_chat_member": {"chat": {"id": 5, "type": "private"}}})
    assert bot_chats.handle_update(message("просто текст"))
    assert not bot.calls


# --- supergroup and members -----------------------------------------------------------------------

@pytest.mark.django_db
def test_migration_to_supergroup_moves_the_space(bot):
    group = chat(telegram_chat_id=-500)
    market = chat_market(group, make("Автор"))
    handle_update({"update_id": 5, "message": {"message_id": 1, "chat": {"id": -500, "type": "group"},
                                               "migrate_to_chat_id": -100777}})
    group.refresh_from_db()
    assert group.telegram_chat_id == -100777
    handle_update({"update_id": 6, "message": {"message_id": 1, "chat": {"id": -100777, "type": "supergroup"},
                                               "migrate_from_chat_id": -500}})
    group.refresh_from_db()
    assert group.telegram_chat_id == -100777 and Group.objects.count() == 1
    assert market.group_id == group.pk


@pytest.mark.django_db
def test_migration_merges_a_space_made_too_early(bot):
    old = chat(telegram_chat_id=-500)
    chat_market(old, make("Автор"))
    early = chat(telegram_chat_id=-100777)
    anya = make("Аня", ANYA)
    join(early, anya)
    handle_update({"update_id": 6, "message": {"message_id": 1, "chat": {"id": -100777, "type": "supergroup"},
                                               "migrate_from_chat_id": -500}})
    old.refresh_from_db()
    assert old.telegram_chat_id == -100777
    assert not Group.objects.filter(pk=early.pk).exists()
    assert GroupMembership.objects.get(group=old, trader=anya).status == "active"


@pytest.mark.django_db
def test_chat_member_left_closes_access_and_join_opens_it(bot):
    group = chat()
    anya = make("Аня", ANYA)
    join(group, anya)
    market = chat_market(group, make("Автор"))
    handle_update(member_status(ANYA, "left"))
    assert GroupMembership.objects.get(group=group, trader=anya).status == "left"
    with pytest.raises(services.MarketError):
        services.buy(anya, market, "yes", 10)
    handle_update(member_status(ANYA, "member"))
    assert GroupMembership.objects.get(group=group, trader=anya).status == "active"
    handle_update(member_status(ANYA, "kicked"))
    assert GroupMembership.objects.get(group=group, trader=anya).status == "left"
    # Strangers without an account are ignored.
    handle_update(member_status(OUTSIDER, "member"))
    assert GroupMembership.objects.filter(group=group).count() == 2


# --- commands -------------------------------------------------------------------------------------

@pytest.mark.django_db
def test_markets_lists_open_markets_without_who_bet_how(bot):
    group = chat()
    market = chat_market(group, make("Автор"))
    anya, boris = make("Аня", ANYA), make("Борис", BORIS)
    for trader, side in ((anya, "yes"), (boris, "no")):
        join(group, trader)
        services.buy(trader, market, side, 20)
    market.refresh_from_db()
    handle_update(message("/markets"))
    (reply,) = bot.sent("sendMessage", CHAT)
    assert "«Перенесут ли коллоквиум?»" in reply["text"]
    assert f"{round(market.prob * 100)}%" in reply["text"] and "2 участника" in reply["text"]
    assert "Аня" not in reply["text"] and "Борис" not in reply["text"]
    assert reply["reply_markup"]["inline_keyboard"][0][0]["url"] == chats.link(group)


@pytest.mark.django_db
def test_markets_when_empty(bot):
    chat()
    handle_update(message("/markets@predbot"))
    assert "пока нет" in bot.sent("sendMessage", CHAT)[0]["text"]


@pytest.mark.django_db
def test_commands_for_another_bot_are_ignored(bot):
    chat()
    handle_update(message("/markets@other_bot"))
    handle_update(message("/start"))
    assert not bot.sent("sendMessage")
    handle_update(message("/MARKETS@PREDBOT"))
    assert len(bot.sent("sendMessage")) == 1


@pytest.mark.django_db
def test_unknown_chat_gets_a_hint(bot):
    handle_update(message("/top"))
    assert "администратором" in bot.sent("sendMessage", CHAT)[0]["text"]
    assert not Group.objects.exists()


@pytest.mark.django_db
def test_top_shows_only_aggregates_of_people_with_a_result(bot):
    group = chat()
    market = chat_market(group, make("Автор"))
    anya, boris, idle = make("Аня", ANYA), make("Борис", BORIS), make("Молчун")
    for trader in (anya, boris, idle):
        join(group, trader)
    services.buy(anya, market, "yes", 50)
    services.buy(boris, market, "no", 30)
    finish(market, "yes")
    handle_update(message("/top"))
    text = bot.sent("sendMessage", CHAT)[0]["text"]
    lines = text.splitlines()
    assert lines[0] == "Рейтинг чата по прибыли:"
    assert lines[1].startswith("1. Аня — +") and "Борис — -30" in text
    assert "Молчун" not in text and "ДА" not in text and "НЕТ" not in text


@pytest.mark.django_db
def test_settings_only_for_admins(bot):
    group = chat()
    handle_update(message("/settings", by=ANYA))
    assert bot.sent("sendMessage", CHAT)[-1]["text"] == bot_chats.SETTINGS_ADMINS_ONLY
    assert "reply_markup" not in bot.sent("sendMessage", CHAT)[-1]
    handle_update(message("/settings", by=ADMIN))
    reply = bot.sent("sendMessage", CHAT)[-1]
    assert "40 монет — по размеру чата (12 участников)" in reply["text"]
    assert "с 50% до 75%" in reply["text"]
    data = [b["callback_data"] for row in reply["reply_markup"]["inline_keyboard"] for b in row]
    assert f"chat:{group.pk}:post:new_market" in data and f"chat:{group.pk}:assist" in data
    assert [d.rsplit(":", 1)[1] for d in data if ":cost:" in d] == ["30", "40", "50", "70", "100", "150", "200",
                                                                   "auto"]


@pytest.mark.django_db
@pytest.mark.parametrize("extra", [{"from": {"id": chats.ANONYMOUS_ADMIN_ID}},
                                   {"sender_chat": {"id": CHAT, "type": "supergroup"}}])
def test_anonymous_admin_is_asked_to_write_as_self(bot, extra):
    chat()
    update = message("/settings")
    update["message"].update(extra)
    handle_update(update)
    assert bot.sent("sendMessage", CHAT)[-1]["text"] == bot_chats.ANONYMOUS
    assert "getChatAdministrators" not in bot.methods()


@pytest.mark.django_db
def test_answers_in_the_same_topic(bot):
    chat(is_forum=True)
    handle_update(message("/markets", message_thread_id=42, is_topic_message=True))
    assert bot.sent("sendMessage", CHAT)[-1]["message_thread_id"] == 42
    handle_update(message("/markets"))  # the general topic
    assert "message_thread_id" not in bot.sent("sendMessage", CHAT)[-1]


# --- settings buttons -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_press_by_non_admin_changes_nothing(bot):
    group = chat()
    handle_update(press(group, f"chat:{group.pk}:post:new_market", by=ANYA))
    handle_update(press(group, f"chat:{group.pk}:cost:200", by=ANYA))
    group.refresh_from_db()
    assert group.post_settings == {} and group.creation_cost is None
    answer = bot.sent("answerCallbackQuery")[-1]
    assert answer["show_alert"] is True and answer["text"] == bot_chats.ALERT_ADMINS_ONLY
    assert not bot.sent("editMessageText")


@pytest.mark.django_db
def test_press_by_admin_changes_and_asks_telegram_every_time(bot):
    group = chat()
    presses = [f"chat:{group.pk}:post:new_market", f"chat:{group.pk}:assist", f"chat:{group.pk}:cost:150"]
    for data in presses:
        handle_update(press(group, data))
    group.refresh_from_db()
    assert group.post_settings == {"new_market": False} and not group.assist_enabled
    assert group.creation_cost == 150 and chats.creation_cost(group) == 150
    assert bot.methods().count("getChatAdministrators") == len(presses)
    edit = bot.sent("editMessageText")[-1]
    assert edit["message_id"] == 77 and "150 монет — своя цена чата" in edit["text"]
    buttons = [b["text"] for row in edit["reply_markup"]["inline_keyboard"] for b in row]
    assert "Новые рынки: выкл" in buttons and "ИИ-помощник: выкл" in buttons and "✓ 150" in buttons
    # Admin rights are gone: the next press is refused although it worked a moment ago.
    bot.admins = []
    handle_update(press(group, f"chat:{group.pk}:cost:auto"))
    group.refresh_from_db()
    assert group.creation_cost == 150
    bot.admins = [ADMIN]
    handle_update(press(group, f"chat:{group.pk}:cost:auto"))
    group.refresh_from_db()
    assert group.creation_cost is None
    assert "✓ По размеру чата" in [b["text"] for row in bot.sent("editMessageText")[-1]["reply_markup"]
                                   ["inline_keyboard"] for b in row]


@pytest.mark.django_db
@override_settings(CHAT_CREATION_COST_MAX=100)
def test_price_stays_within_bounds(bot):
    group = chat()
    for value in ("500", "35", "150", "-1", "abc"):
        handle_update(press(group, f"chat:{group.pk}:cost:{value}"))
    group.refresh_from_db()
    assert group.creation_cost is None
    handle_update(message("/settings"))
    data = [b["callback_data"] for row in bot.sent("sendMessage", CHAT)[-1]["reply_markup"]["inline_keyboard"]
            for b in row]
    assert f"chat:{group.pk}:cost:150" not in data and f"chat:{group.pk}:cost:100" in data


@pytest.mark.django_db
def test_press_from_another_chat_is_stale(bot):
    group = chat()
    handle_update(press(group, f"chat:{group.pk}:assist", chat_id=-999))
    group.refresh_from_db()
    assert group.assist_enabled
    assert "getChatAdministrators" not in bot.methods()


# --- outbox ---------------------------------------------------------------------------------------

@pytest.mark.django_db
def test_new_market_queued_once_and_sent_without_names(bot):
    group = chat()
    market = chat_market(group, make("Автор Рынка"))
    now = msk(12)
    first = bot_chats.tick(now)
    second = bot_chats.tick(now + timedelta(minutes=1))
    assert first["queued"] == 1 and first["sent"] == 1 and second["queued"] == 0 and second["sent"] == 0
    post = ChatPost.objects.get()
    assert post.key == f"new:{market.pk}" and post.status == ChatPost.Status.SENT
    (sent,) = bot.sent("sendMessage", CHAT)
    assert "Новый рынок" in sent["text"] and "«Перенесут ли коллоквиум?»" in sent["text"]
    assert "Автор" not in sent["text"] and "message_thread_id" not in sent
    assert sent["reply_markup"]["inline_keyboard"][0][0]["url"] == f"https://t.me/PredBot/app?startapp=m{market.pk}"


@pytest.mark.django_db
def test_switched_off_kind_is_not_queued(bot):
    group = chat(post_settings={"new_market": False})
    chat_market(group, make("Автор"))
    assert bot_chats.tick(msk(12))["queued"] == 0
    assert not ChatPost.objects.exists() and not bot.calls


@pytest.mark.django_db
def test_frozen_chat_gets_no_posts(bot):
    group = chat()
    market = chat_market(group, make("Автор"))
    bot_chats.tick(msk(3))  # queued during the quiet hours
    assert ChatPost.objects.get().status == ChatPost.Status.QUEUED
    Group.objects.filter(pk=group.pk).update(bot_active=False)
    finish(market, "yes")
    done = bot_chats.tick(msk(10))
    assert done["queued"] == 0 and done["skipped"] == 1 and not bot.sent("sendMessage")


@pytest.mark.django_db
def test_result_with_photo_goes_as_send_photo(bot):
    group = chat()
    market = chat_market(group, make("Автор"))
    anya = make("Аня", ANYA)
    join(group, anya)
    services.buy(anya, market, "yes", 20)
    Group.objects.filter(pk=group.pk).update(post_settings={"new_market": False})
    finish(market, "yes", evidence="photo-file-1")
    bot_chats.tick(msk(12))
    (photo,) = bot.sent("sendPhoto", CHAT)
    assert photo["photo"] == "photo-file-1"
    assert "«Перенесут ли коллоквиум?» — ДА" in photo["caption"] and "Участников: 1" in photo["caption"]
    assert "Аня" not in photo["caption"]
    assert photo["reply_markup"]["inline_keyboard"][0][0]["url"].endswith(f"startapp=m{market.pk}")
    assert ChatPost.objects.get(key=f"result:{market.pk}").method == "sendPhoto"


@pytest.mark.django_db
def test_cancelled_market_result_is_text(bot):
    group = chat(post_settings={"new_market": False})
    market = chat_market(group, make("Автор"))
    finish(market, "cancel", evidence="ignored")
    bot_chats.tick(msk(12))
    (sent,) = bot.sent("sendMessage", CHAT)
    assert "отменён" in sent["text"] and "Монеты вернули" in sent["text"]
    assert not bot.sent("sendPhoto")


@pytest.mark.django_db
def test_daily_cap_postpones_results_and_drops_the_rest(bot):
    group = chat()
    now = msk(12)
    for i in range(5):
        ChatPost.objects.create(group=group, kind="new_market", key=f"old:{i}", text="x",
                                status=ChatPost.Status.SENT, send_after=now, sent_at=now - timedelta(minutes=i))
    resolved = chat_market(group, make("Автор 1"), title="Сдадим ли все зачёт?")
    finish(resolved, "no")
    fresh = chat_market(group, make("Автор 2"), title="Будет ли снег?")
    Question.objects.filter(pk=resolved.pk).update(created_at=now - timedelta(days=2))
    done = bot_chats.tick(now)
    assert done["sent"] == 0 and done["skipped"] == 1 and done["postponed"] == 1
    assert ChatPost.objects.get(key=f"new:{fresh.pk}").status == ChatPost.Status.SKIPPED
    result = ChatPost.objects.get(key=f"result:{resolved.pk}")
    assert result.status == ChatPost.Status.QUEUED and result.send_after == msk(9, days=1)
    assert not bot.calls
    # Next morning the cap starts over.
    assert bot_chats.tick(msk(9, 1, days=1))["sent"] == 1
    assert "— НЕТ" in bot.sent("sendMessage", CHAT)[0]["text"]


@pytest.mark.django_db
def test_quiet_hours_wait_until_nine(bot):
    group = chat()
    chat_market(group, make("Автор"))
    done = bot_chats.tick(msk(23, 30))
    assert done["queued"] == 1 and done["postponed"] == 1 and not bot.calls
    assert ChatPost.objects.get().send_after == msk(9, days=1)
    assert bot_chats.tick(msk(8, 59, days=1))["sent"] == 0
    assert bot_chats.tick(msk(9, 0, days=1))["sent"] == 1


@pytest.mark.django_db
def test_claimed_post_is_sent_once(bot):
    group = chat()
    now = msk(12)
    post = ChatPost.objects.create(group=group, kind="new_market", key="k", text="x", send_after=now)
    assert bot_chats._claim(post, now)
    stale = ChatPost.objects.get(pk=post.pk)
    stale.send_after = now
    assert not bot_chats._claim(stale, now)
    assert bot_chats.flush(now)["sent"] == 0


@pytest.mark.django_db
def test_telegram_errors_retried_then_failed(bot):
    group = chat()
    now = msk(12)
    ChatPost.objects.create(group=group, kind="weekly_top", key="k", text="x", send_after=now)
    bot.fail = {"sendMessage"}
    for minutes in (0, 1, 3):
        bot_chats.flush(now + timedelta(minutes=minutes))
    post = ChatPost.objects.get()
    assert post.status == ChatPost.Status.FAILED and post.attempts == 3


@pytest.mark.django_db
def test_weekly_top_only_on_monday_after_nine_and_once(bot):
    group = chat(post_settings={"new_market": False, "results": False})
    market = chat_market(group, make("Автор"))
    anya, boris, idle = make("Аня", ANYA), make("Борис", BORIS), make("Молчун")
    for trader in (anya, boris, idle):
        join(group, trader)
    services.buy(anya, market, "yes", 50)
    services.buy(boris, market, "no", 30)
    finish(market, "yes")
    monday = next_monday()
    assert bot_chats.tick(msk(12, base=monday - timedelta(days=1)))["queued"] == 0  # Sunday
    assert bot_chats.tick(msk(8, 30, base=monday))["queued"] == 0
    done = bot_chats.tick(msk(10, base=monday))
    assert done["queued"] == 1 and done["sent"] == 1
    assert bot_chats.tick(msk(15, base=monday))["queued"] == 0
    year, week, _ = monday.isocalendar()
    post = ChatPost.objects.get(kind="weekly_top")
    assert post.key == f"top:{group.pk}:{year}-W{week:02d}"
    lines = bot.sent("sendMessage", CHAT)[0]["text"].splitlines()
    assert lines[0] == "Топ-3 недели в чате по прибыли:"
    assert lines[1].startswith("1. Аня — +") and lines[2].startswith("2. ") and "Борис — -30" in lines[-2]
    assert "Молчун" not in post.text


@pytest.mark.django_db
def test_weekly_top_without_results_is_not_posted(bot):
    group = chat()
    join(group, make("Аня", ANYA))
    done = bot_chats.tick(msk(10, base=next_monday()))
    assert done["queued"] == 0 and not bot.calls
    assert ChatPost.objects.get(kind="weekly_top").status == ChatPost.Status.SKIPPED


@pytest.mark.django_db
def test_worker_runs_the_chat_step(bot):
    from market import resolution
    group = chat()
    chat_market(group, make("Автор"))
    with patch("market.bot_chats.fill", return_value=0) as fill:
        done = resolution.tick(msk(12))
    fill.assert_called_once()
    assert "chats_queued" in done


@pytest.mark.django_db
def test_setup_bot_sets_group_commands(bot):
    call_command("setup_bot")
    (group_list,) = [d for d in bot.sent("setMyCommands") if d.get("scope")]
    assert group_list["scope"] == {"type": "all_group_chats"}
    assert [c["command"] for c in group_list["commands"]] == ["markets", "top", "settings"]
    private = [d for d in bot.sent("setMyCommands") if not d.get("scope")]
    assert private[0]["commands"][0]["command"] == "start"
