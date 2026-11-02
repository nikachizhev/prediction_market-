"""Regression checks for reported cross-account notification updates."""
import pytest
from django.utils import timezone
from market.models import Notification, Trader
from market.tests.test_api import Api
from market.tests.test_miniapp_auth import TOKEN

pytestmark = pytest.mark.django_db


@pytest.fixture
def account(client, settings):
    settings.TELEGRAM_BOT_TOKEN = TOKEN
    api = Api(client, 99001)
    api.post('session')
    return api, Trader.objects.get(telegram_chat_id=99001)


@pytest.mark.parametrize('mode', ['all_ids', 'omitted', 'null'])
def test_766_notifications_do_not_cross_account_boundary(account, mode):
    api, me = account
    other = Trader.objects.create(name='Other', pseudonym='Other', telegram_chat_id=99002)
    Notification.objects.bulk_create([
        Notification(recipient=me if i < 3 else other, key=f'report-{i}', text='Test')
        for i in range(766)
    ])
    data = {} if mode == 'omitted' else {'ids': None} if mode == 'null' else {
        'ids': list(Notification.objects.values_list('pk', flat=True))}
    assert api.post('notifications/read', data) == (200, {'unread': 0})
    assert Notification.objects.filter(recipient=me, read_at__isnull=False).count() == 3
    assert Notification.objects.filter(recipient=other, read_at__isnull=True).count() == 763


@pytest.mark.parametrize('ids', [[True], ['1'], [1.5], {}, 'all'])
def test_invalid_ids_do_not_change_anything(account, ids):
    api, me = account
    item = Notification.objects.create(recipient=me, key='mine', text='Test')
    assert api.post('notifications/read', {'ids': ids})[0] == 400
    item.refresh_from_db()
    assert item.read_at is None


def test_empty_or_foreign_ids_are_not_marked_and_response_is_remaining_unread(account):
    api, me = account
    other = Trader.objects.create(name='Other', pseudonym='Other')
    mine = Notification.objects.create(recipient=me, key='mine', text='Test')
    theirs = Notification.objects.create(recipient=other, key='theirs', text='Test')
    for ids in ([], [theirs.pk], [0, -1, 99999999]):
        assert api.post('notifications/read', {'ids': ids}) == (200, {'unread': 1})
    assert not Notification.objects.filter(read_at__isnull=False).exists()
    assert api.post('notifications/read', {'ids': [mine.pk, mine.pk, theirs.pk]}) == (200, {'unread': 0})
    theirs.refresh_from_db()
    assert theirs.read_at is None


def test_unauthenticated_read_has_no_effect(account, client):
    _, me = account
    item = Notification.objects.create(recipient=me, key='mine', text='Test')
    response = client.post('/api/notifications/read', data='{}', content_type='application/json')
    assert response.status_code == 401
    item.refresh_from_db()
    assert item.read_at is None
