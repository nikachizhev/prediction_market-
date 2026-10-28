#!/usr/bin/env bash
# One-time server bootstrap (Ubuntu 26.04, as root), after cloning the repo to /opt/prediction_market/app:
#   bash /opt/prediction_market/app/deploy/setup-server.sh <host>
# Then fill in the bot token in /opt/prediction_market/app/.env and run deploy/update.sh.
# Safe to re-run: keeps an existing .env and database.
set -euo pipefail

HOST="${1:?usage: setup-server.sh <host, e.g. 1-2-3-4.sslip.io>}"
ROOT=/opt/prediction_market
APP="$ROOT/app"

export DEBIAN_FRONTEND=noninteractive
apt-get install -yq docker.io docker-compose-v2 caddy python3-venv git nodejs npm >/dev/null

id pm >/dev/null 2>&1 || useradd --system --home-dir "$ROOT" --shell /usr/sbin/nologin pm
mkdir -p "$ROOT/media"
chown -R pm:pm "$ROOT"
chmod 755 "$ROOT"

if [ ! -f "$APP/.env" ]; then
	cat > "$APP/.env" <<ENV
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')
SITE_URL=https://$HOST
POSTGRES_DB=prediction_market
POSTGRES_USER=prediction_market
POSTGRES_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
MEDIA_ROOT=$ROOT/media
TELEGRAM_BOT_TOKEN=
TELEGRAM_BOT_USERNAME=
TELEGRAM_APP_NAME=app
MODERATION_BOT_TOKEN=
MODERATION_ADMIN_IDS=
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
ENV
	chown pm:pm "$APP/.env"
	chmod 600 "$APP/.env"
	echo "Created $APP/.env — fill in TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_USERNAME."
fi

sed "s/SITE_HOST/$HOST/" "$APP/deploy/Caddyfile" > /etc/caddy/Caddyfile
cp "$APP"/deploy/systemd/pm-*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable pm-db pm-web pm-bot pm-worker caddy >/dev/null
systemctl reload-or-restart caddy
echo "Server ready. Run: bash $APP/deploy/update.sh"
