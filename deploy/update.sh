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
systemctl --no-pager --lines=0 status pm-db pm-web pm-worker pm-bot | grep -E '^●|Active:' || true
