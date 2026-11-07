import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from market import ai_resolution
from market.premoderation import process_queue


class Command(BaseCommand):
    help = "Check queued market content before publication (separate lightweight worker)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        try:
            while True:
                close_old_connections()
                self.stdout.write(f"Checked: {process_queue()}, AI outcomes: {ai_resolution.process_queue()}")
                if options["once"]:
                    return
                time.sleep(10)
        except KeyboardInterrupt:
            pass
