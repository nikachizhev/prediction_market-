"""Independent registry served by the worker, never the web process."""
from time import perf_counter
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()
LAST_SUCCESS = Gauge('pm_worker_last_success_timestamp_seconds', 'Last completed tick', registry=REGISTRY)
TICKS = Counter('pm_worker_ticks_total', 'Worker ticks', ['result'], registry=REGISTRY)
DURATION = Histogram('pm_worker_tick_duration_seconds', 'Worker tick duration', registry=REGISTRY)
TELEGRAM_FAILURES = Counter('pm_worker_telegram_failures_total', 'Handled Telegram action failures', ['action'], registry=REGISTRY)
ACTIONS = {'ask_creator', 'remind_creator', 'send_results'}
for result in ('success', 'error'):
    TICKS.labels(result)
for action in sorted(ACTIONS | {'other'}):
    TELEGRAM_FAILURES.labels(action)


def record_telegram_failure(action):
    TELEGRAM_FAILURES.labels(action if action in ACTIONS else 'other').inc()


def observe_tick(tick):
    start = perf_counter()
    try:
        result = tick()
    except Exception:
        TICKS.labels('error').inc()
        raise
    else:
        TICKS.labels('success').inc()
        LAST_SUCCESS.set_to_current_time()
        return result
    finally:
        DURATION.observe(perf_counter()-start)
