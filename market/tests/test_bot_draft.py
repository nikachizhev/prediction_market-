"""`/market` in a closed chat (spec E9.4): the draft from the AI helper, the preview and `Создать за <цена>`.

Telegram (`market.telegram.api`) and the model (`market.assist.requests.post`) are always mocked.
"""
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest
import requests
from django.test import override_settings
from django.utils import timezone

from market import bot_draft, chats
from market.bot_chats import FROZEN
from market.models import Event, Group, GroupMembership, LedgerEntry, MarketDraft, Question, Trader
from market.telegram import TelegramError, handle_update

pytestmark = pytest.mark.django_db

D = Decimal
MSK = ZoneInfo("Europe/Moscow")
ANYA, BORIS, OUTSIDER, STRANGER = 3002, 3003, 3004, 3005
CHAT = -100777
SOURCE = "спорим, что коллоквиум по матанализу перенесут на следующую неделю"
QUESTION = "Перенесут ли коллоквиум по матанализу до 14 ноября?"


@pytest.fixture(autouse=True)
def env():
    with override_settings(TELEGRAM_BOT_TOKEN="T", TELEGRAM_BOT_USERNAME="PredBot", TELEGRAM_APP_NAME="app",
                           LLM_API_KEY="test-key", ASSIST_ENABLED=True, MARKETS_PER_DAY=3):
        yield


class Bot:
    """Records Bot API calls instead of going to the network."""

    def __init__(self):
        self.calls = []
        self.members = {ANYA, BORIS}
        self.member_count = 12

    def __call__(self, method, **data):
        self.calls.append((method, data))
        if method == "getChatMember":
            return {"status": "member" if data["user_id"] in self.members else "left"}
        if method == "getChatMemberCount":
            return self.member_count
        if method == "answerCallbackQuery":
            return True
        return {"message_id": 900 + len(self.calls)}

    def sent(self, method):
        return [d for m, d in self.calls if m == method]

    def texts(self):
        return [d["text"] for d in self.sent("sendMessage") if d.get("chat_id") == CHAT]

    def alerts(self):
        return [(d["text"], d["show_alert"]) for d in self.sent("answerCallbackQuery")]


@pytest.fixture
def bot():
    fake = Bot()
    with patch("market.telegram.api", side_effect=fake):
        yield fake


def make(name, tg, balance=1000):
    return Trader.objects.create(name=name, pseudonym=name, balance=D(balance), telegram_chat_id=tg)


@pytest.fixture
def group():
    return Group.objects.create(name="Группа ПИ-24", kind="chat", telegram_chat_id=CHAT, code=chats.new_code(),
                                bot_active=True, member_count=12, assist_warned_at=timezone.now())


@pytest.fixture
def anya(group):
    trader = make("Аня", ANYA)
    GroupMembership.objects.create(group=group, trader=trader, status="active", checked_at=timezone.now())
    return trader


@pytest.fixture
def boris(group):
    trader = make("Борис", BORIS)
    GroupMembership.objects.create(group=group, trader=trader, status="active", checked_at=timezone.now())
    return trader


def day(offset: int, at: str = "23:59") -> str:
    return f"{(timezone.now().astimezone(MSK).date() + timedelta(days=offset)).isoformat()}T{at}"


def answer(**kw) -> dict:
    data = {"forbidden": None, "question": QUESTION, "why": "Без срока итог не определить — добавили дату.",
            "options": None,
            "criteria": "ДА — если коллоквиум перенесут: объявление в чате группы.\nНЕТ — если пройдёт по расписанию.\n"
                        "Чем подтвердить: скриншот объявления.",
            "category": "study", "closes_at": day(5), "resolves_at": day(7)}
    data.update(kw)
    return data


def reply(content) -> Mock:
    response = Mock(status_code=200)
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    response.json.return_value = {"choices": [{"message": {"role": "assistant", "content": text}}]}
    return response


def model(*responses):
    return patch("market.assist.requests.post", side_effect=list(responses))


def command(user_id, source=SOURCE, *, text="/market", source_from=BORIS, **extra):
    message = {"message_id": 50, "chat": {"id": CHAT, "type": "supergroup"}, "from": {"id": user_id, "first_name": "Х"},
               "text": text, **extra}
    if source is not None:
        message["reply_to_message"] = {"message_id": 40, "chat": {"id": CHAT, "type": "supergroup"},
                                       "from": {"id": source_from, "first_name": "Б"}, "text": source}
    handle_update({"update_id": 1, "message": message})


def press(user_id, draft, message_id=None):
    handle_update({"update_id": 2, "callback_query": {
        "id": f"cb{user_id}", "from": {"id": user_id}, "data": f"draft:create:{draft.code}",
        "message": {"message_id": message_id or draft.preview_message_id, "chat": {"id": CHAT, "type": "supergroup"}}}})


def assist_events():
    return list(Event.objects.filter(name="assist_request").order_by("id").values_list("props", flat=True))


def made_draft(bot, anya, content=None):
    with model(reply(content or answer())):
        command(ANYA)
    return MarketDraft.objects.get()


# --- /market ------------------------------------------------------------------------------------

def test_without_a_reply_explains_how_to_use(bot, anya):
    with model() as post:
        command(ANYA, source=None)
    assert bot.texts() == [bot_draft.USAGE]
    assert post.call_count == 0 and not MarketDraft.objects.exists()


def test_reply_to_the_topic_root_is_no_reply(bot, anya):
    with model() as post:
        command(ANYA, source="Тема", is_topic_message=True, message_thread_id=40)
    assert bot.texts() == [bot_draft.USAGE] and post.call_count == 0
    assert bot.sent("sendMessage")[0]["message_thread_id"] == 40


def test_not_a_member_is_refused(bot, group):
    make("Чужой", OUTSIDER)
    with model() as post:
        command(OUTSIDER)
    assert bot.texts() == [bot_draft.NOT_MEMBER]
    assert post.call_count == 0 and not MarketDraft.objects.exists()


def test_without_an_account_leads_to_the_app(bot, group):
    command(STRANGER)
    (sent,) = bot.sent("sendMessage")
    assert sent["text"] == bot_draft.NO_ACCOUNT
    assert sent["reply_markup"]["inline_keyboard"][0][0]["url"] == chats.link(group)


def test_frozen_chat_is_refused(bot, group, anya):
    group.bot_active = False
    group.save()
    with model() as post:
        command(ANYA)
    assert bot.texts() == [FROZEN] and post.call_count == 0 and not MarketDraft.objects.exists()


def test_message_without_text(bot, anya):
    command(ANYA, source="   ")
    assert bot.texts() == [bot_draft.NO_TEXT]


def test_first_call_in_a_chat_warns_and_sends_nothing_to_the_model(bot, group, anya):
    Group.objects.filter(pk=group.pk).update(assist_warned_at=None)
    with model(reply(answer())) as post:
        command(ANYA)
        assert bot.texts() == [bot_draft.WARNING]
        assert post.call_count == 0 and not MarketDraft.objects.exists() and assist_events() == []
        group.refresh_from_db()
        assert group.assist_warned_at is not None
        command(ANYA)
        assert post.call_count == 1
    assert MarketDraft.objects.get().assisted and len(bot.texts()) == 2


def test_warning_not_delivered_is_repeated(bot, group, anya):
    Group.objects.filter(pk=group.pk).update(assist_warned_at=None)
    with patch("market.telegram.api", side_effect=TelegramError("down")):
        command(ANYA)
    group.refresh_from_db()
    assert group.assist_warned_at is None


def test_helper_fills_the_draft_and_the_preview(bot, group, anya):
    with model(reply(answer())) as post:
        command(ANYA)
    draft = MarketDraft.objects.get()
    assert draft.assisted and draft.trader == anya and draft.group == group and draft.question is None
    assert draft.title == QUESTION and draft.category == "study" and draft.criteria.startswith("ДА — если")
    assert draft.options == [] and draft.source_text == SOURCE and draft.source_message_id == 40
    assert timezone.localtime(draft.closes_at, MSK).strftime("%Y-%m-%dT%H:%M") == day(5)
    assert timedelta(hours=23, minutes=59) < draft.expires_at - timezone.now() <= timedelta(hours=24)
    # Only the message text reaches the model.
    sent = json.loads(post.call_args.kwargs["json"]["messages"][1]["content"])
    assert set(sent) == {"question", "category", "today"} and sent["question"] == SOURCE
    assert "Аня" not in json.dumps(post.call_args.kwargs["json"], ensure_ascii=False)

    (preview,) = bot.sent("sendMessage")
    text = preview["text"]
    assert f"«{QUESTION}»" in text and "Как определяем результат:" in text and draft.criteria in text
    assert "Приём прогнозов — до" in text and "Итог —" in text and "Создание в этом чате — 40 монет" in text
    assert preview["reply_parameters"]["message_id"] == 50
    (row,) = preview["reply_markup"]["inline_keyboard"]
    assert row[0] == {"text": "Создать за 40", "callback_data": f"draft:create:{draft.code}"}
    assert row[1] == {"text": "Изменить", "url": f"https://t.me/PredBot/app?startapp=draft_{draft.code}"}
    assert draft.preview_message_id == 901 + bot.calls.index(("sendMessage", preview))
    assert assist_events() == [{"result": "ok", "mode": "chat", "category": "study", "refined": True}]


def test_helper_options_go_to_the_draft(bot, anya):
    draft = made_draft(bot, anya, answer(question="Какая группа выиграет кубок факультета 30 ноября?",
                                         options=["ПИ-21", "ПИ-22", "ПИ-23"]))
    assert draft.options == ["ПИ-21", "ПИ-22", "ПИ-23"]
    assert "Варианты: ПИ-21 · ПИ-22 · ПИ-23" in bot.texts()[0]


def test_chat_price_is_shown(bot, group, anya):
    group.creation_cost = 150
    group.save()
    made_draft(bot, anya)
    (preview,) = bot.sent("sendMessage")
    assert "Создание в этом чате — 150 монет" in preview["text"]
    assert preview["reply_markup"]["inline_keyboard"][0][0]["text"] == "Создать за 150"


def assert_text_only(bot, draft, text, note=None):
    assert not draft.assisted and draft.title == text and draft.criteria == "" and draft.closes_at is None
    (preview,) = bot.sent("sendMessage")
    assert preview["reply_markup"] == {"inline_keyboard": [[
        {"text": "Доделать рынок", "url": f"https://t.me/PredBot/app?startapp=draft_{draft.code}"}]]}
    assert f"«{text}»" in preview["text"] and bot_draft.NO_HELPER_TAIL in preview["text"]
    if note:
        assert preview["text"].startswith(note)


@pytest.mark.parametrize("switch", ["chat", "global", "key"])
def test_helper_switched_off_gives_a_text_only_draft(bot, group, anya, switch):
    Group.objects.filter(pk=group.pk).update(assist_warned_at=None, assist_enabled=switch != "chat")
    flags = {"global": {"ASSIST_ENABLED": False}, "key": {"LLM_API_KEY": ""}}.get(switch, {})
    with override_settings(**flags), model() as post:
        command(ANYA)
    assert post.call_count == 0 and assist_events() == []
    assert_text_only(bot, MarketDraft.objects.get(), SOURCE)
    group.refresh_from_db()
    assert group.assist_warned_at is None  # nothing went to the model, so no warning was needed


def test_helper_error_gives_a_text_only_draft(bot, anya):
    with model(requests.Timeout()):
        command(ANYA)
    assert_text_only(bot, MarketDraft.objects.get(), SOURCE, bot_draft.OFFLINE)
    assert assist_events() == [{"result": "error", "mode": "chat", "reason": "timeout"}]


def test_garbage_from_the_helper_gives_a_text_only_draft(bot, anya):
    with model(reply(answer(options=["Одна"]))):
        command(ANYA)
    assert_text_only(bot, MarketDraft.objects.get(), SOURCE, bot_draft.OFFLINE)
    assert assist_events()[0]["reason"] == "schema"


def test_helper_crash_still_leaves_a_draft(bot, anya):
    with model(reply(answer())), patch("market.assist.check", side_effect=RuntimeError("bug")):
        command(ANYA)
    assert_text_only(bot, MarketDraft.objects.get(), SOURCE, bot_draft.OFFLINE)
    assert assist_events()[0] == {"result": "error", "mode": "chat", "reason": "internal"}


@override_settings(ASSIST_DAILY_LIMIT=1)
def test_daily_limit_is_shared_with_the_wizard(bot, anya):
    Event.objects.create(trader=anya, name="assist_request", props={"result": "ok"})
    with model() as post:
        command(ANYA)
    assert post.call_count == 0
    assert_text_only(bot, MarketDraft.objects.get(), SOURCE, "Подсказки ИИ на сегодня закончились — их 1 в день.")
    assert assist_events()[-1] == {"result": "limit", "mode": "chat"}


def test_forbidden_topic_gives_a_text_only_draft(bot, anya):
    content = {"forbidden": "health", "question": None, "why": None, "options": None, "criteria": None,
               "category": None, "closes_at": None, "resolves_at": None}
    with model(reply(content)):
        command(ANYA)
    assert_text_only(bot, MarketDraft.objects.get(), SOURCE, "Похоже, это рынок о здоровье конкретного человека.")
    assert assist_events() == [{"result": "warning", "mode": "chat", "topic": "health"}]


def test_long_message_is_cut_for_the_title(bot, group, anya):
    group.assist_enabled = False
    group.save()
    command(ANYA, source="Спорим " + "очень " * 40 + "долго")
    draft = MarketDraft.objects.get()
    assert len(draft.title) == 120 and draft.title.endswith("…")


def test_statement_without_a_worded_question_is_a_refusal(bot, anya):
    """A chat message is no question: the model must word one, else the answer is unusable."""
    with model(reply(answer(question=None, why=None))):
        command(ANYA, source="спорим " + "что завтра будет снег и пары отменят, " * 5)
    assert not MarketDraft.objects.get().assisted


# --- Создать ------------------------------------------------------------------------------------

def test_create_only_by_the_caller(bot, anya, boris):
    draft = made_draft(bot, anya)
    press(BORIS, draft)
    assert bot.alerts() == [(bot_draft.ALERT_ONLY_CALLER, True)]
    assert not Question.objects.exists()
    boris.refresh_from_db()
    assert boris.balance == D(1000)


def test_create_makes_the_chat_market_once(bot, group, anya):
    draft = made_draft(bot, anya)
    press(ANYA, draft)
    question = Question.objects.get()
    draft.refresh_from_db()
    anya.refresh_from_db()
    assert draft.question == question and question.group == group and question.creator == anya
    assert question.announce is True and question.title == QUESTION and question.category == "study"
    assert question.request_key == bot_draft.request_key(draft.code)
    assert anya.balance == D(960)
    assert bot.alerts() == [(bot_draft.ALERT_CREATED, False)]
    (edit,) = bot.sent("editMessageText")
    assert edit["chat_id"] == CHAT and edit["message_id"] == draft.preview_message_id
    assert edit["text"] == f"Рынок создан:\n«{QUESTION}»"
    assert edit["reply_markup"]["inline_keyboard"][0][0]["text"] == "Открыть рынок"
    assert Event.objects.get(name="market_create").props == {
        "market": question.pk, "category": "study", "chat": True, "draft": True, "announce": True, "via": "chat"}

    press(ANYA, draft)
    anya.refresh_from_db()
    assert Question.objects.count() == 1 and anya.balance == D(960)
    assert LedgerEntry.objects.filter(trader=anya, kind=LedgerEntry.Kind.CREATION).count() == 1
    assert bot.alerts()[-1] == (bot_draft.ALERT_ALREADY, False)
    assert Event.objects.filter(name="market_create").count() == 1


def test_price_moved_after_preview_charges_nothing_and_updates_the_button(bot, group, anya):
    draft = made_draft(bot, anya)
    assert draft.shown_cost == 40
    Group.objects.filter(pk=group.pk).update(creation_cost=70)
    press(ANYA, draft)
    anya.refresh_from_db()
    draft.refresh_from_db()
    assert anya.balance == D(1000) and not Question.objects.exists() and draft.question_id is None
    (edit,) = bot.sent("editMessageText")
    assert edit["reply_markup"]["inline_keyboard"][0][0]["text"] == "Создать за 70"
    assert draft.shown_cost == 70 and bot.alerts()[-1][1] is True and "70" in bot.alerts()[-1][0]
    press(ANYA, draft)  # the person has seen the new price: this press is the confirmation
    anya.refresh_from_db()
    assert anya.balance == D(930) and Question.objects.get().subsidy == D(70)


def test_price_moved_and_preview_not_updated_keeps_asking(bot, group, anya):
    draft = made_draft(bot, anya)
    Group.objects.filter(pk=group.pk).update(creation_cost=70)
    original = bot.__call__
    def broken_edit(method, **data):
        if method == "editMessageText":
            raise TelegramError("down")
        return original(method, **data)
    with patch("market.telegram.api", side_effect=broken_edit):
        press(ANYA, draft)
    draft.refresh_from_db()
    anya.refresh_from_db()
    assert draft.shown_cost == 40 and anya.balance == D(1000) and not Question.objects.exists()
    assert bot.alerts()[-1] == (bot_draft.ALERT_FAILED, True)


def test_price_moved_by_chat_size_is_caught_too(bot, group, anya):
    draft = made_draft(bot, anya)
    bot.member_count = 20  # crossed the tier: 40 → 70, the size comes from Telegram at the press
    press(ANYA, draft)
    anya.refresh_from_db()
    assert anya.balance == D(1000) and not Question.objects.exists()
    assert bot.sent("editMessageText")[0]["reply_markup"]["inline_keyboard"][0][0]["text"] == "Создать за 70"


def test_create_with_options(bot, anya):
    draft = made_draft(bot, anya, answer(question="Какая группа выиграет кубок факультета 30 ноября?",
                                         options=["ПИ-21", "ПИ-22", "ПИ-23"]))
    press(ANYA, draft)
    root = Question.objects.get(parent__isnull=True)
    assert sorted(root.submarkets.values_list("option_label", flat=True)) == ["ПИ-21", "ПИ-22", "ПИ-23"]


def test_create_does_not_wait_for_premoderation(bot, anya):
    draft = made_draft(bot, anya)
    with override_settings(PREMODERATION_ENABLED=True):
        press(ANYA, draft)
    question = Question.objects.get()
    assert question.publication_state == "approved" and question.background_check == "pending"
    (edit,) = bot.sent("editMessageText")
    assert not edit["text"].startswith("Рынок создан, проверяем…")


def test_not_enough_coins_is_an_alert_to_the_caller(bot, anya):
    draft = made_draft(bot, anya)
    Trader.objects.filter(pk=anya.pk).update(balance=D(10))
    press(ANYA, draft)
    assert bot.alerts() == [("Создание рынка стоит 40 монет — на балансе не хватает.", True)]
    draft.refresh_from_db()
    assert draft.question is None and not Question.objects.exists() and not bot.sent("editMessageText")


@override_settings(MARKETS_PER_DAY=0)
def test_daily_market_limit_is_an_alert(bot, anya):
    draft = made_draft(bot, anya)
    press(ANYA, draft)
    assert bot.alerts() == [("Не больше 0 новых рынков в день.", True)]


def test_expired_draft(bot, anya):
    draft = made_draft(bot, anya)
    MarketDraft.objects.filter(pk=draft.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
    press(ANYA, draft)
    assert bot.alerts() == [(bot_draft.ALERT_EXPIRED, True)] and not Question.objects.exists()


def test_market_made_in_the_app_is_not_made_again(bot, anya):
    draft = made_draft(bot, anya)
    now = timezone.now()
    from market import services
    question = services.create_market(anya, title="Перенесут ли коллоквиум?", criteria="Объявление в чате",
                                      category="study", closes_at=now + timedelta(days=2),
                                      resolves_at=now + timedelta(days=3), group=draft.group)
    MarketDraft.objects.filter(pk=draft.pk).update(question=question)
    press(ANYA, draft)
    anya.refresh_from_db()
    assert Question.objects.count() == 1 and anya.balance == D(960)
    assert bot.alerts() == [(bot_draft.ALERT_ALREADY, False)]


def test_text_only_draft_has_no_create(bot, group, anya):
    group.assist_enabled = False
    group.save()
    command(ANYA)
    draft = MarketDraft.objects.get()
    press(ANYA, draft, message_id=1)
    assert bot.alerts() == [(bot_draft.ALERT_NO_HELPER, True)] and not Question.objects.exists()


def test_left_the_chat_cannot_create(bot, group, anya):
    draft = made_draft(bot, anya)
    GroupMembership.objects.filter(trader=anya).update(status="left")
    press(ANYA, draft)
    assert bot.alerts() == [(bot_draft.NOT_MEMBER, True)] and not Question.objects.exists()


def test_unknown_or_foreign_chat_draft(bot, anya):
    draft = made_draft(bot, anya)
    handle_update({"update_id": 3, "callback_query": {
        "id": "x", "from": {"id": ANYA}, "data": "draft:create:nope",
        "message": {"message_id": 1, "chat": {"id": CHAT}}}})
    handle_update({"update_id": 4, "callback_query": {
        "id": "y", "from": {"id": ANYA}, "data": f"draft:create:{draft.code}",
        "message": {"message_id": 1, "chat": {"id": -1}}}})
    assert bot.alerts() == [(bot_draft.ALERT_NOT_FOUND, True)] * 2 and not Question.objects.exists()
