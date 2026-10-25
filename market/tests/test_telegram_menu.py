from datetime import timedelta
from unittest.mock import patch
import pytest
from django.utils import timezone
from market.models import Trader, Question, Position, Submission
from market.telegram import handle_update


def dispatch(text):
    handle_update({"message": {"chat": {"id": 123, "type": "private"}, "text": text}})


def texts(send):
    return "\n".join(call.args[1] for call in send.call_args_list)


@pytest.mark.django_db
def test_menu_and_stats():
    Trader.objects.create(name="Menu", pseudonym="Menu", telegram_chat_id=123)
    with patch("market.telegram.send") as send:
        dispatch("/start")
        assert send.call_args.kwargs["reply_markup"]["inline_keyboard"][0][0]["web_app"]
        dispatch("/menu")
        assert send.call_args.kwargs["reply_markup"]["keyboard"]
        dispatch("📈 Статистика")
        assert "Точность: пока нет результатов" in texts(send)
        dispatch("💰 Баланс")
        assert "Баланс: 1000" in texts(send)


@pytest.mark.django_db
def test_market_types_and_expired_markets():
    Trader.objects.create(name="Menu", pseudonym="Menu", telegram_chat_id=123)
    Question.objects.create(title="Fresh poll", kind="poll")
    Question.objects.create(title="Expired market", deadline=timezone.now()-timedelta(minutes=1))
    with patch("market.telegram.send") as send:
        dispatch("📊 Рынки")
        assert "Fresh poll" in texts(send)
        assert "Вероятность ДА" not in texts(send)
        assert "Expired market" not in texts(send)


@pytest.mark.django_db
def test_personal_data_is_scoped_to_trader():
    trader = Trader.objects.create(name="Menu", pseudonym="Menu", telegram_chat_id=123)
    other = Trader.objects.create(name="Other", pseudonym="Other")
    mine = Question.objects.create(title="My question", creator=trader)
    theirs = Question.objects.create(title="Other question", creator=other)
    Position.objects.create(trader=trader, question=mine, yes_shares=12)
    Position.objects.create(trader=other, question=theirs, no_shares=99)
    poll = Question.objects.create(title="My answer", kind="poll")
    Submission.objects.create(trader=trader, question=poll, value="A")
    with patch("market.telegram.send") as send:
        dispatch("🎯 Мои ставки")
        assert "Акции ДА: 12.00" in texts(send)
        assert "Ваш ответ: A" in texts(send)
        assert "Other question" not in texts(send)
        send.reset_mock()
        dispatch("📝 Мои вопросы")
        assert "My question" in texts(send)
        assert "Other question" not in texts(send)
