import time

from django.core.management.base import BaseCommand

from sources.sync import sync_all


class Command(BaseCommand):
    help = "Sync all question sources (once, or every N seconds with --loop N)."

    def add_arguments(self, parser):
        parser.add_argument("--loop", type=int, default=0, metavar="SECONDS")

    def handle(self, *args, **opts):
        while True:
            for m in sync_all():
                self.stdout.write(m)
            if not opts["loop"]:
                return
            try:
                time.sleep(opts["loop"])
            except KeyboardInterrupt:
                return
