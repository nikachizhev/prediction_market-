from unittest.mock import patch, Mock
import pytest
from market import moderation_bot as bot
from market.models import ModerationPanel, ModerationDelivery, Question
from market.tests.test_moderation_bot import callback, message, complain

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def config(settings):
    settings.MODERATION_BOT_TOKEN = 'MODERATION-UI'
    settings.MODERATION_ADMIN_IDS = ('123',)
    settings.TELEGRAM_BOT_TOKEN = 'PUBLIC'
    settings.SITE_URL = 'https://example.test'


@pytest.fixture
def transport():
    calls = []
    def api(method, **kwargs):
        calls.append((method, kwargs))
        if method == 'sendMessage':
            return {'message_id': 100+len(calls)}
        return {'message_id': kwargs.get('message_id')}
    with patch.object(bot, 'api', side_effect=api):
        yield calls


def click(data, message_id):
    update = callback(data)
    update['callback_query']['message']['message_id'] = message_id
    bot.handle_update(update)


def test_commands_refresh_panel_and_inline_navigation_reuses_it(transport):
    bot.handle_update(message(text='/start'))
    panel_id = ModerationPanel.objects.get().message_id
    bot.handle_update(message(text='/queue'))
    bot.handle_update(message(text='/history'))
    current_id = ModerationPanel.objects.get().message_id
    assert current_id != panel_id
    click('mb:queue:0', current_id)
    click('mb:history:0', current_id)
    click('mb:home', current_id)
    assert sum(method == 'sendMessage' for method, _ in transport) == 3
    assert sum(method == 'deleteMessage' for method, _ in transport) == 2
    assert sum(method == 'editMessageText' for method, _ in transport) == 3
    assert all(data['message_id'] == current_id for method, data in transport if method == 'editMessageText')


@pytest.mark.parametrize('command', ['/start', '/queue', '/history', '/id'])
def test_commands_recover_after_client_clears_history_even_if_edits_would_succeed(transport, command):
    bot.show_home(123)
    old = ModerationPanel.objects.get().message_id
    transport.clear()
    bot.handle_update(message(text=command))
    assert any(method == 'sendMessage' for method, _ in transport)
    assert not any(method == 'editMessageText' for method, _ in transport)
    assert ModerationPanel.objects.get().message_id != old


def test_cannot_delete_old_panel_still_sends_fresh_response(transport):
    bot.show_home(123)
    def api(method, **kwargs):
        if method == 'deleteMessage':
            raise bot.ModerationTelegramError('Already deleted')
        if method == 'editMessageText':
            pytest.fail('A command should send a new panel, not edit the invisible one')
        return {'message_id': 999}
    with patch.object(bot, 'api', side_effect=api):
        bot.handle_update(message(text='/start'))
    assert ModerationPanel.objects.get().message_id == 999


def test_queue_is_one_list_and_card_confirmation_result_edit_in_place(transport):
    for number in range(7):
        q = Question.objects.create(title=f'Market {number}')
        complain(q)
    bot.show_queue(123)
    assert len(transport) == 1
    panel_id = ModerationPanel.objects.get().message_id
    buttons = transport[-1][1]['reply_markup']['inline_keyboard']
    assert sum(row[0]['callback_data'].startswith('mb:open:') for row in buttons) == 5
    click('mb:queue:1', panel_id)
    assert 'Страница 2' in transport[-1][1]['text']
    q = Question.objects.first()
    report = q.complaints.first()
    click(f'mb:open:{q.pk}', panel_id)
    assert 'Критерий результата' in transport[-1][1]['text']
    click(f'mb:ask:hide:{report.pk}', panel_id)
    assert 'ПОДТВЕРЖДЕНИЕ' in transport[-1][1]['text']
    click(f'mb:do:hide:{report.pk}', panel_id)
    assert '✅ Рынок скрыт' in transport[-1][1]['text']
    assert sum(method == 'sendMessage' for method, _ in transport) == 1


def test_notifications_use_one_compact_message_separate_from_panel(transport):
    q = Question.objects.create(title='Complaint test')
    bot.show_home(123)
    panel_id = ModerationPanel.objects.get().message_id
    complain(q, 'The whole reason belongs in the card, not every notification')
    bot.flush_notifications()
    notice_id = ModerationPanel.objects.get().notice_id
    assert notice_id != panel_id
    assert 'УВЕДОМЛЕНИЕ' in transport[-1][1]['text']
    assert 'The whole reason' not in transport[-1][1]['text']
    complain(q)
    bot.flush_notifications()
    assert transport[-1][0] == 'editMessageText' and transport[-1][1]['message_id'] == notice_id
    assert sum(method == 'sendMessage' for method, _ in transport) == 2
    assert ModerationDelivery.objects.filter(sent_at__isnull=False).count() == 2


def test_opening_notice_replaces_old_panel_only(transport):
    bot.show_home(123)
    old = ModerationPanel.objects.get().message_id
    q = Question.objects.create(title='Test')
    complain(q)
    bot.flush_notifications()
    notice = ModerationPanel.objects.get().notice_id
    click('mb:queue:0', notice)
    panel = ModerationPanel.objects.get()
    assert panel.message_id == notice and panel.notice_id is None
    deleted = [data['message_id'] for method, data in transport if method == 'deleteMessage']
    assert deleted == [old]
    assert transport[-1][0] == 'editMessageText'


def test_deleted_panel_is_recreated_but_network_error_does_not_spam(transport):
    bot.show_home(123)
    with patch.object(bot, 'api', side_effect=bot.ModerationTelegramError('Offline')) as api:
        with pytest.raises(bot.ModerationTelegramError):
            bot.show_queue(123)
        assert api.call_count == 1 and api.call_args.args[0] == 'editMessageText'
    def missing(method, **kwargs):
        if method == 'editMessageText':
            raise bot.MissingMessage('Deleted')
        return {'message_id': 999}
    with patch.object(bot, 'api', side_effect=missing):
        bot.show_queue(123)
    assert ModerationPanel.objects.get().message_id == 999


def test_not_modified_response_does_not_create_duplicate():
    response = Mock(ok=False)
    response.json.return_value = {'ok': False, 'error_code': 400, 'description': 'Bad Request: message is not modified'}
    with patch('market.moderation_bot.requests.post', return_value=response) as post:
        assert bot.api('editMessageText', message_id=111)['message_id'] == 111
        assert post.call_count == 1


def test_panel_isolation_between_admins_and_tokens(transport, settings):
    bot.show_home(123)
    first = ModerationPanel.objects.get().message_id
    bot.show_home(456)
    settings.MODERATION_BOT_TOKEN = 'ANOTHER-BOT'
    bot.show_home(123)
    assert ModerationPanel.objects.count() == 3
    assert all(data.get('message_id') != first for method, data in transport if method == 'deleteMessage')


def test_decision_buttons_describe_market_action_and_consequences(transport):
    q = Question.objects.create(title='Clear labels')
    report = complain(q)
    text, buttons = bot.card(q)
    assert buttons[0][0]['text'] == '🚫 Скрыть рынок — есть нарушение'
    assert buttons[1][0]['text'] == '✅ Оставить рынок — нарушения нет'
    bot.handle_update(callback(f'mb:ask:hide:{report.pk}'))
    assert 'Вы считаете жалобы обоснованными' in transport[-1][1]['text']
    assert transport[-1][1]['reply_markup']['inline_keyboard'][0][0]['text'] == 'Да, скрыть рынок'
    bot.handle_update(callback(f'mb:ask:dismiss:{report.pk}'))
    assert 'необоснованными' in transport[-1][1]['text']
    assert transport[-1][1]['reply_markup']['inline_keyboard'][0][0]['text'] == 'Да, оставить рынок'


def test_dismissing_new_reports_never_promises_to_restore_moderator_hidden_market(transport):
    from market import moderation
    q = Question.objects.create(title='Already hidden')
    original = complain(q)
    moderation.review(original.pk, 'hide', telegram_id=123)
    q.refresh_from_db()
    report = complain(q)
    _, buttons = bot.card(q)
    assert buttons[1][0]['text'] == '✅ Новые жалобы без оснований'
    bot.handle_update(callback(f'mb:ask:dismiss:{report.pk}'))
    assert 'ОСТАНЕТСЯ СКРЫТЫМ' in transport[-1][1]['text']
    assert transport[-1][1]['reply_markup']['inline_keyboard'][0][0]['text'] == 'Закрыть новые жалобы'
