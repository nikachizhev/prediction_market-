import time

from django.core.management.base import BaseCommand

from market.resolution import tick


class Command(BaseCommand):
    help = "Background worker: closes betting, asks creators for outcomes, reminders, dispute windows, results."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one pass and exit")
        parser.add_argument("--interval", type=int, default=60)

    def handle(self, *args, **options):
        while True:
            done = tick()
            if any(done.values()):
                self.stdout.write(", ".join(f"{k}: {v}" for k, v in done.items() if v))
            if options["once"]:
                return
            time.sleep(options["interval"])
