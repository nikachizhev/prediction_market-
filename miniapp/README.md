# Mini App

SPA на React 18 + Vite 5 + TypeScript. Нужен Node 18+.

```bash
cd miniapp
npm install
npm run dev          # http://localhost:5173/app/ — /api и /media проксируются на http://127.0.0.1:8000
npm run build        # сборка в miniapp/dist, Django раздаёт её по /app/
npm test -- --run    # юнит-тесты vitest
npm run typecheck
```

Бэкенд (`manage.py runserver`) должен быть запущен на `127.0.0.1:8000`.

## Запуск вне Telegram

В обычном браузере у приложения нет `Telegram.WebApp.initData`, поэтому API ответит 401. Для разработки задайте подписанную строку initData в `miniapp/.env.local` (образец — `miniapp/.env.example`):

```bash
# из корня репозитория; Windows: .\.venv\Scripts\python.exe
.venv/bin/python manage.py make_init_data
```

Скопируйте вывод в `miniapp/.env.local`:

```
VITE_DEV_INIT_DATA=query_id=...&user=...&auth_date=...&hash=...
```

и перезапустите `npm run dev`. Подпись действует 24 часа. Параметр запуска (`start_param`) в браузере можно передать как `?startapp=ref_abc`.

Тема в браузере берётся из системной (`prefers-color-scheme`), в Telegram — из `colorScheme` клиента.
