import time
from django.core.management.base import BaseCommand, CommandError
from market.telegram import api, handle_update, TelegramError


class Command(BaseCommand):
    help = "Receive Telegram commands using long polling (run one instance)."

    def handle(self, *args, **options):
        try:
            if api("getWebhookInfo").get("url"):
                raise CommandError("У бота настроен webhook; отключите его перед polling.")
        except TelegramError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write("Telegram-бот запущен. Ctrl+C — остановка.")
        offset = None
        try:
            while True:
                try:
                    updates = api("getUpdates", offset=offset, timeout=25, allowed_updates=["message", "callback_query"])
                    for update in updates:
                        handle_update(update)
                        offset = update["update_id"] + 1
                except TelegramError as exc:
                    self.stderr.write(str(exc))
                    time.sleep(3)
        except KeyboardInterrupt:
            self.stdout.write("Бот остановлен.")
