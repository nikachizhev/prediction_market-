"""Spec E4 in the bot: the creator states the outcome with a photo, participants can dispute it."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from market import services
from market.models import Dispute, LedgerEntry, Question, ResolutionDraft, Trader
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
            patch("market.telegram.requests.get", return_value=download) as get:
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


def test_creator_full_flow(bot, people, market):
    handle_update(text(CREATOR, f"/start resolve_{market.pk}"))
    ask = bot.sent("sendMessage", CREATOR)[-1]
    assert "Чем закончилось?" in ask["text"]
    assert ask["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"res:{market.pk}:yes"

    handle_update(press(CREATOR, f"res:{market.pk}:yes"))
    assert bot.sent("answerCallbackQuery")
    assert bot.sent("editMessageReplyMarkup", CREATOR)[-1]["message_id"] == 9
    asked = bot.sent("sendMessage", CREATOR)[-1]
    assert asked["text"] == ("Итог: <b>ДА</b>. Пришли фото или скриншот, который это подтверждает — "
                             "его увидят все участники.") and asked["parse_mode"] == "HTML"

    handle_update(text(CREATOR, "вот"))
    assert bot.texts(CREATOR)[-1] == "Нажми скрепку и выбери фото"

    handle_update(photo(CREATOR))
    assert bot.sent("getFile")[-1]["file_id"] == "big"
    assert bot.get.call_args.args[0] == "https://api.telegram.org/file/botT/photos/file_7.jpg"
    assert bot.get.call_args.kwargs["timeout"]
    preview = bot.sent("sendPhoto", CREATOR)[-1]
    assert preview["photo"] == "big" and preview["caption"].startswith("Итог: <b>ДА</b>")
    buttons = [b["text"] for b in preview["reply_markup"]["inline_keyboard"][0]]
    assert buttons == ["Подтвердить", "Изменить"]
    market.refresh_from_db()
    assert market.resolution_state == ""  # nothing published before confirmation

    handle_update(press(CREATOR, f"resok:{market.pk}", 10))
    market.refresh_from_db()
    assert market.resolution_state == Question.Resolution.PENDING and market.proposed_outcome == "yes"
    assert market.evidence_file_id == "big" and market.evidence_note == "Скрин с сайта"
    assert market.evidence.read() == b"\xff\xd8fake-jpeg"
    assert not ResolutionDraft.objects.exists()
    done = bot.sent("sendMessage", CREATOR)[-1]
    fee = int(market.fee_pool / 2)
    assert done["text"] == (f"Готово. Итог <b>ДА</b> опубликован вместе с фото. У участников есть 24 часа, "
                            f"чтобы оспорить его. Если спора не будет, выплаты придут автоматически, "
                            f"а тебе — половина комиссии: <b>+{fee}</b>.")
    assert done["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"] == f"https://example.test/app/market/{market.pk}"

    for user in (ANYA, BORIS):
        note = bot.sent("sendPhoto", user)[-1]
        assert note["photo"] == "big" and "<b>ДА</b>" in note["caption"]
        assert note["reply_markup"]["inline_keyboard"][0][0] == {"text": "Оспорить", "callback_data": f"disp:{market.pk}"}
    assert not bot.sent(chat_id=STRANGER)
    assert len(bot.sent("sendPhoto", CREATOR)) == 1  # only the preview


def test_double_confirm_is_harmless(bot, people, market):
    handle_update(press(CREATOR, f"res:{market.pk}:no"))
    handle_update(photo(CREATOR))
    handle_update(press(CREATOR, f"resok:{market.pk}", 10))
    calls = len(bot.calls)
    handle_update(press(CREATOR, f"resok:{market.pk}", 10))
    market.refresh_from_db()
    assert market.resolution_state == "pending" and market.proposed_outcome == "no"
    new = bot.calls[calls:]
    assert not [d for m, d in new if m in ("sendMessage", "sendPhoto")]
    assert [d for m, d in new if m == "answerCallbackQuery"][0]["text"] == "Итог уже опубликован."
    # After publishing, the outcome buttons explain why nothing happens.
    handle_update(press(CREATOR, f"res:{market.pk}:yes"))
    assert bot.texts(CREATOR)[-1] == "Итог этого рынка уже указан."


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


def test_cancel_without_photo(bot, people, market):
    handle_update(press(CREATOR, f"res:{market.pk}:cancel"))
    offer = bot.sent("sendMessage", CREATOR)[-1]
    assert offer["reply_markup"]["inline_keyboard"][0][0] == {
        "text": "Без доказательства", "callback_data": f"resnone:{market.pk}"}
    handle_update(press(CREATOR, f"resnone:{market.pk}", 10))
    preview = bot.sent("sendMessage", CREATOR)[-1]
    assert "Подтвердить" in str(preview["reply_markup"])
    handle_update(press(CREATOR, f"resok:{market.pk}", 11))
    market.refresh_from_db()
    assert market.resolution_state == "pending" and market.proposed_outcome == "cancel" and not market.evidence
    assert "Отмена рынка опубликована" in bot.texts(CREATOR)[-1]
    assert "оспорь" in bot.texts(ANYA)[-1] and not bot.sent("sendPhoto", ANYA)


def test_change_outcome_from_preview(bot, people, market):
    handle_update(press(CREATOR, f"res:{market.pk}:yes"))
    handle_update(photo(CREATOR))
    handle_update(press(CREATOR, f"resedit:{market.pk}", 10))
    assert not ResolutionDraft.objects.exists()
    assert "Чем закончилось?" in bot.texts(CREATOR)[-1]
    handle_update(press(CREATOR, f"res:{market.pk}:no", 11))
    assert ResolutionDraft.objects.get().outcome == "no"


def test_image_document_is_accepted(bot, people, market):
    handle_update(press(CREATOR, f"res:{market.pk}:yes"))
    doc = text(CREATOR, "")
    del doc["message"]["text"]
    doc["message"]["document"] = {"file_id": "doc1", "mime_type": "image/png"}
    handle_update(doc)
    assert bot.sent("sendDocument", CREATOR)[-1]["document"] == "doc1"
    handle_update(press(CREATOR, f"resok:{market.pk}", 10))
    assert bot.sent("sendDocument", ANYA)


def test_dispute_by_participant(bot, people, market):
    _, anya, _, stranger = people
    handle_update(press(CREATOR, f"res:{market.pk}:yes"))
    handle_update(photo(CREATOR))
    handle_update(press(CREATOR, f"resok:{market.pk}", 10))
    balance = Trader.objects.get(pk=anya.pk).balance

    handle_update(press(ANYA, f"disp:{market.pk}", 20))
    assert bot.texts(ANYA)[-1] == "Спор стоит 20 монет; вернём их, если модератор признает спор. Оспорить?"
    assert not Dispute.objects.exists()
    handle_update(press(ANYA, f"dispok:{market.pk}", 21))
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


def test_dispute_declined(bot, people, market):
    handle_update(press(CREATOR, f"res:{market.pk}:cancel"))
    handle_update(press(CREATOR, f"resnone:{market.pk}", 10))
    handle_update(press(CREATOR, f"resok:{market.pk}", 11))
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
    assert methods == ["setChatMenuButton", "setMyCommands"]
    assert bot.calls[0][1]["menu_button"] == {
        "type": "web_app", "text": "Открыть приложение", "web_app": {"url": "https://example.test/app/"}}
    assert bot.calls[1][1]["commands"][0]["command"] == "start"


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
