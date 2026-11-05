"""Separate process for immediate Telegram outbox jobs; never polls updates."""
import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from market.bot_jobs import process_one


class Command(BaseCommand):
    help = 'Send queued outcome messages and render requested charts.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Drain ready jobs and exit')

    def handle(self, *args, **options):
        try:
            while True:
                close_old_connections()
                try:
                    processed = process_one()
                except Exception as exc:
                    logging.getLogger(__name__).error('Bot queue failed: %s', type(exc).__name__)
                    if options['once']:
                        raise
                    time.sleep(1)
                    continue
                if not processed:
                    if options['once']:
                        return
                    time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            close_old_connections()
