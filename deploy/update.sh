#!/usr/bin/env bash
# Pull the latest code and restart everything (on the server, as root):
#   bash /opt/prediction_market/app/deploy/update.sh
# From your machine: ssh root@<server-ip> bash /opt/prediction_market/app/deploy/update.sh
set -euo pipefail

ROOT=/opt/prediction_market
APP="$ROOT/app"
as_pm() { sudo -u pm -H "$@"; }
cd "$APP"

as_pm git pull --ff-only
(cd miniapp && as_pm npm ci --silent --no-audit --no-fund && as_pm npm run build --silent)

[ -d "$ROOT/venv" ] || as_pm python3 -m venv "$ROOT/venv"
as_pm "$ROOT/venv/bin/pip" install -q --upgrade pip
as_pm "$ROOT/venv/bin/pip" install -q -r requirements.txt

systemctl start pm-db
as_pm "$ROOT/venv/bin/python" manage.py migrate --noinput
as_pm "$ROOT/venv/bin/python" manage.py collectstatic --noinput -v 0
systemctl restart pm-web pm-worker
if grep -q '^TELEGRAM_BOT_TOKEN=.' .env; then
	systemctl restart pm-bot
else
	echo "TELEGRAM_BOT_TOKEN is empty in .env: the bot is not started."
fi
# Install a new unit on existing servers too, not only during first-time setup.
cp deploy/systemd/pm-moderation-bot.service /etc/systemd/system/
systemctl daemon-reload
if grep -q '^MODERATION_BOT_TOKEN=.' .env && grep -q '^MODERATION_ADMIN_IDS=.' .env; then
	systemctl enable pm-moderation-bot >/dev/null
	systemctl restart pm-moderation-bot
else
	systemctl disable --now pm-moderation-bot >/dev/null 2>&1 || true
	echo "Moderation bot not configured: set MODERATION_BOT_TOKEN and MODERATION_ADMIN_IDS in .env."
fi
systemctl --no-pager --lines=0 status pm-db pm-web pm-worker pm-bot pm-moderation-bot | grep -E '^●|Active:' || true
