"""Process metrics; safe to aggregate across Gunicorn workers."""
from prometheus_client import Counter, Gauge

COLLECTION_FAILURES = Counter('pm_business_collection_failures_total', 'Failed database collections')
LAST_COLLECTION = Gauge('pm_business_last_success_timestamp_seconds', 'Last successful business collection', multiprocess_mode='max')

import os
from secrets import compare_digest
from time import perf_counter

from django.conf import settings
from django.http import HttpResponse
from prometheus_client import CollectorRegistry, Histogram, REGISTRY, generate_latest, CONTENT_TYPE_LATEST, multiprocess

REQUESTS = Counter('pm_http_requests_total', 'Completed HTTP responses', ['method', 'route', 'status'])
DURATION = Histogram('pm_http_request_duration_seconds', 'Time to produce HTTP response headers',
                     ['method', 'route'], buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10))
METHODS = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'TRACE', 'CONNECT'}


class MetricsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.METRICS_ENABLED or request.path_info == '/metrics':
            return self.get_response(request)
        start = perf_counter()
        response = self.get_response(request)
        match = getattr(request, 'resolver_match', None)
        route = match.view_name if match else 'unmatched'
        method = request.method if request.method in METHODS else 'OTHER'
        REQUESTS.labels(method, route, str(response.status_code)).inc()
        DURATION.labels(method, route).observe(perf_counter()-start)
        return response


def metrics_response(request):
    if not settings.METRICS_ENABLED:
        return HttpResponse(status=404)
    token = settings.METRICS_TOKEN
    if not token or not compare_digest(request.headers.get('Authorization', '').encode(), f'Bearer {token}'.encode()):
        return HttpResponse(status=403)
    from .observability import BusinessCollector
    # Collect DB once per scrape, separately from per-process counters.
    business = CollectorRegistry()
    business.register(BusinessCollector())
    payload = generate_latest(business)
    if os.environ.get('PROMETHEUS_MULTIPROC_DIR'):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        registry = REGISTRY
    response = HttpResponse(payload + generate_latest(registry), content_type=CONTENT_TYPE_LATEST)
    response['Cache-Control'] = 'no-store'
    return response
