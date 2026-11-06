#!/bin/sh
set -eu
python manage.py migrate --noinput
python manage.py seed_observability
# A fresh directory per master, inherited by all workers. No stale counter files.
export PROMETHEUS_MULTIPROC_DIR="$(mktemp -d /tmp/pm-prometheus.XXXXXX)"
exec gunicorn config.wsgi --bind 0.0.0.0:8000 --workers 2 --config deploy/observability/gunicorn.conf.py --access-logfile -
