"""Spec E4 in the bot: the creator states the outcome in one tap, a photo is optional; participants can dispute."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from market.tests.bot_job_helpers import drain_jobs

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from market import services
from market.models import BotJob, Dispute, Group, GroupMembership, LedgerEntry, Question, ResolutionDraft, Trader
from market.telegram import handle_update

D = Decimal
CREATOR, ANYA, BORIS, STRANGER = 1001, 1002, 1003, 1004


def make(name, chat, balance=1000):
    return Trader.objects.create(name=name, pseudonym=name, balance=D(balance), telegram_chat_id=chat)


class Bot:
    """Records Bot API calls instead of going to the network."""

    def __init__(self):
        self.calls = []

    def __call__(self, method, **data):
        self.calls.append((method, data))
        if method == "getFile":
            return {"file_id": data["file_id"], "file_path": "photos/file_7.jpg", "file_size": 12}
        return {"message_id": len(self.calls)}

    def sent(self, method=None, chat_id=None):
        return [d for m, d in self.calls
                if (method is None or m == method) and (chat_id is None or d.get("chat_id") == chat_id)]

    def texts(self, chat_id):
        return [d.get("text") or d.get("caption") or "" for d in self.sent(chat_id=chat_id)]


@pytest.fixture(autouse=True)
def media(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path, SITE_URL="https://example.test", TELEGRAM_BOT_TOKEN="T"):
        yield


@pytest.fixture
def bot():
    fake = Bot()
    download = MagicMock(ok=True, content=b"\xff\xd8fake-jpeg")
    with patch("market.telegram.api", side_effect=fake), \
            patch("requests.Session.get", return_value=download) as get:
        fake.get = get
        yield fake


@pytest.fixture
def people(db):
    return make("Автор", CREATOR), make("Аня", ANYA), make("Борис", BORIS), make("Чужой", STRANGER)


@pytest.fixture
def market(people):
    creator, anya, boris, _ = people
    now = timezone.now()
    q = services.create_market(creator, title="Снег до декабря?", criteria="Росгидромет", category="fun",
                               closes_at=now + timedelta(hours=2), resolves_at=now + timedelta(hours=3))
    services.buy(anya, q, "yes", 100)
    services.buy(boris, q, "no", 50)
    Question.objects.filter(pk=q.pk).update(deadline=now - timedelta(minutes=1), resolves_at=now - timedelta(minutes=1))
    q.refresh_from_db()
    return q


def text(user, body):
    return {"message": {"message_id": 5, "chat": {"id": user, "type": "private"}, "from": {"id": user}, "text": body}}


def photo(user, caption="Скрин с сайта"):
    return {"message": {"message_id": 6, "chat": {"id": user, "type": "private"}, "from": {"id": user},
                        "caption": caption,
                        "photo": [{"file_id": "small", "width": 90, "height": 90},
                                  {"file_id": "big", "width": 1280, "height": 960}]}}


def press(user, data, message_id=9):
    return {"callback_query": {"id": f"cb{message_id}", "from": {"id": user}, "data": data,
                               "message": {"message_id": message_id, "chat": {"id": user, "type": "private"}}}}


def test_one_tap_states_the_outcome_without_a_photo(bot, people, market):
    handle_update(text(CREATOR, f"/start resolve_{market.pk}"))
    ask = bot.sent("sendMessage", CREATOR)[-1]
    assert "Чем закончилось?" in ask["text"]
    rows = ask["reply_markup"]["inline_keyboard"]
    assert [[b["text"] for b in row] for row in rows] == [["ДА", "НЕТ"], ["Приложить фото"], ["Отменить рынок"]]
    assert rows[0][0]["callback_data"] == f"res:{market.pk}:yes"

    before = len(bot.calls)
    handle_update(press(CREATOR, f"res:{market.pk}:yes"))
    assert bot.calls[before][0] == "answerCallbackQuery"  # acknowledged first
    assert bot.sent("editMessageReplyMarkup", CREATOR)[-1]["message_id"] == 9
    market.refresh_from_db()
    assert market.resolution_state == Question.Resolution.PENDING and market.proposed_outcome == "yes"
    assert market.proposed_by_id == people[0].pk and market.proposal_bond == 0
    assert not market.evidence and not market.evidence_file_id
    assert not ResolutionDraft.objects.exists()
    done = bot.sent("sendMessage", CREATOR)[-1]
    fee = int(market.fee_pool / 2)
    assert done["text"] == (f"Готово. Итог <b>ДА</b> опубликован. У участников есть 24 часа, "
                            f"чтобы оспорить его. Если спора не будет, выплаты придут автоматически, "
                            f"а тебе — половина комиссии: <b>+{fee}</b>.")
    assert done["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"] == f"https://example.test/app/market/{market.pk}"

    assert not bot.sent(chat_id=ANYA)  # queued, not sent from the poller
    drain_jobs()
    for user in (ANYA, BORIS):
        note = bot.sent("sendMessage", user)[-1]
        assert "указал итог: <b>ДА</b>" in note["text"]
        assert note["reply_markup"]["inline_keyboard"][0][0] == {"text": "Оспорить", "callback_data": f"disp:{market.pk}"}
    assert not bot.sent("sendPhoto") and not bot.sent(chat_id=STRANGER)


def test_photo_before_the_tap_goes_with_the_outcome(bot, people, market):
    handle_update(press(CREATOR, f"resphoto:{market.pk}"))
    assert "Пришли фото" in bot.texts(CREATOR)[-1]
    handle_update(text(CREATOR, "вот"))
    assert bot.texts(CREATOR)[-1] == "Нажми скрепку и выбери фото"

    handle_update(photo(CREATOR))
    assert bot.sent("getFile")[-1]["file_id"] == "big"
    assert bot.get.call_args.args[0] == "https://api.telegram.org/file/botT/photos/file_7.jpg"
    assert bot.get.call_args.kwargs["timeout"]
    asked = bot.sent("sendMessage", CREATOR)[-1]
    assert "Фото приложено" in asked["text"] and "Чем закончилось?" in asked["text"]
    assert asked["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"res:{market.pk}:yes"
    market.refresh_from_db()
    assert market.resolution_state == ""  # the photo alone states nothing

    handle_update(press(CREATOR, f"res:{market.pk}:yes", 10))
    market.refresh_from_db()
    assert market.resolution_state == Question.Resolution.PENDING and market.proposed_outcome == "yes"
    assert market.evidence_file_id == "big" and market.evidence_note == "Скрин с сайта"
    assert market.evidence.read() == b"\xff\xd8fake-jpeg"
    assert not ResolutionDraft.objects.exists()
    assert "опубликован вместе с фото" in bot.texts(CREATOR)[-1]
    drain_jobs()
    for user in (ANYA, BORIS):
        note = bot.sent("sendPhoto", user)[-1]
        assert note["photo"] == "big" and "<b>ДА</b>" in note["caption"]
        assert note["reply_markup"]["inline_keyboard"][0][0] == {"text": "Оспорить", "callback_data": f"disp:{market.pk}"}
    assert not bot.sent("sendPhoto", CREATOR)


def test_double_tap_is_harmless(bot, people, market):
    handle_update(press(CREATOR, f"res:{market.pk}:no"))
    calls = len(bot.calls)
    balances = list(Trader.objects.order_by("pk").values_list("balance", flat=True))
    handle_update(press(CREATOR, f"res:{market.pk}:no"))
    market.refresh_from_db()
    assert market.resolution_state == "pending" and market.proposed_outcome == "no"
    assert not [d for m, d in bot.calls[calls:] if m != "answerCallbackQuery"]  # silent
    assert BotJob.objects.count() == 2
    assert list(Trader.objects.order_by("pk").values_list("balance", flat=True)) == balances
    # Another button after publishing explains why nothing happens.
    handle_update(press(CREATOR, f"res:{market.pk}:yes"))
    assert bot.texts(CREATOR)[-1] == "Итог этого рынка уже указан."
    market.refresh_from_db()
    assert market.proposed_outcome == "no"


def test_stranger_cannot_state_outcome(bot, people, market):
    handle_update(text(ANYA, f"/start resolve_{market.pk}"))
    assert bot.texts(ANYA)[-1] == "Итог указывает только создатель рынка."
    handle_update(press(ANYA, f"res:{market.pk}:yes"))
    assert bot.texts(ANYA)[-1] == "Итог указывает только создатель рынка."
    assert not ResolutionDraft.objects.exists()
    # A forged callback from someone else's chat is ignored.
    forged = press(ANYA, f"resok:{market.pk}")
    forged["callback_query"]["message"]["chat"]["id"] = CREATOR
    handle_update(forged)
    market.refresh_from_db()
    assert market.resolution_state == ""


def test_too_early_and_unknown_market(bot, people):
    creator = people[0]
    now = timezone.now()
    q = services.create_market(creator, title="Рано?", criteria="c", category="tech",
                               closes_at=now + timedelta(hours=2), resolves_at=now + timedelta(hours=3))
    handle_update(text(CREATOR, f"/start resolve_{q.pk}"))
    assert "Приём прогнозов ещё идёт" in bot.texts(CREATOR)[-1]
    handle_update(text(CREATOR, "/start resolve_999999"))
    assert bot.texts(CREATOR)[-1] == "Рынок не найден."


def test_cancel_in_one_tap(bot, people, market):
    handle_update(press(CREATOR, f"res:{market.pk}:cancel"))
    market.refresh_from_db()
    assert market.resolution_state == "pending" and market.proposed_outcome == "cancel" and not market.evidence
    assert "Отмена рынка опубликована" in bot.texts(CREATOR)[-1]
    drain_jobs()
    assert "оспорь" in bot.texts(ANYA)[-1] and not bot.sent("sendPhoto", ANYA)


def test_buttons_from_before_phase_3d_show_the_new_ones(bot, people, market):
    ResolutionDraft.objects.create(trader=people[0], question=market, outcome="yes", stage="preview")
    handle_update(press(CREATOR, f"resok:{market.pk}"))
    market.refresh_from_db()
    assert market.resolution_state == ""
    assert "Чем закончилось?" in bot.texts(CREATOR)[-1]
    handle_update(press(CREATOR, f"res:{market.pk}:no", 11))
    market.refresh_from_db()
    assert market.proposed_outcome == "no"


def test_image_document_is_accepted(bot, people, market):
    handle_update(press(CREATOR, f"resphoto:{market.pk}"))
    doc = text(CREATOR, "")
    del doc["message"]["text"]
    doc["message"]["document"] = {"file_id": "doc1", "mime_type": "image/png"}
    handle_update(doc)
    handle_update(press(CREATOR, f"res:{market.pk}:yes", 10))
    drain_jobs()
    assert bot.sent("sendDocument", ANYA)[-1]["document"] == "doc1"


def test_dispute_by_participant(bot, people, market):
    _, anya, _, stranger = people
    handle_update(press(CREATOR, f"res:{market.pk}:yes"))
    balance = Trader.objects.get(pk=anya.pk).balance

    handle_update(press(ANYA, f"disp:{market.pk}", 20))
    assert bot.texts(ANYA)[-1] == "Спор стоит 20 монет; вернём их, если модератор признает спор. Оспорить?"
    assert not Dispute.objects.exists()
    handle_update(press(ANYA, f"dispok:{market.pk}", 21))
    assert bot.texts(ANYA)[-1] == "Спор открыт, списано 20 монет. Итог проверит модератор."
    assert Trader.objects.get(pk=anya.pk).balance == balance - 20
    assert LedgerEntry.objects.filter(trader=anya, kind=LedgerEntry.Kind.DISPUTE).exists()
    market.refresh_from_db()
    assert market.resolution_state == Question.Resolution.DISPUTED
    handle_update(press(ANYA, f"dispok:{market.pk}", 21))
    assert bot.texts(ANYA)[-1] == "Вы уже оспорили этот итог."
    assert Trader.objects.get(pk=anya.pk).balance == balance - 20

    handle_update(press(STRANGER, f"disp:{market.pk}", 30))
    handle_update(press(STRANGER, f"dispok:{market.pk}", 31))
    assert bot.texts(STRANGER)[-1] == "Оспорить итог может только участник рынка."
    assert Trader.objects.get(pk=stranger.pk).balance == 1000
    assert Dispute.objects.count() == 1


def test_dispute_on_chat_market_names_chat_admins(bot, people, market):
    chat = Group.objects.create(name="Группа ПИ-24", kind="chat", telegram_chat_id=-100777, bot_active=True)
    for trader in people:
        GroupMembership.objects.create(group=chat, trader=trader, status="active", checked_at=timezone.now())
    Question.objects.filter(pk=market.pk).update(group=chat)
    handle_update(press(CREATOR, f"res:{market.pk}:yes"))

    handle_update(press(ANYA, f"disp:{market.pk}", 20))
    assert bot.texts(ANYA)[-1] == "Спор стоит 20 монет; вернём их, если админы чата признают спор. Оспорить?"
    handle_update(press(ANYA, f"dispok:{market.pk}", 21))
    assert bot.texts(ANYA)[-1] == "Спор открыт, списано 20 монет. Итог проверят админы чата."
    assert Dispute.objects.filter(question=market).count() == 1


def test_dispute_declined(bot, people, market):
    handle_update(press(CREATOR, f"res:{market.pk}:cancel"))
    handle_update(press(ANYA, f"dispno:{market.pk}", 20))
    assert not Dispute.objects.exists()
    assert bot.sent("editMessageReplyMarkup", ANYA)


@pytest.mark.django_db
def test_start_for_new_person_opens_app(bot):
    handle_update(text(555, "/start"))
    reply = bot.sent("sendMessage", 555)[-1]
    assert reply["reply_markup"]["inline_keyboard"][0][0] == {
        "text": "Открыть приложение", "web_app": {"url": "https://example.test/app/"}}
    handle_update(text(555, "что тут?"))
    assert bot.sent("sendMessage", 555)[-1]["reply_markup"]["inline_keyboard"][0][0]["web_app"]


@pytest.mark.django_db
def test_setup_bot(bot):
    call_command("setup_bot")
    methods = [m for m, _ in bot.calls]
    assert methods == ["setChatMenuButton", "setMyCommands", "setMyCommands"]
    assert bot.calls[0][1]["menu_button"] == {
        "type": "web_app", "text": "Открыть приложение", "web_app": {"url": "https://example.test/app/"}}
    assert bot.calls[2][1]["commands"][0]["command"] == "start"


@pytest.mark.django_db
def test_start_dispute_link_asks_for_confirmation(monkeypatch):
    from market import telegram as tg
    sent = []
    monkeypatch.setattr(tg, "send", lambda chat_id, text, **kw: sent.append((chat_id, text, kw)))
    from market.models import Question
    q = Question.objects.create(title="Q")
    tg.handle_update({"update_id": 1, "message": {"chat": {"id": 77, "type": "private"}, "from": {"id": 77},
                                                  "text": f"/start dispute_{q.pk}"}})
    assert sent and "Оспорить?" in sent[0][1]
    assert sent[0][2]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"dispok:{q.pk}"
