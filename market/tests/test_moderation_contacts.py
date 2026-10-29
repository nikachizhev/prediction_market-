from unittest.mock import patch

import pytest
from market import moderation_bot as bot
from market.models import ModerationContact
from market.tests.test_moderation_bot import message, callback

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def config(settings):
    settings.MODERATION_ADMIN_IDS = ('123',)
    settings.MODERATION_BOT_TOKEN = 'CONTACTS-TEST'


def test_denied_user_recorded_before_delivery_failure():
    update = message(user=999, text='secret message')
    update['message']['from'].update(first_name='Иван', last_name='Петров', username='ivan')
    with patch.object(bot, 'send', side_effect=bot.ModerationTelegramError):
        with pytest.raises(bot.ModerationTelegramError):
            bot.handle_update(update)
    contact = ModerationContact.objects.get(pk=999)
    assert contact.name == 'Иван Петров' and contact.username == 'ivan'
    first = contact.first_seen
    update['message']['from']['username'] = 'new_name'
    with patch.object(bot, 'send'):
        bot.handle_update(update)
    contact.refresh_from_db()
    assert contact.first_seen == first and contact.username == 'new_name'
    assert ModerationContact.objects.count() == 1


def test_private_humans_only():
    with patch.object(bot, 'api'), patch.object(bot, 'send'):
        bot.handle_update(message(user=999, chat=-10, chat_type='group'))
        update = message(user=999)
        update['message']['from']['is_bot'] = True
        bot.handle_update(update)
    assert not ModerationContact.objects.exists()


def test_journal_admin_only_and_page_navigation():
    for number in range(10):
        ModerationContact.objects.create(telegram_id=1000+number, name=f'User {number}')
    with patch.object(bot, 'send'), patch.object(bot, 'render') as render:
        bot.handle_update(message(user=999, text='/contacts'))
        render.assert_not_called()
    with patch.object(bot, 'renew_panel'), patch.object(bot, 'render') as render:
        bot.handle_update(message(text='/contacts'))
    assert 'КТО ПИСАЛ БОТУ' in render.call_args.args[1]
    assert 'mb:contacts:1' in str(render.call_args.args[2])
    with patch.object(bot, 'api'), patch.object(bot, 'render') as render:
        bot.handle_update(callback('mb:contacts:1'))
    assert 'Страница 2' in render.call_args.args[1]
    assert 'Telegram ID:' in render.call_args.args[1]
    with patch.object(bot, 'api'), patch.object(bot, 'render') as render:
        bot.handle_update(callback('mb:contacts:0', user=999))
        render.assert_not_called()
