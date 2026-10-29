"""Spec E8.6: disputes on chat markets go to the chat's admins, the first decision is final, silence -> team."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import override_settings
from django.utils import timezone

from market import bot_notifications, chat_disputes, chats, services
from market.admin import ChatDisputeReviewAdmin, QuestionAdmin
from market.models import (ChatDisputeReview, Dispute, Group, GroupMembership, LedgerEntry, Notification, Question,
                           Trader)
from market.telegram import TelegramError

D = Decimal
CHAT_ID = -100777
CREATOR, ANYA, BORIS = 1001, 1002, 1003
ADMIN1, ADMIN2, NO_ACCOUNT, BOT_ID = 2001, 2002, 2003, 9999


class Bot:
    """Fake Bot API: a list of chat admins, people who never started the bot, recorded calls."""

    def __init__(self, admin_ids):
        self.admin_ids = list(admin_ids)
        self.unreachable: set[int] = set()
        self.admins_fail = False
        self.calls = []

    def __call__(self, method, **data):
        self.calls.append((method, data))
        if method == "getChatAdministrators":
            if self.admins_fail:
                raise TelegramError("down")
            return [{"status": "administrator", "user": {"id": uid, "is_bot": uid == BOT_ID, "first_name": "x"}}
                    for uid in self.admin_ids]
        if method.startswith("send") and data.get("chat_id") in self.unreachable:
            raise TelegramError("Forbidden: bot can't initiate conversation with a user")
        return {"message_id": len(self.calls)}

    def sent(self, method=None, chat_id=None):
        return [d for m, d in self.calls
                if (method is None or m == method) and (chat_id is None or d.get("chat_id") == chat_id)]

    def answers(self):
        return [d.get("text") for m, d in self.calls if m == "answerCallbackQuery"]


@pytest.fixture(autouse=True)
def env():
    with override_settings(SITE_URL="https://example.test", TELEGRAM_BOT_TOKEN="T", CHAT_DISPUTE_HOURS=48):
        yield


def make(name, tg, balance=1000):
    return Trader.objects.create(name=name, pseudonym=f"Псевдо {name}", balance=D(balance), telegram_chat_id=tg)


@pytest.fixture
def people(db):
    return {"creator": make("Автор", CREATOR), "anya": make("Аня", ANYA), "boris": make("Борис", BORIS),
            "admin1": make("Ольга", ADMIN1), "admin2": make("Пётр", ADMIN2)}


@pytest.fixture
def group(people):
    g = Group.objects.create(name="Группа ПИ-24", kind="chat", telegram_chat_id=CHAT_ID, code=chats.new_code(),
                             bot_active=True, member_count=30)
    for trader in people.values():
        GroupMembership.objects.create(group=g, trader=trader, status="active", checked_at=timezone.now())
    return g


def disputed_market(people, group, *, outcome="yes", reason="Матч перенесли, итога ещё нет"):
    now = timezone.now()
    q = services.create_market(people["creator"], title="Сдаст ли Петя матан?", criteria="По ведомости деканата",
                               category="study", closes_at=now + timedelta(hours=2),
                               resolves_at=now + timedelta(hours=3), group=group)
    services.buy(people["anya"], q, "yes", 100)
    services.buy(people["boris"], q, "no", 50)
    Question.objects.filter(pk=q.pk).update(deadline=now - timedelta(minutes=1), resolves_at=now - timedelta(minutes=1))
    q.refresh_from_db()
    services.propose_resolution(people["creator"], q, outcome, evidence_file_id="PHOTO-1", note="Скрин ведомости")
    services.open_dispute(people["anya"], q, reason=reason)
    q.refresh_from_db()
    return q


@pytest.fixture
def market(people, group):
    return disputed_market(people, group)


def run_tick(bot, now=None):
    with patch("market.telegram.api", side_effect=bot):
        return chat_disputes.tick(now or timezone.now())


def press(bot, user, data, message_id=50):
    callback = {"id": f"cb{len(bot.calls)}", "from": {"id": user}, "data": data,
                "message": {"message_id": message_id, "chat": {"id": user, "type": "private"}}}
    with patch("market.telegram.api", side_effect=bot):
        chat_disputes.handle_callback(callback)


def cards(bot, chat_id):
    return [d for d in bot.sent("sendMessage", chat_id) if "reply_markup" in d]


def buttons(markup):
    return [b["text"] for row in markup["inline_keyboard"] for b in row]


# --- who gets the dispute ---------------------------------------------------------------------------

def test_sent_only_to_eligible_admins(people, market):
    # Creator is an admin, Boris is an admin with a position, a bot and someone without an account too.
    bot = Bot([CREATOR, BORIS, ADMIN1, ADMIN2, NO_ACCOUNT, BOT_ID])
    assert run_tick(bot) == {"sent": 1, "escalated": 0}
    receivers = {d["chat_id"] for d in bot.sent("sendMessage")}
    assert receivers == {ADMIN1, ADMIN2}
    review = ChatDisputeReview.objects.get(question=market)
    assert {m["chat_id"] for m in review.messages} == {ADMIN1, ADMIN2}
    assert review.group_id == market.group_id and review.escalated_at is None
    assert review.deadline - review.created_at > timedelta(hours=47)


def test_card_shows_rules_proof_and_comment_without_names(people, market):
    bot = Bot([ADMIN1])
    run_tick(bot)
    photo = bot.sent("sendPhoto", ADMIN1)
    assert len(photo) == 1 and photo[0]["photo"] == "PHOTO-1"
    card = cards(bot, ADMIN1)[0]
    text = card["text"]
    assert "Сдаст ли Петя матан?" in text and "По ведомости деканата" in text
    assert "Итог создателя: <b>ДА</b>" in text and "Скрин ведомости" in text
    assert "«Матч перенесли, итога ещё нет»" in text
    for person in ("anya", "boris"):
        assert people[person].name not in text and people[person].pseudonym not in text
    assert "Ставк" not in text and "100" not in text
    assert buttons(card["reply_markup"]) == ["Итог верный", "Итог неверный", "Открыть рынок"]


def test_image_sent_as_file_falls_back_to_document(people, market):
    bot = Bot([ADMIN1])
    original = bot.__call__

    def reject_photo(method, **data):
        if method == "sendPhoto":
            bot.calls.append((method, data))
            raise TelegramError("can't use file of type Document as Photo")
        return original(method, **data)

    with patch("market.telegram.api", side_effect=reject_photo):
        chat_disputes.tick(timezone.now())
    assert bot.sent("sendDocument", ADMIN1)[0]["document"] == "PHOTO-1"
    assert len(cards(bot, ADMIN1)) == 1


def test_tick_is_idempotent(people, market):
    bot = Bot([ADMIN1])
    run_tick(bot)
    run_tick(bot)
    assert ChatDisputeReview.objects.count() == 1
    assert len(cards(bot, ADMIN1)) == 1


def test_wrong_offers_other_outcomes_and_back(people, market):
    bot = Bot([ADMIN1])
    run_tick(bot)
    review = ChatDisputeReview.objects.get()
    press(bot, ADMIN1, f"cdis:{review.pk}:wrong")
    markup = bot.sent("editMessageReplyMarkup", ADMIN1)[-1]["reply_markup"]
    assert buttons(markup) == ["НЕТ", "Отменить рынок", "Назад"]
    press(bot, ADMIN1, f"cdis:{review.pk}:back")
    assert buttons(bot.sent("editMessageReplyMarkup", ADMIN1)[-1]["reply_markup"])[:2] == ["Итог верный", "Итог неверный"]
    market.refresh_from_db()
    assert market.resolution_state == Question.Resolution.DISPUTED


# --- decisions and money -----------------------------------------------------------------------------

def test_outcome_right_creator_wins_and_stake_burns(people, market):
    bot = Bot([ADMIN1, ADMIN2])
    run_tick(bot)
    review = ChatDisputeReview.objects.get()
    anya_before = Trader.objects.get(pk=people["anya"].pk).balance
    press(bot, ADMIN1, f"cdis:{review.pk}:ok")
    market.refresh_from_db()
    review.refresh_from_db()
    assert market.status == Question.Status.RESOLVED and market.outcome == "yes" and not market.creator_at_fault
    assert review.decision == "yes" and review.decided_by_id == people["admin1"].pk and review.decided_at
    assert Dispute.objects.get().upheld is False
    assert not LedgerEntry.objects.filter(kind=LedgerEntry.Kind.DISPUTE_REFUND).exists()
    assert LedgerEntry.objects.filter(trader=people["creator"], kind=LedgerEntry.Kind.SUBSIDY_RETURN).exists()
    # Anya held YES: paid out, but her 20 coins for the dispute are gone.
    assert Trader.objects.get(pk=people["anya"].pk).balance > anya_before
    assert bot.answers()[-1] == "Решение принято"
    edits = {d["chat_id"]: d["text"] for d in bot.sent("editMessageText")}
    assert edits[ADMIN1].endswith("Ты решил: итог создателя верный (ДА). Решение окончательное.")
    assert edits[ADMIN2].endswith("Спор решён. Псевдо Ольга: итог создателя верный (ДА).")


@pytest.mark.parametrize("choice, outcome", [("no", "no"), ("cancel", None)])
def test_outcome_wrong_refunds_stake_and_creator_loses_subsidy(people, market, choice, outcome):
    bot = Bot([ADMIN1])
    run_tick(bot)
    review = ChatDisputeReview.objects.get()
    anya = Trader.objects.get(pk=people["anya"].pk)
    creator_before = Trader.objects.get(pk=people["creator"].pk).balance
    press(bot, ADMIN1, f"cdis:{review.pk}:wrong")
    press(bot, ADMIN1, f"cdis:{review.pk}:{choice}")
    market.refresh_from_db()
    assert market.creator_at_fault and market.resolution_state == Question.Resolution.FINAL
    if outcome:
        assert market.status == Question.Status.RESOLVED and market.outcome == outcome
    else:
        assert market.status == Question.Status.ANNULLED
    assert Dispute.objects.get().upheld is True
    refund = LedgerEntry.objects.get(trader=anya, kind=LedgerEntry.Kind.DISPUTE_REFUND)
    assert refund.amount == D(20)
    if choice == "no":
        # Anya bet YES: she loses the bet but gets the 20 back.
        assert Trader.objects.get(pk=anya.pk).balance == anya.balance + 20
    assert not LedgerEntry.objects.filter(trader=people["creator"], kind=LedgerEntry.Kind.SUBSIDY_RETURN).exists()
    assert Trader.objects.get(pk=people["creator"].pk).balance <= creator_before + market.fee_pool
    assert ChatDisputeReview.objects.get().decision == choice


def test_first_decision_is_final(people, market):
    bot = Bot([ADMIN1, ADMIN2])
    run_tick(bot)
    review = ChatDisputeReview.objects.get()
    press(bot, ADMIN1, f"cdis:{review.pk}:no")
    balances = {t.pk: t.balance for t in Trader.objects.all()}
    press(bot, ADMIN2, f"cdis:{review.pk}:ok")
    assert bot.answers()[-1] == "Спор решён. Псевдо Ольга: итог неверный, правильный — НЕТ."
    review.refresh_from_db()
    assert review.decision == "no" and review.decided_by_id == people["admin1"].pk
    assert {t.pk: t.balance for t in Trader.objects.all()} == balances


def test_admin_rechecked_on_decision(people, market):
    bot = Bot([ADMIN1, ADMIN2])
    run_tick(bot)
    review = ChatDisputeReview.objects.get()
    bot.admin_ids = [ADMIN2]  # Olga is no longer an admin
    press(bot, ADMIN1, f"cdis:{review.pk}:ok")
    assert bot.answers()[-1] == "Ты больше не админ этого чата — спор решат другие админы."
    market.refresh_from_db()
    assert market.resolution_state == Question.Resolution.DISPUTED
    assert ChatDisputeReview.objects.get().decided_at is None


def test_creator_and_participant_cannot_press(people, market):
    bot = Bot([ADMIN1, CREATOR, BORIS])
    run_tick(bot)
    review = ChatDisputeReview.objects.get()
    press(bot, CREATOR, f"cdis:{review.pk}:ok")
    assert bot.answers()[-1] == "Создатель рынка не решает спор по своему рынку."
    press(bot, BORIS, f"cdis:{review.pk}:no")
    assert bot.answers()[-1] == "У тебя есть прогнозы в этом рынке — спор решат другие админы."
    assert ChatDisputeReview.objects.get().decided_at is None


def test_button_in_a_group_chat_is_ignored(people, market):
    bot = Bot([ADMIN1])
    run_tick(bot)
    review = ChatDisputeReview.objects.get()
    callback = {"id": "x", "from": {"id": ADMIN1}, "data": f"cdis:{review.pk}:ok",
                "message": {"message_id": 1, "chat": {"id": CHAT_ID, "type": "supergroup"}}}
    with patch("market.telegram.api", side_effect=bot):
        chat_disputes.handle_callback(callback)
    assert ChatDisputeReview.objects.get().decided_at is None


# --- escalation to the project team ------------------------------------------------------------------

def test_no_eligible_admins_goes_to_team(people, market):
    bot = Bot([CREATOR, BORIS, BOT_ID, NO_ACCOUNT])
    assert run_tick(bot) == {"sent": 0, "escalated": 1}
    review = ChatDisputeReview.objects.get()
    assert review.escalated_at and review.messages == []
    assert not bot.sent("sendMessage")


def test_telegram_down_goes_to_team(people, market):
    bot = Bot([ADMIN1])
    bot.admins_fail = True
    run_tick(bot)
    assert ChatDisputeReview.objects.get().escalated_at


def test_nobody_reachable_goes_to_team(people, market):
    bot = Bot([ADMIN1, ADMIN2])
    bot.unreachable = {ADMIN1, ADMIN2}
    assert run_tick(bot) == {"sent": 0, "escalated": 1}
    assert ChatDisputeReview.objects.get().escalated_at


def test_one_reachable_admin_is_enough(people, market):
    bot = Bot([ADMIN1, ADMIN2])
    bot.unreachable = {ADMIN1}
    run_tick(bot)
    review = ChatDisputeReview.objects.get()
    assert review.escalated_at is None and [m["chat_id"] for m in review.messages] == [ADMIN2]


def test_silence_for_48_hours_goes_to_team(people, market):
    bot = Bot([ADMIN1, ADMIN2])
    now = timezone.now()
    run_tick(bot, now)
    assert run_tick(bot, now + timedelta(hours=47)) == {"sent": 0, "escalated": 0}
    assert run_tick(bot, now + timedelta(hours=48, minutes=1)) == {"sent": 0, "escalated": 1}
    review = ChatDisputeReview.objects.get()
    assert review.escalated_at
    edits = bot.sent("editMessageText")
    assert {d["chat_id"] for d in edits} == {ADMIN1, ADMIN2}
    assert all(d["text"].endswith("Время вышло — спор решит команда проекта.") for d in edits)
    assert all(buttons(d["reply_markup"]) == ["Открыть рынок"] for d in edits)
    assert run_tick(bot, now + timedelta(hours=50)) == {"sent": 0, "escalated": 0}

    # A late press changes nothing.
    press(bot, ADMIN1, f"cdis:{review.pk}:no")
    assert bot.answers()[-1] == "Спор уже у команды проекта."
    market.refresh_from_db()
    assert market.resolution_state == Question.Resolution.DISPUTED


def test_team_decides_chat_dispute_only_after_escalation(people, market):
    bot = Bot([ADMIN1])
    run_tick(bot)
    with pytest.raises(services.MarketError):
        chat_disputes.team_decide(market, "no")
    review = ChatDisputeReview.objects.get()
    ChatDisputeReview.objects.filter(pk=review.pk).update(escalated_at=timezone.now())
    chat_disputes.team_decide(market, "no")
    market.refresh_from_db()
    review.refresh_from_db()
    assert market.outcome == "no" and market.creator_at_fault
    assert review.decision == "no" and review.decided_by is None
    assert chat_disputes._status_note(review) == "Спор решила команда проекта: итог неверный, правильный — НЕТ."


def test_admin_action_refuses_chat_dispute_before_escalation(people, market, rf):
    bot = Bot([ADMIN1])
    run_tick(bot)
    model_admin = QuestionAdmin(Question, AdminSite())
    shown = []
    model_admin.message_user = lambda request, text, level=None: shown.append(text)
    model_admin.log_change = lambda *a, **k: None
    model_admin.decide_no(rf.post("/"), Question.objects.filter(pk=market.pk))
    assert "админы чата" in shown[0]
    market.refresh_from_db()
    assert market.resolution_state == Question.Resolution.DISPUTED


def test_ordinary_dispute_is_unchanged(people, django_capture_on_commit_callbacks):
    now = timezone.now()
    q = services.create_market(people["creator"], title="Снег до декабря?", criteria="Росгидромет", category="fun",
                               closes_at=now + timedelta(hours=2), resolves_at=now + timedelta(hours=3))
    services.buy(people["anya"], q, "yes", 100)
    Question.objects.filter(pk=q.pk).update(deadline=now - timedelta(minutes=1), resolves_at=now - timedelta(minutes=1))
    q.refresh_from_db()
    services.propose_resolution(people["creator"], q, "yes", evidence_file_id="P")
    services.open_dispute(people["anya"], q)
    bot = Bot([ADMIN1])
    assert run_tick(bot) == {"sent": 0, "escalated": 0}
    assert not ChatDisputeReview.objects.exists() and not bot.calls
    with django_capture_on_commit_callbacks(execute=True):
        chat_disputes.team_decide(q, "no")
    q.refresh_from_db()
    assert q.outcome == "no" and q.creator_at_fault
    bot_notifications.dispute_opened(q.pk)
    assert Notification.objects.get(key=f"dispute-opened:{q.pk}").text == "«Снег до декабря?» — модератор разберёт спор."
    assert Notification.objects.get(key=f"moderator:{q.pk}").title == "Решение модератора:"


# --- texts for the creator ---------------------------------------------------------------------------

def test_creator_texts_for_chat_market(people, market, django_capture_on_commit_callbacks):
    bot_notifications.dispute_opened(market.pk)
    assert Notification.objects.get(key=f"dispute-opened:{market.pk}").text == \
        "«Сдаст ли Петя матан?» — спор решат админы чата."
    bot = Bot([ADMIN1])
    run_tick(bot)
    review = ChatDisputeReview.objects.get()
    with django_capture_on_commit_callbacks(execute=True):
        press(bot, ADMIN1, f"cdis:{review.pk}:no")
    notice = Notification.objects.get(key=f"moderator:{market.pk}")
    assert notice.title == "Решение админов чата:"
    assert notice.text == "«Сдаст ли Петя матан?» — спор признан: итог НЕТ. Монеты за создание рынка не возвращаются."


def test_creator_texts_after_escalation(people, group, django_capture_on_commit_callbacks):
    q = disputed_market(people, group)
    run_tick(Bot([]))
    Notification.objects.all().delete()
    bot_notifications.dispute_opened(q.pk)
    assert Notification.objects.get(key=f"dispute-opened:{q.pk}").text == \
        "«Сдаст ли Петя матан?» — спор разберёт команда проекта."
    with django_capture_on_commit_callbacks(execute=True):
        chat_disputes.team_decide(q, "yes")
    notice = Notification.objects.get(key=f"moderator:{q.pk}")
    assert notice.title == "Решение команды проекта:"
    assert notice.text == "«Сдаст ли Петя матан?» — спор отклонён: итог ДА, как ты и указал."


def test_review_admin_lists_fields(people, market):
    run_tick(Bot([ADMIN1]))
    model_admin = ChatDisputeReviewAdmin(ChatDisputeReview, AdminSite())
    assert model_admin.list_display == ("question", "group", "created_at", "deadline", "decided_by", "decision",
                                        "escalated_at")
    assert not model_admin.has_add_permission(None)
