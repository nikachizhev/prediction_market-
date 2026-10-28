from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

import pytest
from django.db import OperationalError
from django.utils import timezone
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.parser import text_string_to_metric_families

from market import services
from market.models import Event, LedgerEntry, Question, Trade, Trader


def samples():
    from market.observability import BusinessCollector
    registry = CollectorRegistry()
    registry.register(BusinessCollector())
    return {(s.name, tuple(sorted(s.labels.items()))): s.value
            for f in text_string_to_metric_families(generate_latest(registry).decode()) for s in f.samples}


def value(data, name, **labels):
    return data[(name, tuple(sorted(labels.items())))]


def account(name):
    t = Trader.objects.create(name=name, pseudonym=name, balance=0)
    services.grant_starter_pack(t)
    return t


def market(creator):
    return services.create_market(creator, title='Demo?', criteria='Evidence', category='tech',
                                 closes_at=timezone.now()+timedelta(days=2),
                                 resolves_at=timezone.now()+timedelta(days=3))


@pytest.mark.django_db
def test_empty_database_exports_zeroes():
    data = samples()
    assert value(data, 'pm_users', kind='registered', window='all') == 0
    assert value(data, 'pm_trade_volume_coins', direction='buy', window='24h') == 0
    assert value(data, 'pm_database_up') == 1


@pytest.mark.django_db
def test_cohorts_windows_and_cash_turnover():
    creator, user = account('creator'), account('user')
    legacy = Trader.objects.create(name='legacy', pseudonym='legacy')
    q = market(creator)
    for trader, cost, fee, age in [(user, 100, 2, 0), (user, -40, 1, 0), (user, 20, 1, 3),
                                  (legacy, 500, 10, 0), (user, 900, 18, -2)]:
        t = Trade.objects.create(trader=trader, question=q, side='yes', shares=1 if cost>0 else -1,
                                 cost=cost, fee=fee, prob_before=.5, prob_after=.6)
        Trade.objects.filter(pk=t.pk).update(created_at=timezone.now()-timedelta(days=age))
    Event.objects.create(trader=user, name='app_open')
    Event.objects.create(trader=user, name='app_open')
    Event.objects.create(trader=legacy, name='app_open')
    data = samples()
    assert value(data, 'pm_users', kind='registered', window='all') == 2
    assert value(data, 'pm_users', kind='active', window='24h') == 1
    assert value(data, 'pm_trade_volume_coins', direction='buy', window='24h') == 100
    assert value(data, 'pm_trade_volume_coins', direction='sell', window='24h') == 40
    assert value(data, 'pm_trade_volume_coins', direction='buy', window='7d') == 120
    assert value(data, 'pm_trade_fees_coins', direction='sell', window='all') == 1
    assert value(data, 'pm_ledger_coins', kind='starter', window='all') == 2000


@pytest.mark.django_db
def test_idempotent_purchase_counted_once():
    creator, user = account('creator'), account('user')
    q = market(creator)
    key = uuid4()
    services.buy(user, q, 'yes', Decimal(100), request_key=key)
    services.buy(user, q, 'yes', Decimal(100), request_key=key)
    assert value(samples(), 'pm_trades', direction='buy', window='all') == 1


@pytest.mark.django_db
def test_database_failure_does_not_emit_false_business_zeroes():
    with patch('django.db.backends.utils.CursorWrapper.execute', side_effect=OperationalError('offline')):
        data = samples()
    assert value(data, 'pm_database_up') == 0
    assert not any(name == 'pm_users' for name, labels in data)

@pytest.mark.django_db
def test_metrics_endpoint_requires_enable_and_token(client, settings):
    settings.METRICS_ENABLED = False
    assert client.get('/metrics').status_code == 404
    settings.METRICS_ENABLED = True
    settings.METRICS_TOKEN = 'test-secret'
    assert client.get('/metrics').status_code == 403
    response = client.get('/metrics', HTTP_AUTHORIZATION='Bearer test-secret')
    assert response.status_code == 200
    assert b'pm_users' in response.content
    settings.METRICS_TOKEN = ''
    assert client.get('/metrics').status_code == 403


@pytest.mark.django_db
def test_http_status_routes_and_scrape_exclusion(client, settings):
    from market.http_metrics import REQUESTS
    settings.METRICS_ENABLED = True
    settings.METRICS_TOKEN = 'test-secret'
    before = REQUESTS.labels('GET', 'unmatched', '404')._value.get()
    assert client.get('/missing-123').status_code == 404
    assert client.get('/missing-456').status_code == 404
    assert REQUESTS.labels('GET', 'unmatched', '404')._value.get() == before+2
    before = REQUESTS.labels('GET', 'api:me', '401')._value.get()
    assert client.get('/api/me').status_code == 401
    assert REQUESTS.labels('GET', 'api:me', '401')._value.get() == before+1
    response = client.get('/metrics', HTTP_AUTHORIZATION='Bearer test-secret')
    assert b'route="metrics"' not in response.content


def test_http_exception_response_and_bounded_method(settings):
    from django.http import HttpResponse
    from django.test import RequestFactory
    from market.http_metrics import MetricsMiddleware, REQUESTS
    settings.METRICS_ENABLED = True
    request = RequestFactory().generic('ARBITRARY_METHOD', '/bad')
    before = REQUESTS.labels('OTHER', 'unmatched', '500')._value.get()
    middleware = MetricsMiddleware(lambda request: HttpResponse(status=500))
    assert middleware(request).status_code == 500
    assert REQUESTS.labels('OTHER', 'unmatched', '500')._value.get() == before+1


def test_worker_tick_failure_and_recovery():
    from market.worker_metrics import observe_tick, LAST_SUCCESS, TICKS
    before = TICKS.labels('error')._value.get()
    stamp = LAST_SUCCESS._value.get()
    def fail():
        raise ValueError('failure')
    with pytest.raises(ValueError):
        observe_tick(fail)
    assert TICKS.labels('error')._value.get() == before+1
    assert LAST_SUCCESS._value.get() == stamp
    assert observe_tick(lambda: {'closed': 2}) == {'closed': 2}
    assert LAST_SUCCESS._value.get() > 0


def test_handled_telegram_failure_is_counted():
    from market.worker_metrics import TELEGRAM_FAILURES
    from market.resolution import _safe
    before = TELEGRAM_FAILURES.labels('other')._value.get()
    def fail(question):
        raise ValueError('transport unavailable')
    assert _safe(fail, Question(pk=1)) is False
    assert TELEGRAM_FAILURES.labels('other')._value.get() == before+1

@pytest.mark.django_db
def test_demo_seed_is_idempotent_and_never_contacts_telegram(settings):
    from django.core.management import call_command
    settings.OBSERVABILITY_DEMO = True
    with patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden')):
        call_command('seed_observability')
        first = Trade.objects.count()
        call_command('seed_observability')
        assert Trade.objects.count() == first > 0
        from market.resolution import tick
        tick()
    assert value(samples(), 'pm_users', kind='registered', window='all') >= 6


@pytest.mark.django_db
def test_demo_seed_refuses_non_demo_database():
    from django.core.management import call_command, CommandError
    with pytest.raises(CommandError, match='demo'):
        call_command('seed_observability')
    assert not Trader.objects.exists()

@pytest.mark.django_db
def test_cohort_duplicate_starter_and_market_states():
    creator, user = account('creator'), account('user')
    services.grant_starter_pack(user)
    q = market(creator)
    Question.objects.filter(pk=q.pk).update(deadline=timezone.now()-timedelta(seconds=1))
    assert value(samples(), 'pm_users', kind='registered', window='all') == 2
    assert value(samples(), 'pm_markets', state='closed') == 1
    services.propose_resolution(creator, q, 'yes', evidence_file_id='fake')
    assert value(samples(), 'pm_markets', state='pending') == 1


def test_multiprocess_counters_survive_worker_exit(tmp_path):
    import os
    import subprocess
    import sys
    env = dict(os.environ, PROMETHEUS_MULTIPROC_DIR=str(tmp_path))
    code = "from market.http_metrics import REQUESTS; REQUESTS.labels('GET','test','200').inc(3)"
    for _ in range(2):
        subprocess.run([sys.executable, '-c', code], env=env, check=True)
    code = """
from prometheus_client import CollectorRegistry, generate_latest, multiprocess
r = CollectorRegistry()
multiprocess.MultiProcessCollector(r)
print(generate_latest(r).decode())
"""
    output = subprocess.check_output([sys.executable, '-c', code], env=env, text=True)
    found = [s.value for f in text_string_to_metric_families(output) for s in f.samples
             if s.name == 'pm_http_requests_total' and s.labels == {'method':'GET','route':'test','status':'200'}]
    assert found == [6]

@pytest.mark.django_db
def test_zero_proceeds_sale_is_still_a_sale():
    creator, user = account('creator'), account('user')
    q = market(creator)
    services.buy(user, q, 'yes', Decimal(10))
    sale = services.sell(user, q, 'yes', Decimal('0.001'))
    assert sale.cost == 0 and sale.shares < 0
    data = samples()
    assert value(data, 'pm_trades', direction='buy', window='all') == 1
    assert value(data, 'pm_trades', direction='sell', window='all') == 1


@pytest.mark.django_db
def test_demo_balances_reconcile_with_ledger(settings):
    from django.core.management import call_command
    from django.db.models import Sum
    settings.OBSERVABILITY_DEMO = True
    call_command('seed_observability')
    assert Trader.objects.aggregate(s=Sum('balance'))['s'] == LedgerEntry.objects.aggregate(s=Sum('amount'))['s']


def test_malformed_auth_is_forbidden(client, settings):
    settings.METRICS_ENABLED = True
    settings.METRICS_TOKEN = 'test-secret'
    assert client.get('/metrics', HTTP_AUTHORIZATION='Bearer ä').status_code == 403


def exploding_view(request):
    raise RuntimeError('Controlled test exception')


from django.urls import path
urlpatterns = [path('explode', exploding_view, name='explode')]


def test_real_django_exception_counted_as_500(client, settings):
    from market.http_metrics import REQUESTS
    settings.METRICS_ENABLED = True
    settings.ROOT_URLCONF = __name__
    client.raise_request_exception = False
    before = REQUESTS.labels('GET', 'explode', '500')._value.get()
    assert client.get('/explode').status_code == 500
    assert REQUESTS.labels('GET', 'explode', '500')._value.get() == before+1
