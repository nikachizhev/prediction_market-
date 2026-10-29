from datetime import timedelta
from unittest.mock import patch, Mock

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from market import services, moderation, moderation_bot as bot, disputes_bot
from market.models import Question, Trader, DisputeDecision, DisputeDelivery, LedgerEntry
from market.tests.test_moderation_bot import callback, message

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def config(settings):
    settings.MODERATION_ADMIN_IDS = ('123', '456')
    settings.MODERATION_BOT_TOKEN = 'MOD-TEST'
    settings.TELEGRAM_BOT_TOKEN = 'MAIN-TEST'
    settings.SITE_URL = 'https://example.test'


@pytest.fixture
def disputed():
    creator, buyer, other = [Trader.objects.create(name=x, pseudonym=x) for x in ('Author', 'Buyer', 'Other')]
    now = timezone.now()
    q = services.create_market(creator, title='Dispute test', criteria='Official result', category='fun',
        closes_at=now+timedelta(hours=2), resolves_at=now+timedelta(hours=3))
    services.buy(buyer, q, 'yes', 100)
    services.buy(other, q, 'no', 50)
    Question.objects.filter(pk=q.pk).update(deadline=now-timedelta(minutes=1))
    q.refresh_from_db()
    services.propose_resolution(creator, q, 'yes', evidence_file_id='proof-id', note='Official screenshot')
    dispute = services.open_dispute(buyer, q, 'The actual outcome is NO')
    q.refresh_from_db()
    return q, dispute, buyer, other


@pytest.mark.parametrize('decision,upheld,status', [('yes', False, 'resolved'), ('no', True, 'resolved'), ('cancel', True, 'annulled')])
def test_settlement_and_repeat_guard(disputed, decision, upheld, status):
    q, dispute, buyer, _ = disputed
    moderation.decide_dispute(q.pk, decision, expected_dispute=dispute.pk, telegram_id=123)
    dispute.refresh_from_db()
    q.refresh_from_db()
    assert dispute.upheld is upheld
    assert q.status == status and q.resolution_state == 'final'
    assert buyer.ledger.filter(kind=LedgerEntry.Kind.DISPUTE_REFUND).exists() is upheld
    assert DisputeDecision.objects.get(question=q).actor == 'telegram:123'
    balance = list(Trader.objects.values_list('pk', 'balance'))
    with pytest.raises(services.MarketError):
        moderation.decide_dispute(q.pk, decision, expected_dispute=dispute.pk, telegram_id=456)
    assert list(Trader.objects.values_list('pk', 'balance')) == balance


def test_new_dispute_invalidates_confirmation(disputed):
    q, dispute, _, other = disputed
    services.open_dispute(other, q, 'Another argument')
    with pytest.raises(services.MarketError, match='изменился'):
        moderation.decide_dispute(q.pk, 'no', expected_dispute=dispute.pk, telegram_id=123)
    assert not DisputeDecision.objects.exists()


def test_unauthorized_cannot_decide(disputed):
    q, dispute, *_ = disputed
    with pytest.raises(PermissionDenied):
        moderation.decide_dispute(q.pk, 'no', expected_dispute=dispute.pk, telegram_id=999)


def test_confirmation_does_not_settle_and_navigation_separate(disputed):
    q, dispute, *_ = disputed
    with patch.object(bot, 'api'), patch.object(bot, 'render') as render:
        bot.handle_update(callback(f'ds:ask:no:{q.pk}:{dispute.pk}'))
    assert 'залоги возвращаются' in render.call_args.args[1]
    assert 'ds:do:no:' in str(render.call_args.args[2])
    assert not DisputeDecision.objects.exists()
    with patch.object(bot, 'renew_panel'), patch.object(bot, 'render') as render:
        bot.handle_update(message(text='/disputes'))
    assert 'СПОРЫ ОБ ИСХОДЕ' in render.call_args.args[1]
    assert 'ds:open:' in str(render.call_args.args[2])


def test_ui_card_and_final_history(disputed):
    q, dispute, *_ = disputed
    with patch.object(bot, 'render') as render:
        disputes_bot.show_card(123, q)
    assert dispute.reason in render.call_args.args[1]
    assert 'ds:proof:' in str(render.call_args.args[2])
    moderation.decide_dispute(q.pk, 'no', expected_dispute=dispute.pk, telegram_id=123)
    with patch.object(bot, 'render') as render:
        disputes_bot.show_queue(123, history=True)
    assert f'ds:open:{q.pk}' in str(render.call_args.args[2])


def test_delivery_retry_and_per_admin(disputed):
    with patch.object(bot, 'render', side_effect=bot.ModerationTelegramError):
        assert disputes_bot.flush_notifications() == 0
    assert DisputeDelivery.objects.count() == 2
    DisputeDelivery.objects.update(next_attempt=timezone.now())
    with patch.object(bot, 'render'):
        assert disputes_bot.flush_notifications() == 2
        assert disputes_bot.flush_notifications() == 0


def test_settlement_failure_rolls_back(disputed):
    q, dispute, *_ = disputed
    with patch('market.services._apply', side_effect=services.MarketError('failure')):
        with pytest.raises(services.MarketError):
            moderation.decide_dispute(q.pk, 'no', expected_dispute=dispute.pk, telegram_id=123)
    dispute.refresh_from_db()
    assert dispute.upheld is None
    assert not DisputeDecision.objects.exists()
    assert not LedgerEntry.objects.filter(kind=LedgerEntry.Kind.DISPUTE_REFUND).exists()


def test_proof_download_and_upload_use_different_bots(disputed):
    q, *_ = disputed
    response = Mock(ok=True)
    response.json.return_value = {'ok': True}
    with patch('market.telegram.download_file', return_value=(b'proof', 'photos/test.jpg')) as download, patch.object(disputes_bot.requests, 'post', return_value=response) as post:
        disputes_bot.send_proof(123, q)
    download.assert_called_once_with('proof-id')
    assert '/botMOD-TEST/sendDocument' in post.call_args.args[0]
    assert post.call_args.kwargs['files']['document'][1] == b'proof'


def test_dispute_cancellation_platform_covers_shortfall():
    buyer, other = [Trader.objects.create(name=x, pseudonym=x) for x in ('Seller', 'Holder')]
    q = Question.objects.create(title='Disputed underfunded market', b=144)
    purchase = services.buy(buyer, q, 'yes', 100)
    services.buy(other, q, 'yes', 200)
    sale = services.sell(buyer, q, 'yes', purchase.shares)
    profit = -sale.cost-purchase.cost
    assert profit > 0
    Question.objects.filter(pk=q.pk).update(resolution_state='pending', proposed_outcome='yes',
        dispute_until=timezone.now()+timedelta(hours=1))
    q.refresh_from_db()
    dispute = services.open_dispute(other, q, 'Cancel')
    moderation.decide_dispute(q.pk, 'cancel', expected_dispute=dispute.pk, telegram_id=123)
    q.refresh_from_db()
    other.refresh_from_db()
    assert q.refund_compensation == profit
    assert other.balance == 1000
    assert other.payouts.get().amount == 200
    assert other.ledger.filter(kind=LedgerEntry.Kind.DISPUTE_REFUND).count() == 1


def test_stored_photo_sent_in_bot_without_website(disputed, settings, tmp_path):
    from django.core.files.base import ContentFile
    settings.MEDIA_ROOT = tmp_path
    q, *_ = disputed
    q.evidence.save('proof.jpg', ContentFile(b'photo-bytes'), save=True)
    response = Mock(ok=True)
    response.json.return_value = {'ok': True}
    with patch('market.telegram.download_file') as download, patch.object(disputes_bot.requests, 'post', return_value=response) as post:
        disputes_bot.send_proof(123, q)
    download.assert_not_called()
    assert post.call_args.kwargs['files']['document'][1] == b'photo-bytes'


@pytest.mark.django_db(transaction=True)
def test_two_admins_settle_once(disputed):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import connection, close_old_connections
    if connection.vendor != 'postgresql':
        pytest.skip('Requires PostgreSQL row locks')
    q, dispute, *_ = disputed
    barrier = Barrier(2)
    def decide(admin):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
            barrier.wait(timeout=10)
            try:
                moderation.decide_dispute(q.pk, 'no', expected_dispute=dispute.pk, telegram_id=admin)
                return True
            except services.MarketError:
                return False
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(decide, [123, 456])) == 1
    assert DisputeDecision.objects.count() == 1
    assert LedgerEntry.objects.filter(kind=LedgerEntry.Kind.DISPUTE_REFUND).count() == 1
