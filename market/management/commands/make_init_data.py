"""Sign a test initData with the bot token from .env, for running the Mini App outside Telegram."""
import json
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from market.miniapp_auth import sign_init_data


class Command(BaseCommand):
    help = "Print a signed Telegram initData for local development (VITE_DEV_INIT_DATA)."

    def add_arguments(self, parser):
        parser.add_argument("--id", type=int, default=100000001, help="Telegram user id")
        parser.add_argument("--first-name", default="Тестовый")
        parser.add_argument("--username", default="")
        parser.add_argument("--start-param", default="")

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError("Set TELEGRAM_BOT_TOKEN in .env first.")
        user = {"id": options["id"], "first_name": options["first_name"], "language_code": "ru"}
        if options["username"]:
            user["username"] = options["username"]
        fields = {"auth_date": str(int(time.time())), "user": json.dumps(user, ensure_ascii=False, separators=(",", ":"))}
        if options["start_param"]:
            fields["start_param"] = options["start_param"]
        self.stdout.write(sign_init_data(fields, settings.TELEGRAM_BOT_TOKEN))
