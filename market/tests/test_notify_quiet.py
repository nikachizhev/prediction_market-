"""Phase 3d, spec E6.3: per-market notify levels, quiet mode, the 🔕 button, the bell and the star in the API.

Telegram is always mocked.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from market import bot_notifications as notices
from market import notify_levels, services
from market.models import (BotMessage, Group, GroupMembership, MarketNotifyLevel, MarketSubscription, Notification,
                           Question, Trader)
from market.telegram import handle_update
from market.tests.test_api import Api, bot_token  # noqa: F401 - fixture
from market.tests.test_api import trader as api_trader
from market.tests.test_notifications_phase2 import NOON, make, market, set_prob, trade

pytestmark = pytest.mark.django_db

MARKET_KINDS = ("moves", "closing", "results")


@pytest.fixture
def send():
    with patch("market.telegram.send", return_value={"message_id": 1}) as mock:
        yield mock


@pytest.fixture
def api():
    with patch("market.telegram.api", return_value={}) as mock:
        yield mock


def queued(trader, kind):
    return BotMessage.objects.filter(trader=trader, kind=kind).exists()


# --- levels × kinds ----------------------------------------------------------------------------

@pytest.mark.parametrize("level, expected", [
    ("all", {"moves", "closing", "results"}),
    ("result", {"results"}),
    ("none", set()),
])
def test_explicit_level_decides_which_market_notices_reach_the_bot(level, expected):
    anya, q = make("Аня", chat=1), market()
    notify_levels.set_level(anya, q, level)
    for kind in MARKET_KINDS:
        notices.enqueue(anya, kind, f"{kind}:1", "Заголовок:", "текст", question=q, now=NOON)
    assert {kind for kind in MARKET_KINDS if queued(anya, kind)} == expected
    # The inbox gets everything regardless of the level.
    assert Notification.objects.filter(recipient=anya).count() == 3


def test_default_level_star_position_nothing():
    starred, holder, stranger = make("Звезда", chat=1), make("Позиция", chat=2), make("Никто", chat=3)
    q = market()
    MarketSubscription.objects.create(trader=starred, question=q)
    services.buy(holder, q, "yes", 10)
    for who in (starred, holder, stranger):
        for kind in MARKET_KINDS:
            notices.enqueue(who, kind, f"{kind}:{who.pk}", "А:", "б", question=q, now=NOON)
    assert {k for k in MARKET_KINDS if queued(starred, k)} == set(MARKET_KINDS)
    assert {k for k in MARKET_KINDS if queued(holder, k)} == {"results"}
    assert not BotMessage.objects.filter(trader=stranger).exists()


def test_option_of_a_multi_market_uses_the_root_level():
    creator, anya = make("Автор"), make("Аня", chat=1)
    root = services.create_market(creator, title="Кто победит?", criteria="c", category="fun",
                                  closes_at=NOON + timedelta(days=3), resolves_at=NOON + timedelta(days=4),
                                  options=["Аня", "Борис"])
    option = root.submarkets.first()
    notify_levels.set_level(anya, option, "none")
    notices.enqueue(anya, "results", "r", "А:", "б", question=option, now=NOON)
    assert not queued(anya, "results")


def test_level_changed_after_queueing_is_checked_at_send_time(send):
    anya, q = make("Аня", chat=1), market()
    notify_levels.set_level(anya, q, "all")
    notices.enqueue(anya, "moves", "m", "А:", "б", question=q, now=NOON)
    notify_levels.set_level(anya, q, "none")
    assert notices.flush(NOON)["skipped"] == 1 and not send.called


def test_type_switch_still_wins_over_level_all():
    anya, q = make("Аня", chat=1, notify_prefs={"results": False}), market()
    notify_levels.set_level(anya, q, "all")
    notices.enqueue(anya, "results", "r", "А:", "б", question=q, now=NOON)
    assert not queued(anya, "results") and Notification.objects.filter(key="r").exists()


def test_moves_reach_an_explicit_all_without_star():
    anya, boris = make("Аня", chat=1), make("Борис")
    q = market(deadline=NOON + timedelta(days=5))
    trade(q, boris, 0.5, NOON - timedelta(hours=30))
    trade(q, boris, 0.7, NOON - timedelta(hours=1))
    set_prob(q, 0.7)
    notify_levels.set_level(anya, q, "all")
    assert notices.detect_moves(NOON) == 1 and queued(anya, "moves")


def test_closing_goes_to_the_bot_only_at_level_all():
    holder, fan = make("Позиция", chat=1), make("Звезда", chat=2)
    q = market()
    services.buy(holder, q, "yes", 10)
    Question.objects.filter(pk=q.pk).update(deadline=NOON + timedelta(hours=20))
    MarketSubscription.objects.create(trader=fan, question=q)
    assert notices.detect_closing(NOON) == 2
    assert queued(fan, "closing") and not queued(holder, "closing")
    assert Notification.objects.filter(recipient=holder, kind="closing").exists()


# --- quiet mode and resolution steps -----------------------------------------------------------

def test_quiet_mode_lets_through_only_results_and_resolution_steps():
    anya, q = make("Аня", chat=1, notify_prefs={"quiet": True}), market()
    notify_levels.set_level(anya, q, "all")
    for kind in ("moves", "closing", "results", "creator"):
        notices.enqueue(anya, kind, f"{kind}:1", "А:", "б", question=q, now=NOON)
    notices.enqueue(anya, "referral", "referral:1", "А:", "б", now=NOON)
    assert set(BotMessage.objects.filter(trader=anya).values_list("kind", flat=True)) == {"results", "creator"}
    assert Notification.objects.filter(recipient=anya).count() == 5


def test_quiet_mode_switched_on_after_queueing(send):
    anya, q = make("Аня", chat=1), market()
    MarketSubscription.objects.create(trader=anya, question=q)
    notices.enqueue(anya, "moves", "m", "А:", "б", question=q, now=NOON)
    Trader.objects.filter(pk=anya.pk).update(notify_prefs={"quiet": True})
    assert notices.flush(NOON)["skipped"] == 1 and not send.called


@pytest.mark.parametrize("kind", notices.RESOLUTION_KINDS)
def test_resolution_steps_ignore_level_none_and_quiet_mode(kind):
    anya, q = make("Аня", chat=1, notify_prefs={"quiet": True}), market()
    notify_levels.set_level(anya, q, "none")
    notices.enqueue(anya, kind, "k", "А:", "б", question=q, now=NOON)
    assert queued(anya, kind)


def test_dispute_reaches_a_quiet_creator_who_muted_the_market(django_capture_on_commit_callbacks, send):
    creator, anya = make("Автор", chat=1, notify_prefs={"quiet": True}), make("Аня", chat=2)
    q = market(creator=creator)
    services.buy(anya, q, "yes", 100)
    notify_levels.set_level(creator, q, "none")
    Question.objects.filter(pk=q.pk).update(status=Question.Status.CLOSED, resolution_state="pending",
                                            proposed_outcome="no", dispute_until=timezone.now() + timedelta(hours=1))
    with django_capture_on_commit_callbacks(execute=True):
        services.open_dispute(anya, q)
    message = BotMessage.objects.get(trader=creator, kind="creator")
    BotMessage.objects.filter(pk=message.pk).update(send_after=NOON)
    assert notices.flush(NOON)["sent"] == 1
    assert send.call_args.args[0] == 1 and send.call_args.args[1].startswith("Итог оспорили:")


# --- the 🔕 button -----------------------------------------------------------------------------

def mute_buttons(payload):
    return [b for row in payload["reply_markup"]["inline_keyboard"] for b in row
            if b.get("callback_data", "").startswith("mute:")]


def test_mute_button_under_moves_and_closing_not_under_results():
    anya, q = make("Аня", chat=1), market()
    notify_levels.set_level(anya, q, "all")
    notices.enqueue(anya, "moves", "m", "А:", "б", question=q, now=NOON,
                    payload={"reply_markup": notices.market_button(q)})
    notices.enqueue(anya, "closing", "c", "А:", "б", question=q, now=NOON)
    notices.enqueue(anya, "results", "r", "А:", "б", question=q, now=NOON,
                    payload={"reply_markup": notices.market_button(q)})
    moves = BotMessage.objects.get(key="m").payload
    assert moves["reply_markup"]["inline_keyboard"][0][0]["text"] == "Открыть рынок"
    assert mute_buttons(moves) == [{"text": "🔕 Не присылать про этот рынок", "callback_data": f"mute:{q.pk}"}]
    assert len(mute_buttons(BotMessage.objects.get(key="c").payload)) == 1
    assert mute_buttons(BotMessage.objects.get(key="r").payload) == []


def callback(data, user_id=1, chat_id=None, text="Снег: вероятность выросла до 64%.", markup=None):
    return {"callback_query": {
        "id": "cb", "data": data, "from": {"id": user_id},
        "message": {"message_id": 77, "chat": {"id": user_id if chat_id is None else chat_id, "type": "private"},
                    "text": text, "reply_markup": markup or {}}}}


def calls(api, method):
    return [c.kwargs for c in api.call_args_list if c.args[0] == method]


def test_mute_then_restore_without_explicit_choice(api):
    anya, q = make("Аня", chat=1), market()
    MarketSubscription.objects.create(trader=anya, question=q)
    markup = {"inline_keyboard": [*notices.market_button(q)["inline_keyboard"], notices._mute_row(q.pk)]}
    handle_update(callback(f"mute:{q.pk}", markup=markup))
    assert notify_levels.level(anya, q) == "none"
    edit = calls(api, "editMessageText")[-1]
    assert edit["chat_id"] == 1 and edit["message_id"] == 77
    assert edit["text"] == "Снег: вероятность выросла до 64%.\n\nБольше не пишу про этот рынок"
    rows = edit["reply_markup"]["inline_keyboard"]
    assert rows[0][0]["text"] == "Открыть рынок"
    assert rows[-1] == [{"text": "Вернуть", "callback_data": f"mute:back:{q.pk}:default"}]
    assert calls(api, "answerCallbackQuery")[-1] == {"callback_query_id": "cb"}

    handle_update(callback(f"mute:back:{q.pk}:default", text=edit["text"], markup=edit["reply_markup"]))
    assert not MarketNotifyLevel.objects.filter(trader=anya).exists()
    assert notify_levels.level(anya, q) == "all"
    edit = calls(api, "editMessageText")[-1]
    assert edit["text"] == "Снег: вероятность выросла до 64%."
    assert edit["reply_markup"]["inline_keyboard"][-1] == notices._mute_row(q.pk)
    assert len(edit["reply_markup"]["inline_keyboard"]) == 2


def test_restore_puts_back_the_previous_explicit_level(api):
    anya, q = make("Аня", chat=1), market()
    notify_levels.set_level(anya, q, "result")
    handle_update(callback(f"mute:{q.pk}"))
    back = calls(api, "editMessageText")[-1]["reply_markup"]["inline_keyboard"][-1][0]["callback_data"]
    assert back == f"mute:back:{q.pk}:result" and notify_levels.level(anya, q) == "none"
    handle_update(callback(back))
    assert MarketNotifyLevel.objects.get(trader=anya).level == "result"


def test_mute_from_an_option_id_mutes_the_root(api):
    creator, anya = make("Автор"), make("Аня", chat=1)
    root = services.create_market(creator, title="Кто победит?", criteria="c", category="fun",
                                  closes_at=NOON + timedelta(days=3), resolves_at=NOON + timedelta(days=4),
                                  options=["Аня", "Борис"])
    Question.objects.filter(pk=root.pk).update(publication_state="approved")
    root.submarkets.update(publication_state="approved")
    handle_update(callback(f"mute:{root.submarkets.first().pk}"))
    assert MarketNotifyLevel.objects.get(trader=anya).question_id == root.pk


def test_someone_elses_button_changes_nothing(api):
    make("Аня", chat=1), make("Борис", chat=2)
    q = market()
    handle_update(callback(f"mute:{q.pk}", user_id=2, chat_id=1))
    assert not MarketNotifyLevel.objects.exists() and not calls(api, "editMessageText")
    assert calls(api, "answerCallbackQuery")[-1]["text"] == "Эта кнопка не для тебя."


@pytest.mark.parametrize("data", ["mute:999999", "mute:abc", "mute:back:1:loud", "mute:"])
def test_stale_button_gets_a_polite_answer(api, data):
    make("Аня", chat=1)
    handle_update(callback(data))
    assert not MarketNotifyLevel.objects.exists() and not calls(api, "editMessageText")
    assert calls(api, "answerCallbackQuery")[-1]["text"] == "Кнопка устарела."


def test_private_market_after_leaving_the_group_is_stale(api):
    anya = make("Аня", chat=1)
    group = Group.objects.create(name="Клуб")
    q = market(group=group)
    GroupMembership.objects.create(group=group, trader=anya, status="left")
    handle_update(callback(f"mute:{q.pk}"))
    assert not MarketNotifyLevel.objects.exists()
    assert calls(api, "answerCallbackQuery")[-1]["text"] == "Кнопка устарела."


def test_unknown_telegram_user_is_stale(api):
    q = market()
    handle_update(callback(f"mute:{q.pk}", user_id=555))
    assert calls(api, "answerCallbackQuery")[-1]["text"] == "Кнопка устарела."


def test_edit_failure_keeps_the_new_level():
    from market.telegram import TelegramError
    anya, q = make("Аня", chat=1), market()

    def fake(method, **data):
        if method == "editMessageText":
            raise TelegramError("old message")
        return {}
    with patch("market.telegram.api", side_effect=fake):
        handle_update(callback(f"mute:{q.pk}"))
    assert notify_levels.level(anya, q) == "none"


# --- API: the bell, the star, quiet mode -------------------------------------------------------

@pytest.fixture
def anya_api(client, bot_token):  # noqa: F811
    api = Api(client, 1001, first_name="Аня")
    api.post("session")
    return api


def test_notify_endpoint_sets_level_and_detail_shows_it(anya_api):
    q = market()
    _, detail = anya_api.get(f"markets/{q.pk}")
    assert detail["notify_level"] == "none"
    code, data = anya_api.post(f"markets/{q.pk}/notify", {"level": "result"})
    assert code == 200 and data == {"level": "result"}
    assert anya_api.get(f"markets/{q.pk}")[1]["notify_level"] == "result"
    for bad in ({"level": "loud"}, {"level": None}, {}, {"level": ["all"]}):
        code, data = anya_api.post(f"markets/{q.pk}/notify", bad)
        assert code == 400 and data["error"] == "Неизвестный уровень уведомлений."
    assert MarketNotifyLevel.objects.get(trader=api_trader(1001)).level == "result"


def test_notify_on_an_option_sets_the_whole_market(anya_api):
    creator = make("Автор")
    root = services.create_market(creator, title="Кто победит?", criteria="c", category="fun",
                                  closes_at=NOON + timedelta(days=3), resolves_at=NOON + timedelta(days=4),
                                  options=["Аня", "Борис"])
    Question.objects.filter(pk=root.pk).update(publication_state="approved")
    root.submarkets.update(publication_state="approved")
    option = root.submarkets.first()
    assert anya_api.post(f"markets/{option.pk}/notify", {"level": "all"}) == (200, {"level": "all"})
    assert MarketNotifyLevel.objects.get(trader=api_trader(1001)).question_id == root.pk


def test_notify_on_someone_elses_private_market_is_404(anya_api):
    q = market(group=Group.objects.create(name="Клуб"))
    assert anya_api.post(f"markets/{q.pk}/notify", {"level": "all"})[0] == 404
    assert anya_api.post("markets/999999/notify", {"level": "all"})[0] == 404
    assert anya_api.post(f"markets/{q.pk}/star", {"starred": True})[0] == 404
    assert not MarketNotifyLevel.objects.exists()


def test_star_sets_all_and_unstar_drops_the_choice(anya_api):
    q = market()
    me = api_trader(1001)
    notify_levels.set_level(me, q, "none")
    assert anya_api.post(f"markets/{q.pk}/star", {"starred": True}) == (200, {"starred": True, "notify_level": "all"})
    assert MarketNotifyLevel.objects.get(trader=me).level == "all"
    assert anya_api.post(f"markets/{q.pk}/star", {"starred": False}) == (200, {"starred": False, "notify_level": "none"})
    assert not MarketNotifyLevel.objects.filter(trader=me).exists()
    # Unstarred with a position: back to the default «Только итог».
    services.buy(me, q, "yes", 10)
    assert anya_api.post(f"markets/{q.pk}/star", {"starred": False})[1]["notify_level"] == "result"


def test_me_notify_quiet(anya_api):
    _, me = anya_api.get("me")
    assert me["notify_quiet"] is False
    code, me = anya_api.patch("me", {"notify_quiet": True})
    assert code == 200 and me["notify_quiet"] is True
    assert notices.quiet_mode(api_trader(1001))
    # Switches by type are untouched and still patchable together with the quiet mode.
    code, me = anya_api.patch("me", {"notify": {"moves": False}, "notify_quiet": False})
    assert code == 200 and me["notify_quiet"] is False and me["notify"]["moves"] is False
    for bad in ("да", 1, None):
        assert anya_api.patch("me", {"notify_quiet": bad})[0] == 400
    assert api_trader(1001).notify_prefs == {"moves": False, "quiet": False}
