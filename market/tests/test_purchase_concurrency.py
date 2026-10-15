from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import pytest
from django.db import connection, close_old_connections
from market import services
from market.models import Trader, Question, Trade


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('same_request', [True, False])
def test_simultaneous_purchase_requests(same_request):
    if connection.vendor != 'postgresql':
        pytest.skip('Requires PostgreSQL row locks')
    trader = Trader.objects.create(name='Concurrent', pseudonym='Concurrent')
    market = Question.objects.create(title='Concurrent')
    keys = [uuid4(), uuid4()]
    if same_request:
        keys[1] = keys[0]
    barrier = Barrier(2)

    def purchase(key):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
            barrier.wait(timeout=10)
            return services.buy(trader, market, 'yes', 10, request_key=key).pk
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(purchase, keys))
    count = 1 if same_request else 2
    assert len(set(results)) == Trade.objects.count() == count
    trader.refresh_from_db()
    assert trader.balance == 1000-10*count
