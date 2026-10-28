# Локальный мониторинг

Развёртывание на действующем VPS: [серверная инструкция](observability-server.md).

Изолированный стек с синтетическими данными: 8 пользователей, 6 рынков, покупки/продажи, выплаты, возвраты и спор. Реальный Telegram отключён. Боевой `.env` не попадает в образ; база и тома отдельные.

```bash
docker compose -f compose.observability.yaml up -d --build
```

- Grafana · бизнес: http://localhost:13000/d/prediction-market — пользователи, торговля, рынки, экономика.
- Grafana · техника: http://localhost:13000/d/prediction-market-technical — HTTP, БД, сбор метрик и воркер.
- Вход в оба: `admin` / `local-observability`. Вверху — ссылки для переключения с сохранением выбранного периода.
- Prometheus: http://localhost:19090.
- Django: http://localhost:18000 (API/старые страницы; сборка Mini App в этот образ не включена).

Все опубликованные порты привязаны к localhost. Учётные данные намеренно демонстрационные; конфигурация не предназначена для публикации. `/metrics` требует `Authorization: Bearer local-observability-scrape`; воркер слушает 9101 только внутри сети Compose.

```bash
docker compose -f compose.observability.yaml ps
docker compose -f compose.observability.yaml logs --tail=50 app worker
docker compose -f compose.observability.yaml stop
# Повторный запуск сохраняет данные:
docker compose -f compose.observability.yaml up -d
```

Не добавляйте `down -v`, если хотите сохранить историю. Существующий `compose.yaml` и production-деплой не изменены.

## Метрики

Бизнес-снимки — **Gauge**, даже накопленные итоги. Их нельзя использовать с `rate()`/`increase()`: удаление данных может уменьшить значение. `window=24h|7d` — скользящие окна относительно момента сбора; `all` — вся доступная история. Будущие записи исключены. История графиков начинается с запуска Prometheus, старые временные ряды автоматически не восстанавливаются.

| Метрика | Определение |
|---|---|
| `pm_users{kind,window}` | Mini App аккаунты со стартовой записью LedgerEntry; registered — регистрации, active — уникальные app_open, trading — уникальные трейдеры |
| `pm_trades{direction,window}` | Число зафиксированных Trade; повтор покупки с тем же request_key не увеличивает итог |
| `pm_trade_volume_coins` | Сумма abs(Trade.cost), включая комиссию; покупки/продажи отдельно (направление по знаку shares, в том числе продажи с нулевой выручкой). Виртуальные монеты, не выручка |
| `pm_trade_fees_coins` | Начисленная комиссия до возврата/разделения |
| `pm_markets{state}` | Пользовательские LMSR с creator и resolves_at; просроченный open показывается как closed |
| `pm_markets_created{window}` | Созданные рынки той же группы |
| `pm_balance_coins` | Сумма балансов Mini App аккаунтов |
| `pm_ledger_coins{kind,window}` | Абсолютные потоки журнала: бонусы, выплаты, возвраты, комиссия автору и прочее |
| `pm_disputes{state}` | Споры по рынкам указанной группы: pending/upheld/rejected |

Торговые метрики ограничены этими рынками и Mini App аккаунтами. Журнал и баланс охватывают все операции этих аккаунтов. Регистрации определяются по `Trader.created_at`, принадлежность к Mini App — наличием стартовой записи. Это отделяет старые почтовые аккаунты без стартового пакета. Агрегаты выполняются последовательными запросами: при активной торговле соседние значения могут относиться к чуть разным моментам.

Технические:

- `pm_http_requests_total{method,route,status}` — ответы, включая 4xx/5xx; `/metrics` исключён. Route — имя URL, а не путь с ID. Неизвестные методы/маршруты объединены.
- `pm_http_request_duration_seconds` — время формирования ответа, без передачи streaming body. Histogram; p50/p95/p99. Без трафика квантили не определены.
- `pm_database_up`, `pm_business_collection_duration_seconds`, `pm_business_collection_failures_total`, `pm_business_last_success_timestamp_seconds` — состояние БД/сбора. При ошибке бизнес-серии отсутствуют, не заменяются нулями.
- `pm_worker_ticks_total{result}`, `pm_worker_tick_duration_seconds`, `pm_worker_last_success_timestamp_seconds`, `pm_worker_telegram_failures_total{action}` — циклы и ошибки фонового воркера.
- `up{job}` — доступность app/worker/prometheus. Недоступный воркер проверяется отдельно от heartbeat.

Имена, Telegram ID, ID рынков, параметры запросов и тексты ошибок не попадают в labels. Сбор раз в 10 секунд; хранение Prometheus — 15 дней. Для большого объёма БД нужен отдельный кеширующий exporter/предагрегация: текущий вариант рассчитан на локальную MVP-нагрузку.

## Реализация и проверки

Новая зависимость `prometheus-client` обеспечивает формат Prometheus, Histogram и агрегацию нескольких Gunicorn workers. Каждый запуск master получает новую временную папку счётчиков. Перезапуск одного worker сохраняет Counter; новый master обнуляет процессные счётчики, что поддерживается `rate()`. Бизнес-данные сохраняются в БД. Пользовательский DB collector вызывается один раз на scrape отдельно от multiprocess registry.

Оба dashboard и datasource provisioned из `deploy/observability/grafana/`. Изменять панели через `build_dashboard.py`, затем запускать его и перезапускать Grafana при необходимости. Внешняя доставка алертов не настроена; предупреждения видны на dashboard.

```bash
.venv/bin/python -m pytest --ds=config.test_settings -q
# Windows: .\.venv\Scripts\python.exe вместо .venv/bin/python
# PostgreSQL проверки внутри изолированного стека:
docker compose -f compose.observability.yaml exec app python -m pytest --ds=config.postgres_test_settings market/tests/test_observability.py market/tests/test_purchase_concurrency.py -q
```

Пустые бизнес-наборы дают нули; HTTP требует запросов. Для проверки выполните несколько запросов к `/`, `/api/me` (401), `/missing-demo` (404). В demo Mini App-вход без Telegram ожидаемо не работает.

Настройки обычного приложения: `METRICS_ENABLED=1`, непустой `METRICS_TOKEN`; по умолчанию экспорт выключен. Worker HTTP endpoint включается только при `WORKER_METRICS_PORT`, по умолчанию выключен и bind localhost. Production-настройки и защита сети требуют отдельного развёртывания.

Проверка работающего стека с реальным HTTP-трафиком (около 80 секунд):

```bash
.venv/bin/python deploy/observability/verify.py
```

Проверяет все provisioned панели через Prometheus, datasource через Grafana, цели scrape, 200/401/404 и ожидаемые демо-итоги. Playwright для визуальной проверки использовался только как локальный инструмент, не является зависимостью приложения.
