from datetime import timedelta
from io import StringIO
from unittest.mock import patch, Mock

import pytest
import requests
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.core.management import call_command, CommandError
from django.test import override_settings
from django.utils import timezone
from market import moderation, moderation_bot as bot, services
from market.models import MarketComplaint, ModerationAction, ModerationDelivery, ModerationBotState, Question, Trader

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def config(settings):
    settings.MODERATION_BOT_TOKEN = '555:MODERATION-TEST'
    settings.TELEGRAM_BOT_TOKEN = '111:PUBLIC-TEST'
    settings.MODERATION_ADMIN_IDS = ('123', '456')
    settings.SITE_URL = 'https://example.test'


@pytest.fixture
def question():
    return Question.objects.create(title='Проверяемый рынок', resolution_criteria='Проверяемое событие')


def complain(question, reason='Спам'):
    user = Trader.objects.create(name='Reporter', pseudonym='Reporter')
    return MarketComplaint.objects.create(question=question, trader=user, reason=reason)


def message(user=123, text='/queue', chat=None, chat_type='private'):
    return {'message': {'from': {'id': user}, 'chat': {'id': user if chat is None else chat, 'type': chat_type}, 'text': text}}


def callback(data, user=123, chat=None, chat_type='private'):
    return {'callback_query': {'id': 'cb1', 'from': {'id': user}, 'data': data,
                              'message': {'chat': {'id': user if chat is None else chat, 'type': chat_type}}}}


def test_private_transport_uses_own_token_and_hides_network_secrets():
    response = Mock(ok=True)
    response.json.return_value = {'ok': True, 'result': {'message_id': 1}}
    with patch('market.moderation_bot.requests.post', return_value=response) as post:
        bot.api('sendMessage', chat_id=123, text='Test')
    assert post.call_args.args[0] == 'https://api.telegram.org/bot555:MODERATION-TEST/sendMessage'
    with patch('market.moderation_bot.requests.post', side_effect=requests.ConnectionError('secret URL token')):
        with pytest.raises(bot.ModerationTelegramError) as error:
            bot.api('getMe')
    assert 'secret' not in str(error.value) and 'MODERATION-TEST' not in str(error.value)


@pytest.mark.parametrize('token', ['', '111:PUBLIC-TEST'])
def test_transport_cannot_use_public_token(settings, token):
    settings.MODERATION_BOT_TOKEN = token
    with patch('market.moderation_bot.requests.post') as post:
        with pytest.raises(bot.ModerationTelegramError):
            bot.api('getMe')
        post.assert_not_called()


@pytest.mark.parametrize('ids', [(), ('oops',), ('-1',), ('123', 'oops')])
def test_invalid_allowlist_fails_closed(settings, ids):
    settings.MODERATION_ADMIN_IDS = ids
    assert not moderation.admin_ids()
    with pytest.raises(CommandError):
        call_command('run_moderation_bot', once=True)


@pytest.mark.parametrize('user,chat,chat_type', [(999, None, 'private'), (123, -10, 'group'), (123, 456, 'private')])
def test_outsiders_and_groups_cannot_read_or_decide(question, user, chat, chat_type):
    report = complain(question, 'PRIVATE REASON')
    with patch.object(bot, 'api', return_value={}) as api:
        bot.handle_update(message(user, chat=chat, chat_type=chat_type))
        bot.handle_update(callback(f'mb:do:hide:{report.pk}', user, chat, chat_type))
    assert 'PRIVATE REASON' not in str(api.call_args_list)
    assert not ModerationAction.objects.exists()
    report.refresh_from_db()
    assert report.status == 'pending'


def test_queue_contains_reports_and_asks_confirmation(question):
    report = complain(question, '<b>Причина без HTML</b>')
    with patch.object(bot, 'api', return_value={}) as api:
        bot.handle_update(message())
        bot.handle_update(callback(f'mb:open:{question.pk}'))
        assert 'Причина без HTML' in str(api.call_args_list)
        bot.handle_update(callback(f'mb:ask:hide:{report.pk}'))
        assert not ModerationAction.objects.exists()
        assert f'mb:do:hide:{report.pk}' in str(api.call_args_list)
        assert not any('parse_mode' in call.kwargs for call in api.call_args_list)


def test_review_batches_all_pending_and_old_buttons_are_safe(question):
    one, two = complain(question), complain(question, 'Запрещённая тема')
    player = Trader.objects.create(name='Player', pseudonym='Player')
    services.buy(player, question, 'yes', 10)
    player.refresh_from_db()
    balance = player.balance
    with patch.object(bot, 'api', return_value={}):
        bot.handle_update(callback(f'mb:do:hide:{one.pk}'))
        bot.handle_update(callback(f'mb:do:dismiss:{two.pk}', user=456))
    question.refresh_from_db()
    assert question.hidden_at and question.moderation_hidden
    assert question.complaints.filter(status='upheld', reviewed_by='telegram:123').count() == 2
    assert ModerationAction.objects.count() == 1
    player.refresh_from_db()
    assert player.balance == balance and question.status == 'open' and not question.payouts.exists()
    with pytest.raises(services.MarketError, match='скрыт'):
        services.buy(player, question, 'yes', 10)


def test_reject_restores_auto_hidden_not_a_confirmed_hidden_market(question):
    report = complain(question)
    question.hidden_at = timezone.now()
    question.save()
    moderation.review(report.pk, 'dismiss', telegram_id=123)
    question.refresh_from_db()
    assert not question.hidden_at and report.__class__.objects.count() == 1
    new = complain(question)
    moderation.review(new.pk, 'hide', telegram_id=456)
    another = complain(question)
    moderation.review(another.pk, 'dismiss', telegram_id=123)
    question.refresh_from_db()
    assert question.hidden_at and question.moderation_hidden


def test_explicit_restore_checks_latest_decision_and_pending_reports(question):
    report = complain(question)
    moderation.review(report.pk, 'hide', telegram_id=123)
    version = question.moderation_actions.first().pk
    with pytest.raises(services.MarketError, match='изменилось'):
        moderation.restore(question.pk, expected_action=0, telegram_id=123)
    another = complain(question)
    with pytest.raises(services.MarketError, match='новые жалобы'):
        moderation.restore(question.pk, expected_action=version, telegram_id=123)
    moderation.review(another.pk, 'dismiss', telegram_id=123)
    version = question.moderation_actions.first().pk
    with patch.object(bot, 'api', return_value={}):
        bot.handle_update(callback(f'mb:ask:restore:{question.pk}:{version}'))
        question.refresh_from_db()
        assert question.hidden_at
        bot.handle_update(callback(f'mb:do:restore:{question.pk}:{version}'))
    question.refresh_from_db()
    assert not question.hidden_at and not question.moderation_hidden
    assert question.complaints.count() == 2 and question.moderation_actions.count() == 3


def test_service_checks_permissions_before_looking_up_report():
    with pytest.raises(PermissionDenied):
        moderation.review(9999, 'hide', telegram_id=999)
    regular = User.objects.create_user('not-moderator')
    with pytest.raises(PermissionDenied):
        moderation.restore(9999, user=regular)


def test_delivery_includes_old_reports_is_per_admin_and_retries(question):
    report = complain(question)
    with patch.object(bot, 'api', side_effect=bot.ModerationTelegramError('offline')):
        assert bot.flush_notifications() == 0
    assert ModerationDelivery.objects.count() == 2
    assert not ModerationDelivery.objects.filter(sent_at__isnull=False).exists()
    ModerationDelivery.objects.update(next_attempt=timezone.now()-timedelta(seconds=1))
    with patch.object(bot, 'api', return_value={}) as api:
        assert bot.flush_notifications() == 2
        assert {call.kwargs['chat_id'] for call in api.call_args_list} == {123, 456}
        assert bot.flush_notifications() == 0
        assert api.call_count == 2
    report.refresh_from_db()
    assert report.status == 'pending'


def test_removed_admin_stops_receiving_and_new_admin_gets_backlog(question, settings):
    complain(question)
    with patch.object(bot, 'api', side_effect=bot.ModerationTelegramError('offline')):
        bot.flush_notifications()
    ModerationDelivery.objects.update(next_attempt=timezone.now()-timedelta(seconds=1))
    settings.MODERATION_ADMIN_IDS = ('789',)
    with patch.object(bot, 'api', return_value={}) as api:
        assert bot.flush_notifications() == 1
        assert api.call_args.kwargs['chat_id'] == 789


def test_delivery_lease_prevents_parallel_duplicate(question):
    complain(question)
    settings_patch = override_settings(MODERATION_ADMIN_IDS=('123',))
    with settings_patch:
        def send_during_delivery(*args, **kwargs):
            assert bot.flush_notifications() == 0
            return {}
        with patch.object(bot, 'api', side_effect=send_during_delivery) as api:
            assert bot.flush_notifications() == 1 and api.call_count == 1


def test_command_keeps_offset_across_restarts():
    update = message(text='/id')
    update['update_id'] = 52
    def api(method, **kwargs):
        return {} if method == 'getWebhookInfo' else [update] if method == 'getUpdates' else {}
    with patch.object(bot, 'api', side_effect=api), patch.object(bot, 'handle_update') as handle:
        call_command('run_moderation_bot', once=True, stdout=StringIO())
        call_command('run_moderation_bot', once=True, stdout=StringIO())
        assert handle.call_count == 1
    assert ModerationBotState.objects.get().offset == 53


def test_existing_webhook_is_not_deleted():
    with patch.object(bot, 'api', return_value={'url': 'https://example.test/webhook'}) as api:
        with pytest.raises(CommandError, match='webhook'):
            call_command('run_moderation_bot', once=True)
        assert api.call_count == 1


def test_long_emoji_reasons_fit_telegram_limit(question):
    for _ in range(6):
        complain(question, '😀'*300)
    with patch.object(bot, 'api', return_value={}) as api:
        bot.show_card(123, question)
    assert len(api.call_args.kwargs['text'].encode('utf-16-le')) <= 7800


def test_admin_actions_preserve_history(client, question):
    user = User.objects.create_superuser('moderation-admin', password='test')
    client.force_login(user)
    report = complain(question)
    from django.urls import reverse
    response = client.post(reverse('admin:market_marketcomplaint_changelist'),
        {'action': 'uphold', '_selected_action': str(report.pk)})
    assert response.status_code == 302
    report.refresh_from_db()
    assert report.status == 'upheld' and report.reviewed_by == f'django:{user.pk}'
    client.post(reverse('admin:market_question_changelist'), {'action': 'unhide', '_selected_action': str(question.pk)})
    question.refresh_from_db()
    assert not question.hidden_at and question.complaints.count() == 1


def test_reviewed_complaints_do_not_count_toward_auto_hide(client, settings, question):
    from market.tests.test_api import Api
    from market.tests.test_miniapp_auth import TOKEN
    settings.TELEGRAM_BOT_TOKEN = TOKEN
    for _ in range(3):
        complain(question)
    moderation.review(question.complaints.first().pk, 'dismiss', telegram_id=123)
    api = Api(client, 9001)
    api.post('session')
    assert api.post(f'markets/{question.pk}/report', {'reason': 'Новая жалоба'})[0] == 200
    question.refresh_from_db()
    assert not question.hidden_at and question.complaints.filter(status='pending').count() == 1


@pytest.mark.django_db(transaction=True)
def test_two_moderators_cannot_overwrite_each_other():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import connection, close_old_connections
    if connection.vendor != 'postgresql':
        pytest.skip('Requires PostgreSQL row locks')
    question = Question.objects.create(title='Concurrent moderation')
    first, second = complain(question), complain(question)
    barrier = Barrier(2)

    def decide(args):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
            barrier.wait(timeout=10)
            report_id, action, admin_id = args
            try:
                moderation.review(report_id, action, telegram_id=admin_id)
                return action
            except services.MarketError:
                return 'already-reviewed'
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(decide, [(first.pk, 'hide', 123), (second.pk, 'dismiss', 456)]))
    assert outcomes.count('already-reviewed') == 1
    assert ModerationAction.objects.count() == 1
    assert MarketComplaint.objects.values('status').distinct().count() == 1
