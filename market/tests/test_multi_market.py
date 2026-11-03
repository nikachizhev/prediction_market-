"""Tests for unified multiple-choice markets across lists, chats, notifications, and Variant B resolution."""
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from market import bot_chats, bot_markets, bot_notifications, chats, resolution, selectors, services, share
from market.models import (
    BotMessage,
    ChatPost,
    Dispute,
    Group,
    GroupMembership,
    LedgerEntry,
    MarketSubscription,
    Notification,
    Payout,
    Question,
    ReferralReward,
    ResolutionDraft,
    Trader,
)
from market.telegram import handle_update
from market.tests.test_api import Api, bot_token  # noqa: F401

D = Decimal
MSK = ZoneInfo("Europe/Moscow")
CREATOR_TG, ANYA_TG, BORIS_TG = 7101, 7102, 7103
CHAT_TG = -100777

pytestmark = pytest.mark.django_db


class FakeBot:
    def __init__(self):
        self.calls = []

    def __call__(self, method, **data):
        self.calls.append((method, data))
        if method == "getFile":
            return {"file_id": data["file_id"], "file_path": "photos/proof.jpg", "file_size": 16}
        return {"message_id": len(self.calls)}

    def sent(self, method=None, chat_id=None):
        return [
            d for m, d in self.calls
            if (method is None or m == method) and (chat_id is None or d.get("chat_id") == chat_id)
        ]


@pytest.fixture(autouse=True)
def env(tmp_path):
    with override_settings(
        MEDIA_ROOT=tmp_path,
        SITE_URL="https://example.test",
        TELEGRAM_BOT_TOKEN="T",
        TELEGRAM_BOT_USERNAME="ChutyeBot",
        TELEGRAM_APP_NAME="app",
    ):
        yield


@pytest.fixture
def bot():
    fake = FakeBot()
    download = MagicMock(ok=True, content=b"\xff\xd8proof-jpeg")
    with patch("market.telegram.api", side_effect=fake), patch("market.telegram.requests.get", return_value=download):
        yield fake


def make_trader(name: str, tg: int | None = None, balance: int = 1000, **kw) -> Trader:
    return Trader.objects.create(name=name, pseudonym=name, balance=D(balance), telegram_chat_id=tg, **kw)


def make_multi(creator: Trader, *, title="Защита кончится до ...", options=None, group=None) -> tuple[Question, list[Question]]:
    now = timezone.now()
    opts = options or ["до 20:00", "до 21:00", "до 22:00"]
    root = services.create_market(
        creator,
        title=title,
        criteria="По часам в аудитории",
        category="study",
        closes_at=now + timedelta(hours=4),
        resolves_at=now + timedelta(hours=5),
        options=opts,
        group=group,
    )
    subs = list(root.submarkets.all().order_by("id"))
    return root, subs


def tg_text(user: int, body: str) -> dict:
    return {"message": {"message_id": 5, "chat": {"id": user, "type": "private"}, "from": {"id": user}, "text": body}}


def tg_photo(user: int, caption: str = "Фото табло") -> dict:
    return {
        "message": {
            "message_id": 6,
            "chat": {"id": user, "type": "private"},
            "from": {"id": user},
            "caption": caption,
            "photo": [{"file_id": "proof_big", "width": 1280, "height": 960}],
        }
    }


def tg_press(user: int, data: str, message_id: int = 1) -> dict:
    return {
        "callback_query": {
            "id": f"cb_{message_id}_{data}",
            "from": {"id": user},
            "data": data,
            "message": {"message_id": message_id, "chat": {"id": user, "type": "private"}},
        }
    }


def test_timeout_cancellation_sends_single_combined_refund_message(bot, django_capture_on_commit_callbacks):
    """Regression for screenshot bug: cancelling an unresolved multi-market sends ONE message with summed refund."""
    creator = make_trader("Автор", CREATOR_TG)
    anya = make_trader("Аня", ANYA_TG)
    root, subs = make_multi(creator)
    sub_20, sub_21, sub_22 = subs

    services.buy(anya, sub_20, "yes", 57)
    services.buy(anya, sub_21, "no", 46)

    now = timezone.now()
    # Step 1: market closes -> creator receives ONE ask_creator message on root, not 3 per submarket.
    expired = now - timedelta(minutes=5)
    Question.objects.filter(pk__in=[root.pk, *(s.pk for s in subs)]).update(deadline=expired, resolves_at=expired)
    stats = resolution.tick(now)
    assert stats["asked"] == 1
    creator_msgs = bot.sent("sendMessage", CREATOR_TG)
    assert len(creator_msgs) == 1
    assert "Защита кончится до ..." in creator_msgs[0]["text"]
    assert "1. до 20:00" in creator_msgs[0]["text"]

    # Step 2: 72h pass without creator response -> cancelled once with ONE combined refund notification.
    overdue = now - timedelta(hours=73)
    Question.objects.filter(pk__in=[root.pk, *(s.pk for s in subs)]).update(resolves_at=overdue)
    with django_capture_on_commit_callbacks(execute=True):
        stats = resolution.tick(now)
    assert stats["cancelled"] == 1

    root.refresh_from_db()
    assert root.status == Question.Status.ANNULLED
    assert root.resolution_state == Question.Resolution.FINAL
    assert all(s.status == Question.Status.ANNULLED and s.resolution_state == Question.Resolution.FINAL
               for s in root.submarkets.all())

    anya.refresh_from_db()
    assert anya.balance == D("1000.00")

    queued = list(BotMessage.objects.filter(trader=anya, kind="results"))
    assert len(queued) == 1
    msg = queued[0]
    assert msg.question_id == root.pk
    assert msg.text == "Рынок «Защита кончится до ...» отменён. Возврат: +103.00."
    btn = msg.payload["reply_markup"]["inline_keyboard"][0][0]
    assert btn["web_app"]["url"] == f"https://example.test/app/market/{root.pk}"


def test_variant_b_bot_dialog_and_final_settlement(bot, django_capture_on_commit_callbacks):
    """Creator picks ДА/НЕТ per option in one bot card, uploads 1 photo, confirms once; 1 notice & 1 payout sent."""
    creator = make_trader("Автор", CREATOR_TG, balance=1000)
    anya = make_trader("Аня", ANYA_TG, balance=1000)
    boris = make_trader("Борис", BORIS_TG, balance=1000)
    root, subs = make_multi(creator)
    sub1, sub2, sub3 = subs

    creator_balance_after_create = Trader.objects.get(pk=creator.pk).balance
    t_anya_1 = services.buy(anya, sub1, "yes", 60)
    services.buy(anya, sub2, "yes", 40)
    services.buy(boris, sub2, "yes", 50)

    past = timezone.now() - timedelta(minutes=2)
    Question.objects.filter(pk__in=[root.pk, *(s.pk for s in subs)]).update(deadline=past, resolves_at=past)

    # Creator starts resolution dialog.
    handle_update(tg_text(CREATOR_TG, f"/start resolve_{root.pk}"))
    ask = bot.sent("sendMessage", CREATOR_TG)[-1]
    callbacks = [b["callback_data"] for row in ask["reply_markup"]["inline_keyboard"] for b in row]
    assert f"resopt:{root.pk}:{sub1.pk}:yes" in callbacks
    assert f"resnext:{root.pk}" not in callbacks

    # Creator toggles options one by one; "Продолжить →" appears only after all 3 are picked.
    handle_update(tg_press(CREATOR_TG, f"resopt:{root.pk}:{sub1.pk}:yes", message_id=1))
    handle_update(tg_press(CREATOR_TG, f"resopt:{root.pk}:{sub2.pk}:no", message_id=1))
    mid_edit = bot.sent("editMessageText", CREATOR_TG)[-1]
    mid_callbacks = [b["callback_data"] for row in mid_edit["reply_markup"]["inline_keyboard"] for b in row]
    assert f"resnext:{root.pk}" not in mid_callbacks

    handle_update(tg_press(CREATOR_TG, f"resopt:{root.pk}:{sub3.pk}:yes", message_id=1))
    last_edit = bot.sent("editMessageText", CREATOR_TG)[-1]
    last_callbacks = [b["callback_data"] for row in last_edit["reply_markup"]["inline_keyboard"] for b in row]
    assert f"resnext:{root.pk}" in last_callbacks

    # Proceed to photo upload -> 1 photo -> preview -> confirm.
    handle_update(tg_press(CREATOR_TG, f"resnext:{root.pk}", message_id=1))
    assert "Пришли фото или скриншот" in bot.sent("sendMessage", CREATOR_TG)[-1]["text"]

    handle_update(tg_photo(CREATOR_TG))
    preview = bot.sent("sendPhoto", CREATOR_TG)[-1]
    assert "до 20:00: <b>ДА</b>" in preview["caption"]
    assert "до 21:00: <b>НЕТ</b>" in preview["caption"]
    assert "до 22:00: <b>ДА</b>" in preview["caption"]

    handle_update(tg_press(CREATOR_TG, f"resok:{root.pk}", message_id=10))
    assert not ResolutionDraft.objects.exists()

    root.refresh_from_db()
    for s in (sub1, sub2, sub3):
        s.refresh_from_db()
    assert root.resolution_state == Question.Resolution.PENDING
    assert (sub1.proposed_outcome, sub2.proposed_outcome, sub3.proposed_outcome) == ("yes", "no", "yes")

    # Anya and Boris each receive ONE dispute-window photo message with button disp:<root.pk>.
    for tg_id in (ANYA_TG, BORIS_TG):
        photos = bot.sent("sendPhoto", tg_id)
        assert len(photos) == 1
        assert photos[0]["reply_markup"]["inline_keyboard"][0][0] == {"text": "Оспорить", "callback_data": f"disp:{root.pk}"}

    # Dispute window expires -> tick finalizes all submarkets & settles creator once.
    expired_window = timezone.now() - timedelta(minutes=1)
    Question.objects.filter(pk__in=[root.pk, sub1.pk, sub2.pk, sub3.pk]).update(dispute_until=expired_window)
    with django_capture_on_commit_callbacks(execute=True):
        stats = resolution.tick(timezone.now())
    assert stats["finalized"] == 1

    root.refresh_from_db()
    for s in (sub1, sub2, sub3):
        s.refresh_from_db()
    assert root.status == Question.Status.RESOLVED and root.resolution_state == Question.Resolution.FINAL
    assert (sub1.outcome, sub2.outcome, sub3.outcome) == ("yes", "no", "yes")
    assert "до 20:00: ДА" in root.result_label and "до 21:00: НЕТ" in root.result_label

    # Creator gets 1 subsidy return + 1 creator fee entry on root.
    assert LedgerEntry.objects.filter(trader=creator, kind=LedgerEntry.Kind.SUBSIDY_RETURN).count() == 1
    fee_entries = list(LedgerEntry.objects.filter(trader=creator, kind=LedgerEntry.Kind.CREATOR_FEE))
    assert len(fee_entries) == 1 and fee_entries[0].question_id == root.pk
    total_fee_pool = sum((s.fee_pool for s in (sub1, sub2, sub3)), D(0))
    expected_fee = (total_fee_pool / 2).quantize(D("0.01"), rounding="ROUND_FLOOR")
    assert fee_entries[0].amount == expected_fee
    creator.refresh_from_db()
    assert creator.balance > creator_balance_after_create

    # Anya won on sub1 and lost on sub2 -> ONE result BotMessage with confetti; Boris lost -> ONE message without confetti.
    anya_msgs = list(BotMessage.objects.filter(trader=anya, kind="results"))
    boris_msgs = list(BotMessage.objects.filter(trader=boris, kind="results"))
    assert len(anya_msgs) == 1 and len(boris_msgs) == 1
    expected_win = t_anya_1.shares.quantize(D("0.01"), rounding="ROUND_FLOOR")
    assert f"Ты угадал! +{bot_notifications.coins(expected_win)}" in anya_msgs[0].text
    assert anya_msgs[0].payload.get("message_effect_id") == "5046509860389126442"
    assert "В этот раз мимо" in boris_msgs[0].text
    assert "message_effect_id" not in boris_msgs[0].payload


@pytest.mark.parametrize("decision,expect_upheld", [("yes", False), ("cancel", True)])
def test_dispute_and_moderator_decision_on_multi_market(decision, expect_upheld):
    """Option trader can open dispute on root; moderator confirms per-option outcomes or cancels whole market."""
    creator = make_trader("Автор", CREATOR_TG)
    anya = make_trader("Аня", ANYA_TG)
    root, subs = make_multi(creator, options=["Вариант А", "Вариант Б"])
    sub_a, sub_b = subs

    services.buy(anya, sub_b, "no", 80)
    past = timezone.now() - timedelta(minutes=5)
    Question.objects.filter(pk__in=[root.pk, sub_a.pk, sub_b.pk]).update(deadline=past, resolves_at=past)

    proof = SimpleUploadedFile("proof.jpg", b"\xff\xd8proof", content_type="image/jpeg")
    services.propose_resolution(
        creator,
        root,
        "yes",
        evidence=proof,
        submarket_outcomes={sub_a.pk: "yes", sub_b.pk: "yes"},
    )

    dispute = services.open_dispute(anya, root, reason="Б не случился")
    root.refresh_from_db()
    sub_a.refresh_from_db()
    sub_b.refresh_from_db()
    assert root.resolution_state == Question.Resolution.DISPUTED
    assert sub_a.resolution_state == Question.Resolution.DISPUTED
    assert sub_b.resolution_state == Question.Resolution.DISPUTED

    services.moderator_decide(root, decision)
    dispute.refresh_from_db()
    root.refresh_from_db()
    sub_a.refresh_from_db()
    sub_b.refresh_from_db()
    anya.refresh_from_db()

    assert dispute.upheld is expect_upheld
    assert root.resolution_state == Question.Resolution.FINAL
    if decision == "yes":
        assert root.status == Question.Status.RESOLVED
        assert (sub_a.outcome, sub_b.outcome) == ("yes", "yes")
        assert anya.balance == D("1000.00") - D("80.00") - dispute.stake
    else:
        assert root.status == Question.Status.ANNULLED
        assert sub_a.status == Question.Status.ANNULLED and sub_b.status == Question.Status.ANNULLED
        assert anya.balance == D("1000.00")


def test_unified_lists_subscriptions_mine_profile_chats_and_referrals(client, bot_token, bot):  # noqa: F811
    """Multi-choice market stays unified across feed, /api/mine, profile ledger, chats, inline search, and referrals."""
    creator_api = Api(client, 8101, first_name="Автор")
    creator_api.post("session")
    anya_api = Api(client, 8102, first_name="Аня")
    anya_api.post("session")

    creator = Trader.objects.get(telegram_chat_id=8101)
    inviter = make_trader("Пригласивший", 8109)
    anya = Trader.objects.get(telegram_chat_id=8102)
    anya.referred_by = inviter
    anya.save(update_fields=["referred_by"])

    root, subs = make_multi(creator, title="Кто выиграет хакатон?", options=["Команда 1", "Команда 2", "Команда 3"])
    sub1, sub2, sub3 = subs

    # Trade via API on sub1 and sub2.
    code, trade_res = anya_api.post(
        f"markets/{sub1.pk}/buy",
        {"side": "yes", "amount": "50.00", "request_key": str(uuid.uuid4())},
    )
    assert code == 200
    assert anya_api.post(
        f"markets/{sub2.pk}/buy",
        {"side": "no", "amount": "30.00", "request_key": str(uuid.uuid4())},
    )[0] == 200

    # Auto-subscription attaches to parent, not child submarkets.
    assert MarketSubscription.objects.filter(trader=anya, question=root).exists()
    assert not MarketSubscription.objects.filter(trader=anya, question__in=subs).exists()

    # Selectors, bot_markets, inline share search return only root.
    assert [q.pk for q in selectors.market_list(trader=anya)] == [root.pk]
    assert [q.pk for q in bot_markets.tracked_markets(anya)] == [root.pk]
    assert [q.pk for q in share.search(anya, "хакатон")] == [root.pk]
    assert share.market_link(anya, sub1).endswith(f"-m{root.pk}")

    # Submarket detail includes parent_id for Mini App redirect; /api/mine unifies watched & created and tags positions.
    _, sub_detail = anya_api.get(f"markets/{sub1.pk}")
    assert sub_detail["parent_id"] == root.pk

    _, mine_anya = anya_api.get("mine")
    assert [m["id"] for m in mine_anya["watched"]] == [root.pk]
    assert len(mine_anya["positions"]) == 2
    assert {m["parent_id"] for m in mine_anya["positions"]} == {root.pk}

    _, mine_creator = creator_api.get("mine")
    assert [m["id"] for m in mine_creator["created"]] == [root.pk]
    assert D(mine_creator["created"][0]["fee_expected"]) > 0

    # Profile ledger points submarket trades to parent market id.
    _, prof = anya_api.get("profile")
    trade_entries = [e for e in prof["ledger"] if e["kind"] == "buy"]
    assert len(trade_entries) == 2
    assert {e["market"]["id"] for e in trade_entries} == {root.pk}

    # Referral count: trading on 3 options of the SAME multi-market counts as 1 market, not 3.
    assert anya_api.post(
        f"markets/{sub3.pk}/buy",
        {"side": "yes", "amount": "20.00", "request_key": str(uuid.uuid4())},
    )[0] == 200
    for idx in range(3):
        other = Question.objects.create(title=f"Бинарный {idx}", resolution_criteria="c", kind=Question.Kind.LMSR, b=144)
        services.buy(anya, other, "yes", 10)
    # 1 multi-market + 3 binary markets = 4 distinct root markets -> no reward yet!
    assert not ReferralReward.objects.filter(friend=anya, granted=True).exists()
    fifth = Question.objects.create(title="Пятый рынок", resolution_criteria="c", kind=Question.Kind.LMSR, b=144)
    services.buy(anya, fifth, "yes", 10)
    assert ReferralReward.objects.filter(friend=anya, granted=True).exists()

    # Closed Telegram chat: 1 market count, 1 card in chat_markets, 1 new_market ChatPost, 1 results ChatPost.
    grp = Group.objects.create(
        name="Чат курса",
        kind="chat",
        telegram_chat_id=CHAT_TG,
        code=chats.new_code(),
        bot_active=True,
        member_count=15,
    )
    GroupMembership.objects.create(group=grp, trader=creator, status="active", checked_at=timezone.now())
    GroupMembership.objects.create(group=grp, trader=anya, status="active", checked_at=timezone.now())

    chat_root, chat_subs = make_multi(creator, title="Когда выложат оценки?", options=["Сегодня", "Завтра"], group=grp)
    services.buy(anya, chat_subs[0], "yes", 25)

    _, space = anya_api.get(f"chats/{grp.code}")
    assert space["markets"] == 1
    _, c_markets = anya_api.get(f"chats/{grp.code}/markets")
    assert [m["id"] for m in c_markets["items"]] == [chat_root.pk]
    assert [q.pk for q in bot_chats.open_markets(grp)] == [chat_root.pk]

    now = timezone.now()
    assert bot_chats.fill(now) == 1
    new_posts = list(ChatPost.objects.filter(group=grp, kind="new_market"))
    assert len(new_posts) == 1 and new_posts[0].question_id == chat_root.pk
    assert "• Сегодня" in new_posts[0].text and "• Завтра" in new_posts[0].text

    # Closing & moves detectors send at most 1 notification per root market (never per submarket).
    Question.objects.filter(pk__in=[chat_root.pk, *(s.pk for s in chat_subs)]).update(
        deadline=now + timedelta(hours=12),
    )
    assert bot_notifications.detect_closing(now) == 2  # 1 for `root` + 1 for `chat_root`
    assert BotMessage.objects.filter(trader=anya, kind="closing", question=chat_root).count() == 1
    assert BotMessage.objects.filter(trader=anya, kind="closing", question__parent__isnull=False).count() == 0

    # Resolving the chat multi-market queues ONE results ChatPost summarizing per-option outcomes.
    past = now - timedelta(minutes=5)
    Question.objects.filter(pk__in=[chat_root.pk, *(s.pk for s in chat_subs)]).update(
        deadline=past, resolves_at=past,
    )
    proof = SimpleUploadedFile("chat.jpg", b"\xff\xd8chat", content_type="image/jpeg")
    services.propose_resolution(
        creator,
        chat_root,
        "yes",
        evidence=proof,
        submarket_outcomes={chat_subs[0].pk: "yes", chat_subs[1].pk: "no"},
    )
    Question.objects.filter(pk__in=[chat_root.pk, *(s.pk for s in chat_subs)]).update(dispute_until=past)
    services.finalize_resolution(chat_root)
    assert bot_chats.fill(now) == 1
    res_posts = list(ChatPost.objects.filter(group=grp, kind="results"))
    assert len(res_posts) == 1 and res_posts[0].question_id == chat_root.pk
    assert "Сегодня: ДА" in res_posts[0].text and "Завтра: НЕТ" in res_posts[0].text
