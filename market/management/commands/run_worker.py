import time
import logging

from django.conf import settings
from prometheus_client import start_http_server
from market.worker_metrics import REGISTRY, observe_tick

from django.core.management.base import BaseCommand

from market.resolution import tick


class Command(BaseCommand):
    help = "Background worker: closes betting, asks creators for outcomes, reminders, dispute windows, results."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one pass and exit")
        parser.add_argument("--interval", type=int, default=60)

    def handle(self, *args, **options):
        if settings.WORKER_METRICS_PORT:
            start_http_server(settings.WORKER_METRICS_PORT, addr=settings.WORKER_METRICS_HOST, registry=REGISTRY)
        while True:
            try:
                done = observe_tick(tick)
            except Exception:
                logging.getLogger(__name__).exception("Worker tick failed")
                if options["once"]:
                    raise
                time.sleep(options["interval"])
                continue
            if any(done.values()):
                self.stdout.write(", ".join(f"{k}: {v}" for k, v in done.items() if v))
            if options["once"]:
                return
            time.sleep(options["interval"])
