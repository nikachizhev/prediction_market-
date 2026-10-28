import hashlib
import time
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from market import moderation, moderation_bot as bot
from market.models import ModerationBotState


class Command(BaseCommand):
    help = 'Run the separate, private complaints moderation bot (one instance per token).'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='One delivery/poll pass and exit')

    def handle(self, *args, **options):
        if not settings.MODERATION_BOT_TOKEN or settings.MODERATION_BOT_TOKEN == settings.TELEGRAM_BOT_TOKEN:
            raise CommandError('Задайте отдельный MODERATION_BOT_TOKEN, отличный от токена основного бота.')
        if not moderation.admin_ids():
            raise CommandError('Задайте MODERATION_ADMIN_IDS: положительные Telegram ID через запятую.')
        fingerprint = hashlib.sha256(settings.MODERATION_BOT_TOKEN.encode()).hexdigest()
        lock = int(fingerprint[:15], 16)
        acquired = False
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_try_advisory_lock(%s)', [lock])
                acquired = cursor.fetchone()[0]
            if not acquired:
                raise CommandError('Этот бот модерации уже запущен.')
        try:
            try:
                if bot.api('getWebhookInfo').get('url'):
                    raise CommandError('У бота модерации настроен webhook. Отключите его перед polling.')
            except bot.ModerationTelegramError as exc:
                raise CommandError(str(exc)) from None
            state, _ = ModerationBotState.objects.get_or_create(token_fingerprint=fingerprint)
            self.stdout.write('Бот модерации запущен. Администраторы должны открыть его и нажать /start.')
            while True:
                try:
                    bot.flush_notifications()
                    updates = bot.api('getUpdates', offset=state.offset, timeout=0 if options['once'] else 15,
                                      http_timeout=25, allowed_updates=['message', 'callback_query'])
                    for update in updates:
                        if update['update_id'] < state.offset:
                            continue
                        bot.handle_update(update)
                        state.offset = update['update_id']+1
                        state.save(update_fields=['offset'])
                except bot.ModerationTelegramError as exc:
                    self.stderr.write(str(exc))
                    if not options['once']:
                        time.sleep(3)
                if options['once']:
                    break
        except KeyboardInterrupt:
            self.stdout.write('Бот модерации остановлен.')
        finally:
            if acquired:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_advisory_unlock(%s)', [lock])
