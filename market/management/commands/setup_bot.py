from django.core.management.base import BaseCommand, CommandError

from market.bot_resolution import app_url
from market import telegram
from market.telegram import TelegramError


class Command(BaseCommand):
    help = "Set the bot's menu button «Открыть приложение» and its command list (run once after deploy)."

    def handle(self, *args, **options):
        try:
            telegram.api("setChatMenuButton", menu_button={
                "type": "web_app", "text": "Открыть приложение", "web_app": {"url": app_url()},
            })
            # Re-setting the same list is harmless, so the command can be re-run after every deploy.
            telegram.api("setMyCommands", commands=[{"command": "start", "description": "Открыть приложение"},
                                                    {"command": "my", "description": "Мои рынки"}])
        except TelegramError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(f"Кнопка меню ведёт на {app_url()}")
        self.stdout.write("Для «Поделиться» включите инлайн-режим в @BotFather: /setinline.")
