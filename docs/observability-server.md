# Prometheus и Grafana на сервере

Для существующего Linux VPS из `deploy/setup-server.sh`: приложение в `/opt/prediction_market/app`, пользователь `pm`, сервисы `pm-web` и `pm-worker`, Docker Compose v2. Здесь мониторятся реальные данные; демобаза и `seed_observability` не используются. На сервере эти шаги ещё не выполнялись; проверены конфигурации и локальная реализация метрик.

Compose: `deploy/observability/server/compose.yaml`. Только Prometheus и Grafana, приложение/БД остаются под текущим управлением. Контейнеры используют [host networking Linux](https://docs.docker.com/engine/network/drivers/host/), поэтому видят loopback systemd-сервисов. Все monitoring endpoints слушают `127.0.0.1`.

## 1. Обновить приложение

Команды на сервере под root. Сначала сохраните изменения, если checkout сервера грязный; не используйте reset/clean.

```bash
cd /opt/prediction_market/app
sudo -u pm git status --short
sudo -u pm git fetch origin
sudo -u pm git switch miniapp2-observability
sudo -u pm git pull --ff-only
bash deploy/update.sh
```

Если локальной ветки ещё нет, `git switch` создаст её по одноимённой ветке origin. Проверить: `sudo -u pm git branch --show-current`. Не устанавливайте `DJANGO_SETTINGS_MODULE=config.observability_settings`: это только деморежим без настоящего Telegram.

## 2. Создать секреты

Скрипт сохраняет существующие секреты при повторном запуске. `server.env` содержит пароль Grafana, `metrics_token` — токен Prometheus. Они вне репозитория. Токен записывается также в существующий `.env` приложения; остальные параметры сохраняются.

```bash
python3 - <<'PY'
import os
from pathlib import Path
import secrets

root = Path('/etc/prediction-market-monitoring')
root.mkdir(mode=0o700, parents=True, exist_ok=True)
root.chmod(0o700)
token_file = root / 'metrics_token'
if not token_file.exists():
    token_file.write_text(secrets.token_hex(32) + '\n')
# Container's unprivileged Prometheus user must read the bind-mounted file.
# On the host the enclosing directory remains root-only (0700).
token_file.chmod(0o444)
env_file = root / 'server.env'
if not env_file.exists():
    env_file.write_text('GRAFANA_ADMIN_PASSWORD=' + secrets.token_hex(32) + '\n')
env_file.chmod(0o600)
app_env = Path('/opt/prediction_market/app/.env')
lines = app_env.read_text().splitlines()
lines = [line for line in lines if not line.startswith('METRICS_TOKEN=')]
lines.append('METRICS_TOKEN=' + token_file.read_text().strip())
app_env.write_text('\n'.join(lines) + '\n')
app_env.chmod(0o600)
PY
chown pm:pm /opt/prediction_market/app/.env
```

Grafana применяет начальный admin-пароль только при создании своей БД. После смены пароля через Grafana значение `server.env` не сбрасывает его автоматически.

## 3. Включить экспорт systemd

```bash
cd /opt/prediction_market/app
install -d /etc/systemd/system/pm-web.service.d /etc/systemd/system/pm-worker.service.d
install -m 644 deploy/observability/server/pm-web.conf /etc/systemd/system/pm-web.service.d/monitoring.conf
install -m 644 deploy/observability/server/pm-worker.conf /etc/systemd/system/pm-worker.service.d/monitoring.conf
systemctl daemon-reload
systemctl restart pm-web pm-worker
systemctl --no-pager status pm-web pm-worker
```

Systemd создаёт `/run/pm-web-metrics`, доступную пользователю сервиса, и удаляет при остановке. Все три Gunicorn workers используют общие счётчики; при новом запуске master старые файлы не остаются. Worker экспортирует метрики на loopback:9101. `deploy/update.sh` сохраняет эти drop-in настройки.

В `/etc/caddy/Caddyfile` внутри блока домена, **перед общим `handle`**, добавьте:

```caddyfile
@monitoring path /metrics /metrics/*
handle @monitoring {
    respond 404
}
```

```bash
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
```

Prometheus ходит напрямую на `127.0.0.1:8000` с bearer-токеном, а публичный URL `/metrics` закрыт. Повторный `setup-server.sh` перезаписывает Caddyfile — после него восстановите этот блок.

## 4. Запустить мониторинг

```bash
cd /opt/prediction_market/app
docker compose --env-file /etc/prediction-market-monitoring/server.env -f deploy/observability/server/compose.yaml config --quiet
docker compose --env-file /etc/prediction-market-monitoring/server.env -f deploy/observability/server/compose.yaml up -d
docker compose --env-file /etc/prediction-market-monitoring/server.env -f deploy/observability/server/compose.yaml ps
```

Оба дашборда и datasource [подключаются provisioning Grafana](https://grafana.com/docs/grafana/latest/administration/provisioning/). Prometheus [читает bearer-токен из файла](https://prometheus.io/docs/prometheus/latest/configuration/configuration/). Retention — 15 дней, данные в отдельных Docker volumes. Порты 13000/19090/9101 не открывайте в firewall; наружный домен для мониторинга не нужен.

## 5. Проверить и открыть

На сервере:

```bash
curl --fail http://127.0.0.1:13000/api/health
curl --fail http://127.0.0.1:19090/-/ready
curl --fail http://127.0.0.1:19090/api/v1/targets
curl --fail 'http://127.0.0.1:19090/api/v1/query?query=pm_database_up'
curl --fail http://127.0.0.1:9101/metrics
```

Через 20 секунд все три targets (`app`, `worker`, `prometheus`) должны быть `up`, `pm_database_up=1`. Если app down/403 — проверьте совпадение токена и перезапуск pm-web; worker down — порт и логи `journalctl -u pm-worker -n 50`. Метрики не заменяют резервное копирование БД.

На своём компьютере (локальные 13000/19090 могут быть заняты демостеком, поэтому здесь другие порты):

```bash
ssh -N -L 23000:127.0.0.1:13000 -L 29090:127.0.0.1:19090 root@<server-ip>
```

- Бизнес: http://localhost:23000/d/prediction-market
- Техника: http://localhost:23000/d/prediction-market-technical
- Prometheus: http://localhost:29090

Вход Grafana: `admin`; пароль из `/etc/prediction-market-monitoring/server.env` на сервере. Не используйте локальный демопароль. Без HTTP-трафика квантили задержек пустые — создайте несколько обычных запросов к приложению. В бизнес-панелях будут реальные данные, не обязательно 8 пользователей/42 сделки; локальный `verify.py` с проверкой демочисел здесь не подходит.

## Обновление и остановка

После обновления кода повторите `up -d` из шага 4. Изменения JSON-дашбордов подхватываются provisioning; после изменения datasource/Prometheus-конфига перезапустите соответствующий контейнер:

```bash
docker compose --env-file /etc/prediction-market-monitoring/server.env -f deploy/observability/server/compose.yaml restart prometheus grafana
# Остановить только мониторинг, сохранить историю:
docker compose --env-file /etc/prediction-market-monitoring/server.env -f deploy/observability/server/compose.yaml stop
```

Чтобы полностью выключить сбор, удалите только созданные `monitoring.conf` drop-in файлы, выполните `systemctl daemon-reload` и перезапустите `pm-web pm-worker`. Основные unit-файлы не меняются. Не удаляйте Docker volumes, если нужна история.
