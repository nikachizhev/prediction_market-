"""Read-only business snapshots. No user identifiers enter metric labels."""
from datetime import timedelta
import logging
from time import perf_counter

from django.db import DatabaseError
from django.db.models import Count, Sum, Q, Case, When, Value, CharField
from django.db.models.functions import Abs
from django.utils import timezone
from prometheus_client.core import GaugeMetricFamily

from .models import Dispute, Event, LedgerEntry, Question, Trade, Trader

log = logging.getLogger(__name__)
WINDOWS = {'all': None, '24h': timedelta(days=1), '7d': timedelta(days=7)}


def gauge(name, help_text, rows, labels=()):
    metric = GaugeMetricFamily(name, help_text, labels=labels)
    for keys, value in rows:
        metric.add_metric(keys, float(value or 0))
    return metric


def windowed(queryset, now, delta):
    queryset = queryset.filter(created_at__lte=now)
    return queryset.filter(created_at__gte=now-delta) if delta else queryset


class BusinessCollector:
    def describe(self):
        return []  # Registration must not query the database.

    def collect(self):
        start = perf_counter()
        try:
            metrics = self.snapshot(timezone.now())
        except DatabaseError:
            log.exception('Business metric collection failed')
            from .http_metrics import COLLECTION_FAILURES
            COLLECTION_FAILURES.inc()
            yield gauge('pm_database_up', 'Business database collection succeeded', [([], 0)])
        else:
            from .http_metrics import LAST_COLLECTION
            LAST_COLLECTION.set_to_current_time()
            yield from metrics
            yield gauge('pm_database_up', 'Business database collection succeeded', [([], 1)])
        yield gauge('pm_business_collection_duration_seconds', 'Latest business collection duration',
                    [([], perf_counter()-start)])

    def snapshot(self, now):
        cohort = Trader.objects.filter(pk__in=LedgerEntry.objects.filter(
            kind=LedgerEntry.Kind.STARTER, created_at__lte=now).values('trader_id'))
        markets = Question.objects.filter(kind='lmsr', creator__isnull=False,
                                          resolves_at__isnull=False, created_at__lte=now)
        trades = Trade.objects.filter(question__in=markets, trader__in=cohort)
        ledger = LedgerEntry.objects.filter(trader__in=cohort)
        users, counts, volumes, fees, created, operations = [], [], [], [], [], []
        for window, delta in WINDOWS.items():
            users.append((["registered", window], windowed(cohort, now, delta).count()))
            users.append((["active", window], windowed(Event.objects.filter(
                trader__in=cohort, name='app_open'), now, delta).values('trader_id').distinct().count()))
            current = windowed(trades, now, delta)
            users.append((["trading", window], current.values('trader_id').distinct().count()))
            for direction, condition in [('buy', Q(shares__gte=0)), ('sell', Q(shares__lt=0))]:
                stats = current.filter(condition).aggregate(n=Count('pk'), volume=Sum(Abs('cost')), fee=Sum('fee'))
                counts.append(([direction, window], stats['n']))
                volumes.append(([direction, window], stats['volume']))
                fees.append(([direction, window], stats['fee']))
            created.append(([window], windowed(markets, now, delta).count()))
            if window != '7d':
                sums = dict(windowed(ledger, now, delta).order_by().values('kind').annotate(
                    total=Sum(Abs('amount'))).values_list('kind', 'total'))
                operations.extend(([kind, window], sums.get(kind, 0)) for kind in LedgerEntry.Kind.values)
        states = dict(markets.annotate(state=Case(
            When(status='annulled', then=Value('annulled')),
            When(status='resolved', then=Value('resolved')),
            When(resolution_state='disputed', then=Value('disputed')),
            When(resolution_state='pending', then=Value('pending')),
            When(Q(status='closed') | Q(deadline__lte=now), then=Value('closed')),
            default=Value('open'), output_field=CharField())).order_by().values('state').annotate(
                n=Count('pk')).values_list('state', 'n'))
        disputes = Dispute.objects.filter(question__in=markets, created_at__lte=now)
        return [
            gauge('pm_users', 'Mini App accounts, new registrations or unique active/trading users in window', users, ['kind', 'window']),
            gauge('pm_trades', 'Committed trade count snapshot', counts, ['direction', 'window']),
            gauge('pm_trade_volume_coins', 'Cash turnover: absolute cost including fees', volumes, ['direction', 'window']),
            gauge('pm_trade_fees_coins', 'Trade fees charged, before refunds or settlement', fees, ['direction', 'window']),
            gauge('pm_markets_created', 'User-created LMSR markets', created, ['window']),
            gauge('pm_markets', 'User-created LMSR markets by lifecycle state',
                  [([state], states.get(state, 0)) for state in ['open', 'closed', 'pending', 'disputed', 'resolved', 'annulled']], ['state']),
            gauge('pm_balance_coins', 'Total Mini App account wallet balances', [([], cohort.aggregate(s=Sum('balance'))['s'])]),
            gauge('pm_ledger_coins', 'Absolute ledger flows, not revenue', operations, ['kind', 'window']),
            gauge('pm_disputes', 'Disputes by moderation result',
                  [([label], disputes.filter(upheld=upheld).count()) for label, upheld in
                   [('pending', None), ('upheld', True), ('rejected', False)]], ['state']),
        ]
