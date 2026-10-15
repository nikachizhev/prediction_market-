from decimal import Decimal, ROUND_FLOOR
from importlib import import_module
from uuid import uuid4
from unittest.mock import patch
import pytest
from django.apps import apps
from django.contrib.auth.models import User
from django.db import connection
from django.urls import reverse
from market import services
from market.models import Question, Trader, Trade, Position, Payout
from market.purchase_tokens import issue

pytestmark = pytest.mark.django_db


@pytest.fixture
def trader():
    return Trader.objects.create(user=User.objects.create_user('safe-buyer'), name='Buyer', pseudonym='Buyer')


@pytest.fixture(params=['lmsr', 'ls_lmsr', 'cpmm'])
def market(request):
    return Question.objects.create(title='Safety', kind=request.param)


def test_same_request_retries_once_even_after_market_finishes(trader, market):
    key = uuid4()
    first = services.buy(trader, market, 'yes', 10, request_key=key)
    services.resolve(market, 'yes')
    trader.refresh_from_db()
    balance = trader.balance
    assert services.buy(trader, market, 'yes', 10, request_key=key).pk == first.pk
    trader.refresh_from_db()
    assert trader.balance == balance and market.trades.count() == 1


def test_new_identifiers_allow_identical_top_up(trader, market):
    first = services.buy(trader, market, 'yes', 10, request_key=uuid4())
    second = services.buy(trader, market, 'yes', 10, request_key=uuid4())
    assert first.pk != second.pk
    assert market.trades.count() == 2
    trader.refresh_from_db()
    assert trader.balance == Decimal('980')


def test_reused_key_cannot_change_parameters_or_owner(trader, market):
    key = uuid4()
    services.buy(trader, market, 'yes', 10, request_key=key)
    other = Trader.objects.create(name='Other', pseudonym='Other')
    another = Question.objects.create(title='Other market')
    for person, question, side, amount in [(trader, market, 'no', 10), (trader, market, 'yes', 20),
                                           (other, market, 'yes', 10), (trader, another, 'yes', 10)]:
        with pytest.raises(services.MarketError, match='идентификатор'):
            services.buy(person, question, side, amount, request_key=key)
    assert Trade.objects.count() == 1


def test_failed_purchase_does_not_consume_key(trader, market):
    key = uuid4()
    with patch('market.subscriptions.price_changed', side_effect=RuntimeError('rollback')):
        with pytest.raises(RuntimeError):
            services.buy(trader, market, 'yes', 10, request_key=key)
    trader.refresh_from_db()
    assert trader.balance == 1000 and not Trade.objects.exists() and not Position.objects.exists()
    services.buy(trader, market, 'yes', 10, request_key=key)
    assert Trade.objects.count() == 1


@pytest.mark.parametrize('sold_side', ['yes', 'no'])
def test_selling_one_side_keeps_other_refund(trader, market, sold_side):
    kept_side = 'no' if sold_side == 'yes' else 'yes'
    sold = services.buy(trader, market, sold_side, 100)
    kept = services.buy(trader, market, kept_side, 50)
    services.sell(trader, market, sold_side, sold.shares)
    pos = trader.positions.get(question=market)
    assert getattr(pos, sold_side+'_spent') == 0
    assert getattr(pos, kept_side+'_spent') == pos.spent == kept.cost == 50
    trader.refresh_from_db()
    balance = trader.balance
    services.annul(market)
    trader.refresh_from_db()
    assert trader.balance == balance+50
    assert Payout.objects.get(question=market).amount == 50
    with pytest.raises(services.MarketError):
        services.annul(market)


def test_partial_sales_rebuys_and_rounding_preserve_basis(trader, market):
    yes = services.buy(trader, market, 'yes', Decimal('33.33'))
    services.buy(trader, market, 'no', Decimal('17.17'))
    sold_shares = (yes.shares/3).quantize(Decimal('.000001'), rounding=ROUND_FLOOR)
    services.sell(trader, market, 'yes', sold_shares)
    pos = trader.positions.get(question=market)
    expected = yes.cost-(yes.cost*sold_shares/yes.shares).quantize(Decimal('.01'), rounding=ROUND_FLOOR)
    assert pos.yes_spent == expected and pos.no_spent == Decimal('17.17')
    services.buy(trader, market, 'yes', 10)
    pos.refresh_from_db()
    assert pos.yes_spent == expected+10 and pos.spent == pos.yes_spent+pos.no_spent
    services.sell(trader, market, 'yes', pos.yes_shares)
    pos.refresh_from_db()
    services.sell(trader, market, 'no', pos.no_shares)
    pos.refresh_from_db()
    assert pos.spent == pos.yes_spent == pos.no_spent == 0
    services.annul(market)
    assert not Payout.objects.filter(question=market).exists()


def test_data_migration_rebuilds_legacy_broken_basis_without_changing_balance(trader, market):
    yes = services.buy(trader, market, 'yes', 100)
    services.buy(trader, market, 'no', 50)
    services.sell(trader, market, 'yes', yes.shares)
    Position.objects.filter(trader=trader, question=market).update(spent=0, yes_spent=0, no_spent=0)
    trader.refresh_from_db()
    balance = trader.balance
    migration = import_module('market.migrations.0014_purchase_safety')
    from types import SimpleNamespace
    migration.restore_side_costs(apps, SimpleNamespace(connection=connection))
    pos = trader.positions.get(question=market)
    trader.refresh_from_db()
    assert pos.no_spent == pos.spent == 50 and pos.yes_spent == 0
    assert trader.balance == balance


def test_web_retries_and_separate_purchases(client, trader, market):
    client.force_login(trader.user)
    url = reverse('market:buy', args=[market.pk])
    page_url = reverse('market:question', args=[market.pk])
    token = client.get(page_url).context['purchase_token']
    payload = {'side': 'yes', 'amount': '10', 'purchase_token': token}
    assert client.post(url, payload).status_code == 302
    assert client.post(url, payload).status_code == 302
    assert market.trades.count() == 1
    payload['purchase_token'] = client.get(page_url).context['purchase_token']
    client.post(url, payload)
    assert market.trades.count() == 2


def test_web_missing_forged_and_foreign_tokens_cannot_buy(client, trader, market):
    client.force_login(trader.user)
    another = Question.objects.create(title='Other')
    other = Trader.objects.create(name='Other', pseudonym='Other')
    for token in ['', 'forged', issue(other, market), issue(trader, another)]:
        client.post(reverse('market:buy', args=[market.pk]), {'side': 'yes', 'amount': '10', 'purchase_token': token})
    assert not Trade.objects.exists()
