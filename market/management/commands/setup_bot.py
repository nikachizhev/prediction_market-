from django.core.management.base import BaseCommand, CommandError

from market.bot_resolution import app_url
from market.telegram import TelegramError, api


class Command(BaseCommand):
    help = "Set the bot's menu button «Открыть приложение» and its command list (run once after deploy)."

    def handle(self, *args, **options):
        try:
            api("setChatMenuButton", menu_button={
                "type": "web_app", "text": "Открыть приложение", "web_app": {"url": app_url()},
            })
            api("setMyCommands", commands=[{"command": "start", "description": "Открыть приложение"}])
        except TelegramError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(f"Кнопка меню ведёт на {app_url()}")
