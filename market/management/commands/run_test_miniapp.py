"""Local test launcher. Never use this for the production bot/server."""
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from dotenv import set_key


class Command(BaseCommand):
    help = "Run Django with a reconnecting HTTPS tunnel and update the test bot menu."

    def add_arguments(self, parser):
        parser.add_argument("--bot", required=True, help="Expected test bot username, without @")

    def handle(self, *args, **options):
        from market.telegram import api, TelegramError
        if not shutil.which("ssh"):
            raise CommandError("OpenSSH (ssh) is required")
        try:
            actual = api("getMe")["username"]
        except TelegramError as exc:
            raise CommandError(str(exc)) from exc
        if actual.lower() != options["bot"].lstrip("@").lower():
            raise CommandError("Configured bot does not match --bot; nothing changed")
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", 8000)) == 0:
                raise CommandError("Port 8000 is busy. Stop the existing runserver with Ctrl+C first.")
        self.stdout.write(f"Test bot: @{actual}. Keep this terminal open. Ctrl+C stops everything.")
        tunnel = server = None
        try:
            while True:
                self.stdout.write("Connecting HTTPS tunnel...")
                tunnel = subprocess.Popen([
                    "ssh", "-T", "-o", "StrictHostKeyChecking=accept-new",
                    "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                    "-o", "ExitOnForwardFailure=yes", "-R", "80:127.0.0.1:8000",
                    "nokey@localhost.run",
                ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding="utf-8", errors="replace", bufsize=1)
                url = None
                for line in tunnel.stdout:
                    match = re.search(r"https://[a-z0-9-]+\.lhr\.life\b", line)
                    if match and url is None:
                        url = match.group(0)
                        set_key(str(settings.BASE_DIR / ".env"), "SITE_URL", url, quote_mode="never")
                        settings.SITE_URL = url
                        try:
                            call_command("setup_bot")
                        except TelegramError as exc:
                            raise CommandError(str(exc)) from exc
                        env = {**os.environ, "SITE_URL": url}
                        server = subprocess.Popen([
                            sys.executable, "manage.py", "runserver", "127.0.0.1:8000", "--noreload",
                        ], cwd=settings.BASE_DIR, env=env)
                        self.stdout.write(self.style.SUCCESS(f"Open @{actual} via its menu: {url}/app/"))
                        self.stdout.write("After reconnecting, close the old Mini App and reopen via the bot menu.")
                self.stop(server)
                server = None
                tunnel.wait()
                self.stdout.write("Tunnel disconnected. Reconnecting in 3 seconds...")
                time.sleep(3)
        except KeyboardInterrupt:
            self.stdout.write("Test server and tunnel stopped.")
        finally:
            self.stop(server)
            self.stop(tunnel)

    @staticmethod
    def stop(process):
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
