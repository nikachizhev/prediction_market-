from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
import pytest
from django.utils import timezone
from market import services, moderation, moderation_bot as bot
from market.models import Dispute, LedgerEntry, ModerationAction, Payout, Question, Trader
from market.tests.test_moderation_bot import complain, callback

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def config(settings):
    settings.MODERATION_ADMIN_IDS = ('123',)
    settings.MODERATION_BOT_TOKEN = 'MOD-REFUNDS'
    settings.SITE_URL = 'https://example.test'


@pytest.fixture
def people():
    return [Trader.objects.create(name=name, pseudonym=name) for name in ('Creator', 'Buyer', 'Other')]


@pytest.fixture
def market(people):
    now = timezone.now()
    return services.create_market(people[0], title='Cancel test', criteria='Test', category='tech',
        closes_at=now+timedelta(hours=2), resolves_at=now+timedelta(hours=3))


def test_cancel_returns_full_investment_and_fees_creator_loses_subsidy(people, market):
    creator, buyer, other = people
    services.buy(buyer, market, 'yes', 100)
    services.buy(other, market, 'no', 50)
    report = complain(market)
    moderation.review(report.pk, 'cancel', telegram_id=123)
    for trader in (buyer, other):
        trader.refresh_from_db()
        assert trader.balance == 1000
    creator.refresh_from_db()
    assert creator.balance == 900
    assert not creator.ledger.filter(kind__in=['creator_fee', 'subsidy_return']).exists()
    market.refresh_from_db()
    assert market.status == 'annulled' and market.resolution_state == 'final'
    assert market.outcome is None and market.moderation_hidden and market.creator_at_fault
    assert list(Payout.objects.order_by('amount').values_list('amount', flat=True)) == [Decimal(50), Decimal(100)]
    assert market.moderation_actions.get().action == 'cancel'


def test_partial_sale_is_not_paid_twice(people, market):
    _, buyer, _ = people
    purchase = services.buy(buyer, market, 'yes', 100)
    sale = services.sell(buyer, market, 'yes', purchase.shares/2)
    already_received = -sale.cost
    report = complain(market)
    moderation.review(report.pk, 'cancel', telegram_id=123)
    assert buyer.payouts.get().amount == 100-already_received
    buyer.refresh_from_db()
    assert buyer.balance == 1000


def test_platform_covers_shortfall_without_reclaiming_realized_profit(people):
    _, buyer, other = people
    # An underfunded legacy market: real trade history but no creator subsidy.
    q = Question.objects.create(title='No reserve', b=144)
    purchase = services.buy(buyer, q, 'yes', 100)
    services.buy(other, q, 'yes', 200)
    sale = services.sell(buyer, q, 'yes', purchase.shares)
    profit = -sale.cost-purchase.cost
    assert profit > 0
    buyer.refresh_from_db()
    before = buyer.balance
    report = complain(q)
    moderation.review(report.pk, 'cancel', telegram_id=123)
    buyer.refresh_from_db()
    other.refresh_from_db()
    q.refresh_from_db()
    assert buyer.balance == before and buyer.balance > 1000
    assert not buyer.payouts.exists()
    assert other.balance == 1000 and other.payouts.get().amount == 200
    assert q.refund_compensation == profit


def test_cancellation_refunds_unresolved_dispute_stakes(people, market):
    creator, buyer, _ = people
    services.buy(buyer, market, 'yes', 100)
    market.deadline = timezone.now()-timedelta(seconds=1)
    market.save(update_fields=['deadline'])
    services.propose_resolution(creator, market, 'yes', evidence_file_id='test-evidence')
    services.open_dispute(buyer, market, 'Wrong')
    report = complain(market)
    moderation.review(report.pk, 'cancel', telegram_id=123)
    buyer.refresh_from_db()
    assert buyer.balance == 1000
    assert buyer.ledger.filter(kind=LedgerEntry.Kind.DISPUTE_REFUND).get().amount == 20
    assert Dispute.objects.get().upheld is True


def test_repeat_cancel_and_stale_restore_cannot_pay_or_reopen_twice(people, market):
    services.buy(people[1], market, 'yes', 100)
    report = complain(market)
    moderation.review(report.pk, 'cancel', telegram_id=123)
    balance = Trader.objects.get(pk=people[1].pk).balance
    for action in (lambda: moderation.review(report.pk, 'cancel', telegram_id=123),
                   lambda: moderation.restore(market.pk, telegram_id=123)):
        with pytest.raises(services.MarketError):
            action()
    assert Payout.objects.count() == 1 and ModerationAction.objects.count() == 1
    assert Trader.objects.get(pk=people[1].pk).balance == balance
    _, buttons = bot.card(Question.objects.get(pk=market.pk))
    assert not any('restore' in b.get('callback_data', '') or ':cancel:' in b.get('callback_data', '') for row in buttons for b in row)


def test_hidden_market_can_later_be_cancelled(people, market):
    services.buy(people[1], market, 'yes', 100)
    report = complain(market)
    moderation.review(report.pk, 'hide', telegram_id=123)
    market.refresh_from_db()
    _, buttons = bot.card(market)
    assert any(b.get('callback_data') == f'mb:ask:cancel:{report.pk}' for row in buttons for b in row)
    moderation.review(report.pk, 'cancel', telegram_id=123)
    assert market.moderation_actions.count() == 2
    assert Trader.objects.get(pk=people[1].pk).balance == 1000


def test_resolved_market_is_not_reversed_or_paid_again(people, market):
    services.buy(people[1], market, 'yes', 100)
    services.resolve(market, 'yes')
    report = complain(market)
    balances = list(Trader.objects.order_by('pk').values_list('balance', flat=True))
    with pytest.raises(services.MarketError, match='завершён'):
        moderation.review(report.pk, 'cancel', telegram_id=123)
    assert balances == list(Trader.objects.order_by('pk').values_list('balance', flat=True))
    report.refresh_from_db()
    assert report.status == 'pending' and not ModerationAction.objects.exists()


def test_failure_rolls_back_balances_and_complaint_decision(people, market):
    services.buy(people[1], market, 'yes', 100)
    report = complain(market)
    with patch('market.notifications.market_finished', side_effect=RuntimeError('test rollback')):
        with pytest.raises(RuntimeError):
            moderation.review(report.pk, 'cancel', telegram_id=123)
    market.refresh_from_db()
    report.refresh_from_db()
    assert market.status == 'open' and not market.hidden_at and report.status == 'pending'
    assert Trader.objects.get(pk=people[1].pk).balance == 900
    assert not Payout.objects.exists() and not ModerationAction.objects.exists()


def test_bot_has_separate_hide_and_cancel_with_explicit_financial_confirmation(people, market):
    services.buy(people[1], market, 'yes', 100)
    report = complain(market)
    _, buttons = bot.card(market)
    actions = {b.get('callback_data') for row in buttons for b in row}
    assert f'mb:ask:hide:{report.pk}' in actions and f'mb:ask:cancel:{report.pk}' in actions
    with patch.object(bot, 'api', return_value={}) as api:
        bot.handle_update(callback(f'mb:ask:cancel:{report.pk}'))
        text = api.call_args.kwargs['text']
        assert 'Автор теряет' in text and 'за вычетом' in text
        assert 'Балансы и исход рынка не изменятся' not in text
        assert not Payout.objects.exists()
        bot.handle_update(callback(f'mb:do:cancel:{report.pk}'))
    market.refresh_from_db()
    assert market.status == 'annulled' and Payout.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_simultaneous_cancellations_refund_only_once(settings):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import connection, close_old_connections
    if connection.vendor != 'postgresql':
        pytest.skip('Requires PostgreSQL row locks')
    q = Question.objects.create(title='Concurrent cancellation')
    player = Trader.objects.create(name='Concurrent', pseudonym='Concurrent')
    services.buy(player, q, 'yes', 100)
    first, second = complain(q), complain(q)
    barrier = Barrier(2)
    def cancel(report_id):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
            barrier.wait(timeout=10)
            try:
                moderation.review(report_id, 'cancel', telegram_id=123)
                return True
            except services.MarketError:
                return False
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(cancel, [first.pk, second.pk]))
    assert sum(outcomes) == 1 and Payout.objects.count() == 1 and ModerationAction.objects.count() == 1
    player.refresh_from_db()
    assert player.balance == 1000
