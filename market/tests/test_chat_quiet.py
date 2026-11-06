"""A quiet bot in closed chats (spec E8.4, E8.5; phase 3d, T29): modes, announced markets, `announce` in the API.

Telegram is always mocked.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone

from market import bot_chats, chats, services
from market.models import ChatPost, Event, GroupMembership, MarketDraft, Question
from market.telegram import handle_update
from market.tests.test_api import Api, bot_send, bot_token, create_payload, trader  # noqa: F401 (fixtures)
from market.tests.test_bot_chats import (  # noqa: F401 (fixtures)
    ADMIN, ANYA, BORIS, CHAT, OUTSIDER, Bot, bot, chat, chat_market, env, finish, join, make, message, msk,
    next_monday, press,
)

VERA, GLEB = 3005, 3006
MODES = ("quiet", "important", "all")


def bettors(group, market, *ids, amount=10):
    """Chat members who bet on the market (only the fact; the bot never says who bet how)."""
    people = []
    for tg in ids:
        person = make(f"Игрок {tg}", tg)
        join(group, person)
        services.buy(person, market, "yes", amount)
        people.append(person)
    return people


def old(market, days=2):
    """Created long ago: no longer «new» for the «Всё» mode."""
    Question.objects.filter(pk=market.pk).update(created_at=timezone.now() - timedelta(days=days))
    market.refresh_from_db()


def keys():
    return set(ChatPost.objects.values_list("key", flat=True))


# --- the mode ----------------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize("stored, mode", [({}, "important"), ({"mode": "all"}, "all"), ({"mode": "quiet"}, "quiet"),
                                          ({"mode": "loud"}, "important"), ({"new_market": False}, "important")])
def test_post_mode_defaults_to_important(stored, mode):
    group = chat(post_settings=stored)
    assert chats.post_mode(group) == mode
    assert chats.post_enabled(group, "results") is (mode != "quiet")


@pytest.mark.django_db
@pytest.mark.parametrize("mode", MODES)
def test_modes_for_new_markets(bot, mode):
    group = chat(post_settings={"mode": mode})
    author = make("Автор")
    plain = chat_market(group, author, title="Будет ли снег?")
    asked = chat_market(group, author, title="Перенесут ли коллоквиум?", announce=True)
    bot_chats.tick(msk(12))
    expected = {"quiet": set(), "important": {f"new:{asked.pk}"},
                "all": {f"new:{asked.pk}", f"new:{plain.pk}"}}[mode]
    assert keys() == expected
    assert len(bot.sent("sendMessage", CHAT)) == len(expected)
    for market in (plain, asked):
        market.refresh_from_db()
        assert (market.announced_at is not None) is (f"new:{market.pk}" in expected)


@pytest.mark.django_db
@pytest.mark.parametrize("mode", MODES)
def test_modes_for_results(bot, mode):
    group = chat(post_settings={"mode": mode})
    author = make("Автор")
    plain = chat_market(group, author, title="Будет ли снег?")
    announced = chat_market(group, author, title="Перенесут ли коллоквиум?")
    old(plain)
    old(announced)
    Question.objects.filter(pk=announced.pk).update(announced_at=timezone.now() - timedelta(days=2))
    finish(plain, "yes")
    finish(announced, "no")
    bot_chats.tick(msk(12))
    expected = {"quiet": set(), "important": {f"result:{announced.pk}"},
                "all": {f"result:{announced.pk}", f"result:{plain.pk}"}}[mode]
    assert keys() == expected
    assert len(bot.sent("sendMessage", CHAT)) == len(expected)


@pytest.mark.django_db
@pytest.mark.parametrize("mode", MODES)
def test_modes_for_weekly_top(bot, mode):
    group = chat(post_settings={"mode": mode})
    market = chat_market(group, make("Автор"))
    bettors(group, market, ANYA)
    finish(market, "yes")
    bot_chats.tick(msk(10, base=next_monday()))
    top = ChatPost.objects.filter(kind="weekly_top")
    if mode == "quiet":
        assert not top.exists() and not bot.calls
    else:
        assert top.get().status == ChatPost.Status.SENT
        assert [d for d in bot.sent("sendMessage", CHAT) if d["text"].startswith("Топ-3 недели")]


@pytest.mark.django_db
def test_quiet_mode_still_answers_commands(bot):
    chat(post_settings={"mode": "quiet"})
    handle_update(message("/markets"))
    assert "Открытых рынков в чате пока нет" in bot.sent("sendMessage", CHAT)[-1]["text"]


@pytest.mark.django_db
def test_mode_changed_while_waiting_skips_unwanted_posts(bot):
    group = chat(post_settings={"mode": "all"})
    author = make("Автор")
    plain = chat_market(group, author, title="Будет ли снег?")
    asked = chat_market(group, author, title="Перенесут ли коллоквиум?", announce=True)
    done = bot_chats.tick(msk(23, 30))  # queued during the quiet hours
    assert done["queued"] == 2 and not bot.calls
    group.post_settings = {"mode": "important"}
    group.save(update_fields=["post_settings"])
    done = bot_chats.tick(msk(9, 5, days=1))
    assert done["sent"] == 1 and done["skipped"] == 1
    assert ChatPost.objects.get(key=f"new:{plain.pk}").status == ChatPost.Status.SKIPPED
    assert ChatPost.objects.get(key=f"new:{asked.pk}").status == ChatPost.Status.SENT


# --- announced markets -------------------------------------------------------------------------

@pytest.mark.django_db
def test_ticked_market_is_announced_once_with_the_usual_text(bot):
    group = chat()
    market = chat_market(group, make("Автор Рынка"), announce=True)
    assert market.announce and market.announced_at is None
    now = msk(12)
    assert bot_chats.tick(now)["queued"] == 1
    assert bot_chats.tick(now + timedelta(minutes=1))["queued"] == 0
    market.refresh_from_db()
    assert market.announced_at == now
    (sent,) = bot.sent("sendMessage", CHAT)
    assert sent["text"].startswith("Новый рынок в чате:\n«Перенесут ли коллоквиум?»")
    assert "Автор" not in sent["text"] and "reply_parameters" not in sent and "message_thread_id" not in sent


@pytest.mark.django_db
@override_settings(PREMODERATION_ENABLED=True)
def test_ticked_market_waits_for_premoderation(bot):
    group = chat()
    market = chat_market(group, make("Автор"), announce=True)
    assert market.publication_state == "pending"
    assert bot_chats.tick(msk(12))["queued"] == 0 and not ChatPost.objects.exists()
    # Approved three days later: an asked announcement still goes out.
    Question.objects.filter(pk=market.pk).update(publication_state="approved",
                                                 created_at=timezone.now() - timedelta(days=3))
    assert bot_chats.tick(msk(12, 5))["queued"] == 1
    assert ChatPost.objects.get().key == f"new:{market.pk}"


@pytest.mark.django_db
def test_three_chat_members_announce_a_quiet_market(bot):
    group = chat()
    market = chat_market(group, make("Автор Рынка"))
    old(market, days=5)  # the crowd counts whenever it gathers, not only on the first day
    bettors(group, market, ANYA, BORIS)
    assert bot_chats.tick(msk(12))["queued"] == 0
    # Someone who left the chat does not count, even with a position.
    leaver = bettors(group, market, OUTSIDER)[0]
    GroupMembership.objects.filter(group=group, trader=leaver).update(status="left")
    # A second bet by the same member is still one participant.
    services.buy(trader(ANYA), market, "no", 10)
    assert bot_chats.tick(msk(12, 1))["queued"] == 0 and not ChatPost.objects.exists()
    bettors(group, market, VERA)
    assert bot_chats.tick(msk(12, 2))["queued"] == 1
    market.refresh_from_db()
    assert market.announced_at == msk(12, 2)
    post = ChatPost.objects.get()
    assert post.key == f"crowd:{market.pk}" and post.kind == "new_market"
    (sent,) = bot.sent("sendMessage", CHAT)
    assert sent["text"].startswith("В рынке уже 3 участника:\n«Перенесут ли коллоквиум?»\nВероятность ДА: ")
    assert sent["text"].endswith("%")
    assert "Автор" not in sent["text"] and "Игрок" not in sent["text"]
    assert sent["reply_markup"]["inline_keyboard"][0][0]["url"].endswith(f"startapp=m{market.pk}")
    # Never again, however many more people come.
    bettors(group, market, GLEB)
    assert bot_chats.tick(msk(13))["queued"] == 0
    assert len(bot.sent("sendMessage", CHAT)) == 1


@pytest.mark.django_db
def test_crowd_counts_options_of_a_multi_market(bot):
    group = chat()
    author = make("Автор")
    join(group, author)
    now = timezone.now()
    market = services.create_market(author, title="Кто сдаст первым?", criteria="По ведомости", category="study",
                                    closes_at=now + timedelta(days=3), resolves_at=now + timedelta(days=4),
                                    group=group, options=["Аня", "Борис", "Вера"])
    old(market)
    options = list(market.submarkets.order_by("id"))
    for tg, option in zip((ANYA, BORIS, VERA), options):
        person = make(f"Игрок {tg}", tg)
        join(group, person)
        services.buy(person, option, "yes", 10)
    assert bot_chats.tick(msk(12))["queued"] == 1
    text = bot.sent("sendMessage", CHAT)[0]["text"]
    assert text.startswith("В рынке уже 3 участника:\n«Кто сдаст первым?»\n• Аня — ")


@pytest.mark.django_db
def test_crowd_needs_an_open_market(bot):
    group = chat()
    market = chat_market(group, make("Автор"))
    old(market)
    bettors(group, market, ANYA, BORIS, VERA)
    Question.objects.filter(pk=market.pk).update(deadline=msk(11, 59))
    assert bot_chats.tick(msk(12))["queued"] == 0


@pytest.mark.django_db
@pytest.mark.parametrize("forum", [True, False])
def test_market_from_a_message_replies_to_it_in_its_topic(bot, forum):
    group = chat(is_forum=forum)
    author = make("Автор", ADMIN)
    market = chat_market(group, author)  # announce is not even ticked: made from a message is enough
    MarketDraft.objects.create(code="d1", trader=author, group=group, source_message_id=555, thread_id=42,
                               title=market.title, question=market, expires_at=timezone.now() + timedelta(hours=24))
    assert bot_chats.tick(msk(12))["queued"] == 1
    post = ChatPost.objects.get()
    assert post.payload["reply_parameters"] == {"message_id": 555, "allow_sending_without_reply": True}
    (sent,) = bot.sent("sendMessage", CHAT)
    assert sent["reply_parameters"] == {"message_id": 555, "allow_sending_without_reply": True}
    assert sent["text"].startswith("Новый рынок в чате:")
    if forum:
        assert sent["message_thread_id"] == 42
    else:
        assert "message_thread_id" not in sent
    # Its result goes to the same topic.
    finish(market, "yes")
    bot_chats.tick(msk(12, 5))
    result = bot.sent("sendMessage", CHAT)[-1]
    assert "Итог рынка чата" in result["text"] and "reply_parameters" not in result
    assert result.get("message_thread_id") == (42 if forum else None)


@pytest.mark.django_db
def test_draft_without_source_message_is_announced_without_reply(bot):
    group = chat()
    author = make("Автор", ADMIN)
    market = chat_market(group, author)
    MarketDraft.objects.create(code="d2", trader=author, group=group, title=market.title, question=market,
                               expires_at=timezone.now() + timedelta(hours=24))
    bot_chats.tick(msk(12))
    (sent,) = bot.sent("sendMessage", CHAT)
    assert "reply_parameters" not in sent


# --- results only for announced markets --------------------------------------------------------

@pytest.mark.django_db
def test_result_only_for_announced_market(bot):
    group = chat()
    author = make("Автор")
    announced = chat_market(group, author, title="Перенесут ли коллоквиум?", announce=True)
    quiet = chat_market(group, author, title="Будет ли снег?")
    bot_chats.tick(msk(12))
    assert keys() == {f"new:{announced.pk}"}
    finish(announced, "yes")
    finish(quiet, "no")
    bot_chats.tick(msk(13))
    assert keys() == {f"new:{announced.pk}", f"result:{announced.pk}"}
    assert "«Перенесут ли коллоквиум?» — ДА" in bot.sent("sendMessage", CHAT)[-1]["text"]


@pytest.mark.django_db
def test_result_of_crowd_announced_market(bot):
    group = chat()
    market = chat_market(group, make("Автор"))
    old(market)
    bettors(group, market, ANYA, BORIS, VERA)
    bot_chats.tick(msk(12))
    finish(market, "yes")
    bot_chats.tick(msk(13))
    assert keys() == {f"crowd:{market.pk}", f"result:{market.pk}"}


@pytest.mark.django_db
def test_result_of_market_announced_before_phase_3d(bot):
    """Before `announced_at` the `new_market` post was the announcement."""
    group = chat()
    market = chat_market(group, make("Автор"))
    old(market)
    ChatPost.objects.create(group=group, kind="new_market", key=f"new:{market.pk}", text="x", question=market,
                            status=ChatPost.Status.SENT, send_after=timezone.now(), sent_at=timezone.now())
    finish(market, "yes")
    bot_chats.tick(msk(12))
    assert ChatPost.objects.get(key=f"result:{market.pk}").status == ChatPost.Status.SENT


# --- /settings -----------------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize("mode, label", [("quiet", "Тишина"), ("important", "Важное"), ("all", "Всё")])
def test_mode_buttons_mark_the_current_one(bot, mode, label):
    group = chat(post_settings={"mode": mode})
    handle_update(message("/settings"))
    reply = bot.sent("sendMessage", CHAT)[-1]
    row = reply["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in row if b["text"].startswith("✓ ")] == [f"✓ {label}"]
    assert f"Сообщения бота: «{label}» — {bot_chats.MODE_HINTS[mode]}." in reply["text"]
    assert all(b["callback_data"].startswith(f"chat:{group.pk}:mode:") for b in row)


@pytest.mark.django_db
def test_admin_switches_modes_checked_every_time(bot):
    group = chat()
    for mode in ("quiet", "all", "important"):
        handle_update(press(group, f"chat:{group.pk}:mode:{mode}"))
        group.refresh_from_db()
        assert chats.post_mode(group) == mode
        edit = bot.sent("editMessageText")[-1]
        assert f"«{dict(quiet='Тишина', important='Важное', all='Всё')[mode]}»" in edit["text"]
    assert bot.methods().count("getChatAdministrators") == 3
    bot.admins = []
    handle_update(press(group, f"chat:{group.pk}:mode:quiet"))
    group.refresh_from_db()
    assert chats.post_mode(group) == "important"
    assert bot.sent("answerCallbackQuery")[-1]["text"] == bot_chats.ALERT_ADMINS_ONLY


@pytest.mark.django_db
def test_unknown_mode_and_old_switches(bot):
    group = chat()
    handle_update(press(group, f"chat:{group.pk}:mode:loud"))
    assert bot.sent("answerCallbackQuery")[-1]["text"] == bot_chats.NO_SUCH
    calls = bot.methods().count("getChatAdministrators")
    # A switch from a /settings message sent before the modes: just ask for a fresh one.
    handle_update(press(group, f"chat:{group.pk}:post:new_market"))
    assert bot.sent("answerCallbackQuery")[-1]["text"] == bot_chats.STALE
    assert bot.methods().count("getChatAdministrators") == calls
    group.refresh_from_db()
    assert group.post_settings == {}


# --- POST /api/markets: announce -------------------------------------------------------------------

@pytest.fixture
def anya_api(client, bot_token):  # noqa: F811
    api = Api(client, ANYA, first_name="Аня")
    api.post("session")
    return api


def chat_api_telegram():
    def api(method, **kw):
        if method == "getChatMemberCount":
            return 12
        raise AssertionError(f"unexpected Telegram call {method}")
    return patch("market.chats.telegram.api", side_effect=api)


@pytest.mark.django_db
def test_api_announce_for_chat_market(anya_api):
    group = chat()
    join(group, trader(ANYA))
    with chat_api_telegram():
        code, data = anya_api.post("markets", create_payload(chat=group.code, announce=True))
    assert code == 200
    assert Question.objects.get(pk=data["market"]["id"]).announce is True
    props = Event.objects.get(name="market_create").props
    assert props["announce"] is True and props["chat"] is True


@pytest.mark.django_db
@pytest.mark.parametrize("payload", [{}, {"announce": False}, {"announce": None}])
def test_api_chat_market_without_announce(anya_api, payload):
    group = chat()
    join(group, trader(ANYA))
    with chat_api_telegram():
        code, data = anya_api.post("markets", create_payload(chat=group.code, **payload))
    assert code == 200 and Question.objects.get(pk=data["market"]["id"]).announce is False
    assert Event.objects.get(name="market_create").props["announce"] is False


@pytest.mark.django_db
def test_api_announce_without_chat_is_ignored(anya_api):
    code, data = anya_api.post("markets", create_payload(announce=True))
    assert code == 200 and Question.objects.get(pk=data["market"]["id"]).announce is False
    assert "announce" not in Event.objects.get(name="market_create").props


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["true", 1, 0, [], {"x": 1}])
def test_api_announce_must_be_boolean(anya_api, value):
    group = chat()
    join(group, trader(ANYA))
    code, data = anya_api.post("markets", create_payload(chat=group.code, announce=value))
    assert code == 400 and "announce" in data["error"]
    assert not Question.objects.exists() and trader(ANYA).balance == 1000


@pytest.mark.django_db
@override_settings(PREMODERATION_ENABLED=True)
def test_api_announce_kept_through_premoderation(anya_api):
    group = chat()
    join(group, trader(ANYA))
    with chat_api_telegram():
        code, data = anya_api.post("markets", create_payload(chat=group.code, announce=True))
    assert code == 200 and data["market"] is None and data["publication"] == "pending"
    market = Question.objects.get(pk=data["draft_id"])
    assert market.announce is True and market.announced_at is None
