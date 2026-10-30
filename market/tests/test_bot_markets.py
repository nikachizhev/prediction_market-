"""Spec E6.1, E6.2, E6.4: «Мои рынки» in the bot, the 7-day animation, sharing a market."""
import json
import shutil
import subprocess
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from market import bot_markets, charts, services, share
from market.miniapp_auth import _referrer
from market.models import (ChartAnimation, Event, Group, GroupMembership, MarketSubscription, Question, Trade,
                           Trader)
from market.telegram import TelegramError, handle_update
from market.tests.test_api import Api
from market.tests.test_bot_resolution_dialog import Bot, press, text
from market.tests.test_miniapp_auth import TOKEN

D = Decimal
ANYA, BORIS, STRANGER = 2001, 2002, 2003


@pytest.fixture(autouse=True)
def env(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path, SITE_URL="https://example.test", TELEGRAM_BOT_TOKEN=TOKEN,
                           TELEGRAM_BOT_USERNAME="predbot", TELEGRAM_APP_NAME="app"):
        yield


class FakeBot(Bot):
    def __call__(self, method, **data):
        self.calls.append((method, data))
        if method == "savePreparedInlineMessage":
            return {"id": "prep-1", "expiration_date": 1}
        if method == "sendAnimation":
            return {"message_id": len(self.calls), "animation": {"file_id": "anim-id"}}
        return {"message_id": len(self.calls)}


@pytest.fixture
def bot():
    fake = FakeBot()
    fake.uploads = []

    def upload(method, files, **data):
        fake.uploads.append((method, files, data))
        return {"message_id": 1, "animation": {"file_id": "uploaded-id"}, "document": {"file_id": "doc-id"}}

    with patch("market.telegram.api", side_effect=fake), patch("market.telegram.upload", side_effect=upload):
        yield fake


@pytest.fixture
def render():
    with patch("market.charts.render_animation", return_value=b"mp4-bytes") as fake:
        yield fake


def make(name, chat, **kw):
    return Trader.objects.create(name=name, pseudonym=name, balance=D(1000), telegram_chat_id=chat,
                                 referral_code=f"code{chat}", **kw)


@pytest.fixture
def people(db):
    return make("Аня", ANYA), make("Борис", BORIS)


def market(title="Будет снег?", creator=None, **kw):
    return Question.objects.create(title=title, resolution_criteria="Росгидромет", creator=creator, **kw)


def track(trader, *questions):
    for q in questions:
        MarketSubscription.objects.create(trader=trader, question=q)


def keyboard(call):
    return call["reply_markup"]["inline_keyboard"]


# --- «Мои рынки» -------------------------------------------------------------------------------------

def test_my_lists_tracked_markets_with_label(bot, people):
    anya, boris = people
    q = market("Выйдет ли GTA VI до конца 2026 года, и сразу на ПК?", creator=boris)
    track(anya, q)
    Trade.objects.create(trader=boris, question=q, side="yes", shares=D(10), cost=D(5),
                         prob_before=0.5, prob_after=0.5)
    Trade.objects.filter(question=q).update(created_at=timezone.now() - timedelta(days=2))
    Question.objects.filter(pk=q.pk).update(q_yes=50)  # price moved up since yesterday
    handle_update(text(ANYA, "/my"))
    reply = bot.sent("sendMessage", ANYA)[-1]
    assert reply["text"] == "Мои рынки\nНажми на рынок, чтобы увидеть график за неделю."
    (button,), = keyboard(reply)
    q.refresh_from_db()
    pct = round(q.prob * 100)
    up = round((q.prob - 0.5) * 100)
    assert button == {"text": f"Выйдет ли GTA VI до конца 202… · {pct}% ▲ {up}", "callback_data": f"mk:{q.pk}"}


def test_label_down_arrow_and_no_arrow():
    q = Question(title="Матан без пересдач", resolution_criteria="-", q_no=30)
    with patch("market.bot_markets.change_24h", return_value=-6.2):
        assert bot_markets.button_label(q).endswith(f"{round(q.prob * 100)}% ▼ 6")
    with patch("market.bot_markets.change_24h", return_value=0.2):
        assert bot_markets.button_label(q) == f"Матан без пересдач · {round(q.prob * 100)}%"


def test_list_hides_resolved_other_kinds_and_others_private(bot, people):
    anya, boris = people
    group = Group.objects.create(name="Закрытая", owner=boris)
    private = market("Приватный", group=group)
    done = market("Завершён", status=Question.Status.RESOLVED, outcome="yes")
    poll = market("Опрос", kind=Question.Kind.POLL)
    ok = market("Открытый")
    track(anya, private, done, poll, ok)
    assert bot_markets.tracked_markets(anya) == [ok]


def test_paging_edits_in_place(bot, people, settings):
    anya, _ = people
    settings.BOT_MARKETS_PAGE = 10
    qs = [market(f"Рынок {i}") for i in range(23)]
    track(anya, *qs)
    handle_update(text(ANYA, "/my"))
    first = keyboard(bot.sent("sendMessage", ANYA)[-1])
    assert len(first) == 11 and first[-1] == [{"text": "→", "callback_data": "my:1"}]
    assert first[0][0]["text"].startswith("Рынок 22")  # newest subscription first

    handle_update(press(ANYA, "my:1", message_id=77))
    edit = bot.sent("editMessageText")[-1]
    assert edit["message_id"] == 77 and edit["chat_id"] == ANYA
    assert [b["text"] for b in keyboard(edit)[-1]] == ["←", "→"]
    assert len(keyboard(edit)) == 11

    handle_update(press(ANYA, "my:2", message_id=77))
    last = keyboard(bot.sent("editMessageText")[-1])
    assert len(last) == 4 and last[-1] == [{"text": "←", "callback_data": "my:1"}]
    assert len(bot.sent("sendMessage", ANYA)) == 1  # paging never sends a new message
    assert len(bot.sent("answerCallbackQuery")) == 2


def test_empty_list_and_stranger(bot, people):
    handle_update(text(ANYA, "/my"))
    reply = bot.sent("sendMessage", ANYA)[-1]
    assert "Пока ты ни за чем не следишь" in reply["text"]
    assert keyboard(reply)[0][0]["web_app"]["url"] == "https://example.test/app/"
    handle_update(text(STRANGER, "/my"))
    assert "Привет!" in bot.sent("sendMessage", STRANGER)[-1]["text"]


def test_start_has_my_markets_button_only_for_accounts(bot, people):
    handle_update(text(ANYA, "/start"))
    rows = keyboard(bot.sent("sendMessage", ANYA)[-1])
    assert rows[0][0]["text"] == "Открыть приложение"
    assert rows[1] == [{"text": "Мои рынки", "callback_data": "my:new"}]
    handle_update(text(STRANGER, "/start"))
    assert len(keyboard(bot.sent("sendMessage", STRANGER)[-1])) == 1

    handle_update(press(ANYA, "my:new"))
    assert bot.sent("sendMessage", ANYA)[-1]["text"].startswith("Пока ты")
    assert bot.sent("answerCallbackQuery")


def test_callbacks_still_reach_resolution_dialog(bot, people):
    with patch("market.bot_resolution_dialog.handle_callback") as dialog, \
            patch("market.bot_markets.handle_callback") as mine:
        handle_update(press(ANYA, "res:5:yes"))
        handle_update(press(ANYA, "disp:5"))
        handle_update(press(ANYA, "mk:5"))
    assert dialog.call_count == 2 and mine.call_count == 1


# --- animation ---------------------------------------------------------------------------------------

def test_chart_caption_with_position_and_upload_then_file_id(bot, render, people):
    anya, boris = people
    now = timezone.now()
    q = services.create_market(boris, title="Снег до декабря?", criteria="Росгидромет", category="fun",
                               closes_at=now + timedelta(days=2), resolves_at=now + timedelta(days=3))
    services.buy(anya, q, "yes", 100)
    q.refresh_from_db()
    shares = q.positions.get(trader=anya).yes_shares

    handle_update(press(ANYA, f"mk:{q.pk}"))
    method, files, data = bot.uploads[-1]
    assert method == "sendAnimation" and files["animation"][1] == b"mp4-bytes"
    lines = data["caption"].split("\n")
    assert lines[0] == "Снег до декабря?"
    assert lines[1].startswith(f"{round(q.prob * 100)}% · ▲ ") and lines[1].endswith(" за сутки")
    assert lines[2] == f"Твоя позиция: ДА · выплата {int(shares)}"
    assert data["reply_markup"] == {"inline_keyboard": [[
        {"text": "Открыть в приложении", "web_app": {"url": f"https://example.test/app/market/{q.pk}"}}]]}
    assert ChartAnimation.objects.get(question=q).file_id == "uploaded-id"
    assert bot.sent("answerCallbackQuery")

    # Same hour: no render, no upload, the stored file_id goes out.
    handle_update(press(ANYA, f"mk:{q.pk}"))
    assert render.call_count == 1 and len(bot.uploads) == 1
    assert bot.sent("sendAnimation", ANYA)[-1]["animation"] == "uploaded-id"


def test_next_hour_renders_again(bot, render, people):
    anya, _ = people
    q = market()
    hour = charts.hour_of(timezone.now())
    old = ChartAnimation.objects.create(question=q, hour=hour - timedelta(hours=1), file_id="old-id")
    bot_markets.send_chart(ANYA, anya, q)
    assert render.call_count == 1 and bot.uploads
    assert list(ChartAnimation.objects.filter(question=q).values_list("hour", flat=True)) == [hour]
    assert not ChartAnimation.objects.filter(pk=old.pk).exists()

    later = timezone.now() + timedelta(hours=1)
    charts.animation_for(q, now=later)
    assert render.call_count == 2


def test_document_file_id_is_used_when_no_animation(bot, render, people):
    anya, _ = people
    q = market()
    with patch("market.telegram.upload", return_value={"document": {"file_id": "doc-id"}}):
        bot_markets.send_chart(ANYA, anya, q)
    assert ChartAnimation.objects.get(question=q).file_id == "doc-id"


def test_caption_without_position(people):
    anya, _ = people
    q = market("Нейросеть и ЕГЭ")
    assert bot_markets.caption(anya, q).split("\n") == [
        "Нейросеть и ЕГЭ", "50% · без изменений за сутки", "Позиции нет — только слежу"]


def test_render_error_falls_back_to_text(bot, people):
    anya, _ = people
    q = market()
    track(anya, q)
    with patch("market.charts.render_animation", side_effect=charts.ChartError("нет ffmpeg")):
        handle_update(press(ANYA, f"mk:{q.pk}"))
    reply = bot.sent("sendMessage", ANYA)[-1]
    assert reply["text"].startswith("Будет снег?\n50%")
    assert keyboard(reply)[0][0]["text"] == "Открыть в приложении"
    assert not bot.uploads and not ChartAnimation.objects.exists()


def test_others_private_market_is_not_found(bot, render, people):
    _, boris = people
    group = Group.objects.create(name="Закрытая", owner=boris)
    q = market("Секрет", group=group)
    handle_update(press(ANYA, f"mk:{q.pk}"))
    assert bot.sent("answerCallbackQuery")[-1]["text"] == "Рынок не найден."
    assert not render.called and not bot.uploads and not bot.sent("sendMessage", ANYA)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe is not installed")
def test_real_render_is_short_silent_h264(tmp_path):
    data = charts.render_animation([0.5 + i / 200 for i in range(charts.SAMPLES)], 5)
    path = tmp_path / "a.mp4"
    path.write_bytes(data)
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, check=True).stdout)
    (stream,) = info["streams"]  # no audio track
    assert stream["codec_name"] == "h264" and stream["pix_fmt"] == "yuv420p"
    assert (stream["width"], stream["height"]) == (charts.WIDTH, charts.HEIGHT)
    assert float(info["format"]["duration"]) <= 3.0


# --- share -------------------------------------------------------------------------------------------

@pytest.fixture
def still():
    with patch("market.charts.render_still", return_value=b"jpeg") as fake:
        yield fake


def test_inline_query_empty_lists_tracked(bot, still, people):
    anya, _ = people
    q = market("Будет снег?")
    track(anya, q)
    handle_update({"inline_query": {"id": "iq1", "from": {"id": ANYA}, "query": "", "offset": ""}})
    answer = bot.sent("answerInlineQuery")[-1]
    assert answer["is_personal"] is True and answer["cache_time"] <= 60
    (result,) = answer["results"]
    assert result["type"] == "photo" and result["id"] == f"m{q.pk}"
    assert result["photo_url"].startswith("https://example.test/media/share/market-")
    assert result["caption"].startswith("Будет снег?\nВероятность ДА: 50%")
    assert result["reply_markup"]["inline_keyboard"][0][0] == {
        "text": "Открыть в приложении", "url": f"https://t.me/predbot/app?startapp=ref_code{ANYA}-m{q.pk}"}


def test_inline_query_search_and_limits(bot, still, people):
    anya, boris = people
    group = Group.objects.create(name="Закрытая", owner=boris)
    market("Снег в секрете", group=group)
    market("Снег закрыт", status=Question.Status.CLOSED)
    for i in range(12):
        market(f"Снег {i}")
    other = market("Дождь")
    handle_update({"inline_query": {"id": "iq2", "from": {"id": ANYA}, "query": "Снег"}})
    titles = [r["title"] for r in bot.sent("answerInlineQuery")[-1]["results"]]
    assert len(titles) == 10 and all(t.startswith("Снег ") and t[5:].isdigit() for t in titles)

    handle_update({"inline_query": {"id": "iq3", "from": {"id": ANYA}, "query": f"m{other.pk}"}})
    assert [r["title"] for r in bot.sent("answerInlineQuery")[-1]["results"]] == ["Дождь"]

    handle_update({"inline_query": {"id": "iq4", "from": {"id": STRANGER}, "query": ""}})
    assert bot.sent("answerInlineQuery")[-1]["results"] == []


def test_inline_query_skips_chat_markets(bot, still, people):
    anya, _ = people
    chat = Group.objects.create(name="Группа ПИ-24", kind="chat", telegram_chat_id=-100777, bot_active=True)
    GroupMembership.objects.create(group=chat, trader=anya, status="active", checked_at=timezone.now())
    secret = market("Снег в чате", group=chat)
    public = market("Снег для всех")
    track(anya, secret, public)

    handle_update({"inline_query": {"id": "iq5", "from": {"id": ANYA}, "query": ""}})
    assert [r["title"] for r in bot.sent("answerInlineQuery")[-1]["results"]] == ["Снег для всех"]
    handle_update({"inline_query": {"id": "iq6", "from": {"id": ANYA}, "query": "Снег"}})
    assert [r["title"] for r in bot.sent("answerInlineQuery")[-1]["results"]] == ["Снег для всех"]
    handle_update({"inline_query": {"id": "iq7", "from": {"id": ANYA}, "query": f"m{secret.pk}"}})
    assert bot.sent("answerInlineQuery")[-1]["results"] == []


def test_article_result_without_https(bot, still, people, settings):
    anya, _ = people
    settings.SITE_URL = "http://127.0.0.1:8000"
    q = market()
    result = share.inline_result(anya, q)
    assert result["type"] == "article" and "photo_url" not in result
    assert result["input_message_content"]["message_text"].startswith("Будет снег?\nВероятность ДА")
    assert not still.called


def test_share_endpoint(bot, still, client, people):
    anya, _ = people
    q = market()
    api = Api(client, ANYA)
    code, data = api.post(f"markets/{q.pk}/share")
    assert code == 200 and data == {"prepared_message_id": "prep-1"}
    call = bot.sent("savePreparedInlineMessage")[-1]
    assert call["user_id"] == ANYA and call["allow_user_chats"] and call["allow_group_chats"]
    assert call["allow_channel_chats"] and call["result"]["id"] == f"m{q.pk}"
    assert Event.objects.filter(trader=anya, name="share", props__market=q.pk).exists()


def test_share_endpoint_503_and_private(client, people):
    _, boris = people
    q = market()
    api = Api(client, ANYA)
    with patch("market.telegram.api", side_effect=TelegramError("нет связи")), \
            patch("market.charts.render_still", return_value=b"jpeg"):
        code, data = api.post(f"markets/{q.pk}/share")
    assert code == 503 and data["error"]
    assert not Event.objects.filter(name="share").exists()

    group = Group.objects.create(name="Закрытая", owner=boris)
    secret = market("Секрет", group=group)
    assert api.post(f"markets/{secret.pk}/share")[0] == 404


def test_referrer_from_market_link(db):
    inviter = make("Аня", ANYA)
    assert _referrer(f"ref_{inviter.referral_code}-m5") == inviter
    assert _referrer(f"ref_{inviter.referral_code}") == inviter
    assert _referrer("m5") is None
    assert _referrer(f"ref_{inviter.referral_code}-mx") is None
    assert _referrer("ref_-m5") is None
    assert _referrer("ref_nobody-m5") is None


def test_setup_bot_sets_commands(bot):
    call_command("setup_bot")
    commands = bot.sent("setMyCommands")[-1]["commands"]
    assert {"command": "my", "description": "Мои рынки"} in commands
    assert commands[0]["command"] == "start"


def test_upload_sends_multipart_with_json_fields():
    from unittest.mock import MagicMock
    from market import telegram
    response = MagicMock(ok=True)
    response.json.return_value = {"ok": True, "result": {"message_id": 3}}
    with patch("market.telegram.requests.post", return_value=response) as post:
        result = telegram.upload("sendAnimation", {"animation": ("a.mp4", b"x", "video/mp4")},
                                 chat_id=5, caption="Тест", reply_markup={"inline_keyboard": []}, duration=3)
    assert result == {"message_id": 3}
    kwargs = post.call_args.kwargs
    assert kwargs["files"] == {"animation": ("a.mp4", b"x", "video/mp4")}
    assert kwargs["data"] == {"chat_id": "5", "caption": "Тест", "reply_markup": '{"inline_keyboard": []}',
                              "duration": "3"}
