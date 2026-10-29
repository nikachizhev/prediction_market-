---
name: deploy
description: Выкатить ветку main на прод-сервер 91.108.241.25 — запушить, запустить deploy/update.sh, при необходимости setup_bot, проверить сервисы, логи и сайт. Использовать, когда пользователь просит задеплоить, обновить сервер или «чтобы бот работал по новой версии».
---

# Деплой на прод

Сервер, пути и сервисы описаны в CLAUDE.md, раздел «Сервер (прод)». Вызов `/deploy` — это и есть явная просьба пользователя запушить `main` и обновить прод. Команда тоже вливает свои ветки в `main` через PR.

## 1. Проверки перед выкаткой

- Текущая ветка — `main`, `git status` чистый (иначе — спроси, коммитить ли).
- `git fetch origin` и `git merge-base --is-ancestor origin/main main`: если команда влила что-то новое, сначала подтяни (`git pull --no-rebase`), реши конфликты (номера миграций!) и прогони тесты.
- Тесты зелёные: `.venv/bin/python -m pytest --ds=config.test_settings -q`. Красные — остановись и скажи.
- Есть что выкатывать: `git log --oneline origin/main..main` (может быть пусто, если уже запушено — тогда смотри, отстаёт ли сервер).
- Сервер на `main` и без локальных правок:
  `ssh -o BatchMode=yes root@91.108.241.25 "cd /opt/prediction_market/app && sudo -u pm git branch --show-current && sudo -u pm git log --oneline -1 && sudo -u pm git status --short"`

Если сервер ещё на `miniapp` (прод жил на ней до 28.10.2026), один раз переключи его — дальше `update.sh` сам держит сервер на `main`:
  `ssh -o BatchMode=yes root@91.108.241.25 "cd /opt/prediction_market/app && sudo -u pm git fetch -q origin && sudo -u pm git switch main"`

## 2. Выкатка

```bash
git push origin main
ssh -o BatchMode=yes root@91.108.241.25 bash /opt/prediction_market/app/deploy/update.sh
```

`update.sh` делает pull, сборку Mini App, зависимости, миграции, collectstatic и рестарт `pm-web`, `pm-worker`, `pm-bot`. Таймаут команды ставь до 10 минут.

Запусти `setup_bot` на сервере, если менялись `market/management/commands/setup_bot.py`, команды бота или кнопка меню (`git diff <старый коммит сервера>..HEAD --stat` покажет):

```bash
ssh -o BatchMode=yes root@91.108.241.25 "cd /opt/prediction_market/app && sudo -u pm /opt/prediction_market/venv/bin/python manage.py setup_bot"
```

Никогда не запускай `setup_bot` локально — он перепишет кнопку меню у всех пользователей на локальный `SITE_URL`.

Новая системная зависимость (как ffmpeg) — поставь `apt-get install -y <пакет>` и допиши её в `deploy/setup-server.sh`.

## 3. Проверка после

- Сервер на нужном коммите: `sudo -u pm git log --oneline -1` совпадает с локальным `HEAD`.
- `systemctl is-active pm-web pm-worker pm-bot` — все `active`.
- Сайт: `curl -s -o /dev/null -w '%{http_code}' https://91-108-241-25.sslip.io/app/` → 200.
- Логи за 2 минуты: `journalctl -u pm-bot -u pm-worker -u pm-web --since '-2min' --no-pager | grep -iE 'error|traceback|conflict|отклонил'`. Строка gunicorn «Worker … was sent SIGTERM» при рестарте — норма. «Conflict» / «Telegram отклонил запрос» у бота — где-то запущен второй бот с тем же токеном (часто локальный `run_telegram_bot`).

## 4. Отчёт

Коротко: какой коммит теперь на сервере, применённые миграции, статус сервисов и сайта, что найдено в логах, что надо проверить руками в Telegram.
