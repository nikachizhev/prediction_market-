# JSON API Mini App

Контракт между `miniapp/` и Django. Все пути под `/api/`. Тела и ответы — JSON, суммы — строки с двумя знаками (`"123.45"`), вероятности — числа `0..1`, даты — ISO 8601 с часовым поясом.

## Авторизация

Каждый запрос несёт заголовок `Authorization: tma <initData>` — строку `Telegram.WebApp.initData` как есть. Сервер проверяет HMAC-SHA256 (ключ `HMAC_SHA256("WebAppData", BOT_TOKEN)`) и `auth_date` не старше 24 ч. Нет заголовка или подпись неверна → `401 {"error": "..."}`. Аккаунт создаёт только `POST /api/session`; до него остальные эндпоинты отвечают 401 — клиент сначала ждёт сессию.

## Ошибки

`4xx {"error": "текст для пользователя по-русски"}`. Чужой приватный или скрытый рынок → `404`. Нарушение правил (лимит, баланс, рынок закрыт) → `400`.

## Типы

```ts
type Money = string;                 // "123.45"
type Side = "yes" | "no";
type MarketStatus = "open" | "closed" | "pending" | "disputed" | "resolved" | "annulled";
// open — идёт приём; closed — приём закрыт, ждём итог; pending — итог указан, идёт окно спора;
// disputed — спор у модератора; resolved — завершён; annulled — отменён, деньги возвращены

interface Limits { max_trade: Money; market_cap: Money; fee_rate: number; creation_cost: Money; dispute_cost: Money; b: number; trade_limit_share: number }  // 0.2 — доля баланса на сделку

interface Me {
  id: number; name: string; pseudonym: string; show_real_name: boolean; display_name: string;
  photo_url: string | null; balance: Money; onboarded: boolean;
  interests: string[];                                  // ключи категорий из онбординга
  referral_link: string; referred_by: string | null;     // display_name пригласившего
  invested: { [marketId: number]: Money };               // сколько вложено в открытые рынки (для лимита 500)
  limits: Limits;
}

interface MarketCard {
  id: number; title: string; category: string; prob: number;
  change_24h: number;                // в процентных пунктах, со знаком
  spark: number[];                   // вероятность ДА, 7 точек — по одной на день
  participants: number; volume: Money;
  closes_at: string; resolves_at: string | null; status: MarketStatus; outcome: Side | null;
  starred: boolean;
  my_side: Side | null;              // куда склоняется моя позиция — «Твой прогноз» в ленте
  recommendation_reason?: string;  // пояснение порядка только для sort=recommended
}

interface Position { yes_shares: string; no_shares: string; spent: Money; value: Money; payout_if_yes: Money; payout_if_no: Money }

interface MarketDetail extends MarketCard {
  criteria: string; resolves_at: string; created_at: string;
  author: { display_name: string; photo_url: string | null; resolved: number; disputes_lost: number };
  is_creator: boolean; can_trade: boolean; trade_block: string | null;   // причина, почему ставить нельзя max_amount: Money;   // сколько можно поставить сейчас (лимит сделки, 500 на рынок, баланс)
  my_position: Position | null;
  b: number;                         // глубина рынка LMSR
  trades: { side: Side; amount: Money; prob_before: number; prob_after: number; at: string; sell: boolean }[];   // анонимная лента, последние 20
  resolution: { proposed_outcome: Side | "cancel" | null; evidence_url: string | null; note: string; dispute_until: string | null; can_dispute: boolean } | null;
}

interface Quote { side: Side; amount: Money; fee: Money; cost: Money;   // cost — сколько спишется (≤ amount)
                shares: string; payout_if_win: Money; prob_before: number; prob_after: number; max_amount: Money }
interface TradeResult { balance: Money; market: MarketDetail; trade: { side: Side; shares: string; cost: Money; fee: Money } }

interface LedgerEntry { kind: string; label: string; amount: Money; at: string; market: { id: number; title: string } | null }
```

## Эндпоинты

| Метод и путь | Тело / параметры | Ответ |
| --- | --- | --- |
| `POST /api/session` | `{start_param?: string}` — вызывается при каждом открытии. Создаёт аккаунт (стартовый пакет, пригласивший по `ref_<код>`), начисляет бонус дня, пишет событие `app_open` | `{me: Me, new_account: boolean, granted: {kind: "starter" \| "daily" \| "insurance", amount: Money}[]}` — в день регистрации бонуса дня нет |
| `GET /api/me` | — | `Me` |
| `PATCH /api/me` | `{pseudonym?, show_real_name?, onboarded?, interests?: string[]}` — `interests` только из ключей категорий | `Me` |
| `GET /api/categories` | — | `{slug, label}[]`: games «Игры», study «Учёба», tech «Технологии», sport «Спорт», fun «Фан» (weather «Погода» убрана 28.10.2026) |
| `GET /api/markets` | `?category=&q=&sort=recommended\|hot\|new\|soon&page=` (20 на страницу) | `{items: MarketCard[], next_page: number \| null, total: number}` |
| `GET /api/markets/{id}` | — | `MarketDetail` |
| `GET /api/markets/{id}/history` | `?period=d\|w\|m\|all` | `{points: {t: string, p: number}[]}` |
| `POST /api/markets/{id}/quote` | `{side, amount}` | `Quote` |
| `POST /api/markets/{id}/buy` | `{side, amount, request_key: uuid}` — повтор с тем же ключом не списывает второй раз | `TradeResult` |
| `POST /api/markets/{id}/sell` | `{side, shares}` | `TradeResult` |
| `POST /api/markets/{id}/star` | `{starred: boolean}` | `{starred}` |
| `POST /api/markets/{id}/report` | `{reason?: string}` | `{ok: true}` |
| `POST /api/markets` | `{title, criteria, category, closes_at, resolves_at, request_key?: uuid}` — повтор с тем же ключом возвращает уже созданный рынок и не списывает 100 второй раз | `{market: MarketDetail, balance: Money}` |
| `GET /api/mine` | — | `{positions: (MarketCard & {position: Position})[], watched: MarketCard[], created: (MarketCard & {fee_expected: Money, needs_resolution: boolean, resolve_link: string})[]}` |
| `GET /api/rating` | `?scope=all\|friends&by=profit\|accuracy&period=week\|month\|all` | `{items: {rank, display_name, photo_url, value: string, is_me, predictions: number, accuracy: number \| null, profit: Money}[], me: {rank, value, predictions, accuracy, profit} \| null, min_resolved: number}`; `value` — прибыль (`"123.45"`) или доля верных (`"0.6300"`). Прибыль за неделю/месяц — по рынкам, завершённым за период. `predictions` — завершённые прогнозы за период; в рейтинг по точности среди всех попадают те, у кого их не меньше `min_resolved` (20) |
| `GET /api/profile` | — | `{me: Me, stats: {profit: Money, accuracy: number \| null, correct: number, evaluated: number, rank: number \| null, predictions: number, markets_created: number, disputes_lost: number}, joined_at: string \| null, invited: number, calibration: {price: number, count: number, hits: number}[], best_prediction: {market: {id, title}, side: Side, price: number, gain: Money} \| null, balance_history: {t, balance}[], ledger: LedgerEntry[]}` |
| `POST /api/events` | `{name: "share" \| "onboarding_done" \| "onboarding_skip" \| "open_bot", props?: object}` | `{ok: true}` |

«Указать итог» в «Моих» открывает бота: `https://t.me/<бот>?start=resolve_<id>`; «Оспорить итог» — `https://t.me/<бот>?start=dispute_<id>`.

## Фаза 2 (контракт, 27.10.2026)

Добавления к MVP. Старые поля не меняются.

```ts
type NotifyKind = "results" | "moves" | "closing" | "referral" | "creator";
// results — итог рынка и выплата; moves — вероятность отслеживаемого рынка сдвинулась на 10+ п.п. за сутки;
// closing — до конца приёма прогнозов 24 часа; referral — награда за друга; creator — события моих рынков (спор открыт, решение модератора)

interface Me {                                   // + к MVP
  notify: { [k in NotifyKind]: boolean };        // что бот присылает; в приложении («Уведомления») видно всё
  unread_notifications: number;
}

interface Notice {
  id: number; kind: NotifyKind | string;
  group: "mine" | "pay" | "markets";             // фильтры экрана: Мои рынки / Выплаты / Слежу
  title: string; text: string; at: string; read: boolean;
  market: { id: number; title: string } | null;
  action: "resolve" | "market" | null;           // кнопка: «Указать итог в боте» / «Открыть рынок»
}

interface ReferralSummary {
  invited: number;            // сколько людей пришло по моей ссылке
  rewarded: number;           // за скольких получена награда
  earned: Money;              // сколько монет получено за друзей
  reward: Money;              // "300.00" — по столько обоим
  predictions_needed: number; // 5 — прогнозов в разных рынках
}

interface CategoryStat { slug: string; label: string; predictions: number; correct: number; accuracy: number | null; profit: Money }

interface BestPrediction { market: { id: number; title: string }; side: Side; price: number; gain: Money }

interface MarketDetail {                          // + к MVP
  top_moves: { at: string; from: number; to: number }[];   // до 3 самых резких сдвигов цены одной сделкой, анонимно
}
```

| Метод и путь | Тело / параметры | Ответ |
| --- | --- | --- |
| `PATCH /api/me` | + `{notify?: Partial<{[k in NotifyKind]: boolean}>}` — неизвестные ключи → 400 | `Me` |
| `GET /api/notifications` | `?filter=all\|mine\|pay\|markets` | `{items: Notice[], unread: number}` — последние 50, новые сверху |
| `POST /api/notifications/read` | `{ids?: number[]}` — без `ids` прочитать все | `{unread: number}` |
| `GET /api/profile` | + поля | `+ {referral: ReferralSummary, categories: CategoryStat[], best_predictions: BestPrediction[]}` — `best_predictions` до 3, первым идёт `best_prediction`; `categories` только где есть завершённые прогнозы |
| `GET /api/profile/history` | `?period=d\|w\|m\|all` | `{points: {t: string, balance: Money, profit: Money}[]}` — до 200 точек; `profit` — реализованная прибыль без бонусов: сделки рынка учитываются в момент его завершения |
| `POST /api/markets/{id}/share` | — | `{prepared_message_id: string}` — для `Telegram.WebApp.shareMessage`; `503 {error}`, если Telegram недоступен — тогда клиент открывает обычную ссылку `t.me/share` |

**Ссылка на рынок** — `t.me/<бот>/<приложение>?startapp=ref_<код>-m<id>` (или `m<id>` без приглашения). Сервер берёт код пригласившего до `-m`; Mini App после входа открывает `/market/<id>`.

## Фаза 3a (контракт, 28.10.2026)

Быстрое создание рынка (спека E9.1–E9.3, макет `docs/mockups/quick-create/`). Старые поля не меняются.

```ts
interface Me {                                     // + к MVP
  assist: { enabled: boolean; left: number };      // enabled = false: нет ключа или помощник выключен — карточки помощника нет
}

interface AssistResult {
  question: string | null;   // уточнённая формулировка; null — вопрос и так однозначный
  why: string | null;        // зачем уточнили, одной фразой
  criteria: string;          // «ДА — если… / НЕТ — если… / Чем подтвердить… / Если перенесут или отменят…»
  category: string;          // ключ из GET /api/categories
  closes_at: string;         // уже проверено правилами создания: приём от 1 ч до 1 года
  resolves_at: string;
  left: number;              // сколько подсказок осталось сегодня
}

interface AssistWarning { warning: "forbidden"; text: string; left: number }   // запрещённая тема, поля не заполняем

interface MarketTemplate {
  id: string;
  category: string;
  text: string;              // "Сдам ли {subject} на 4 или 5 до {date}?"
  criteria: string;          // с теми же {key}
  blanks: { key: string; label: string; type: "text" | "date" | "number"; placeholder: string; hint: string }[];
  // сроки: приём до 23:59 накануне {date}, итог — на следующий день после {date}
}
```

| Метод и путь | Тело / параметры | Ответ |
| --- | --- | --- |
| `POST /api/assist` | `{title: string, category?: string}` — `title` от 10 до 120 символов | `AssistResult` или `AssistWarning`; `429 {error}` — лимит 20 в день (МСК); `503 {error}` — помощник выключен, нет ключа или модель не ответила за 10 с, ответ не по схеме тоже 503 |
| `GET /api/templates` | — | `MarketTemplate[]`, по 2 на категорию, в порядке категорий |
| `GET /api/markets/similar` | `?q=` — от 10 символов | `{items: MarketCard[]}` — до 3 открытых рынков, которые видит пользователь (`visible_questions`), похожесть по словам вопроса, без ИИ |
| `POST /api/markets` | + `{assist?: "accepted" \| "edited" \| "undone", template?: string}` | как раньше; оба поля пишутся в событие `market_create` |

Сервер пишет событие `assist_request` с `props.result`: `"ok" | "warning" | "limit" | "error"`. Модели уходят только текст вопроса, категория и сегодняшняя дата. Лимит считают события `assist_request` за московский день, кроме `limit`; при выключенном помощнике события нет. Неизвестные значения `assist` и `template` в `POST /api/markets` отбрасываются, рынок создаётся.

## Фаза 3b (контракт, 29.10.2026)

Закрытые чаты (спека E8, макет `docs/mockups/Chat.dc.html`). Старые поля не меняются. Чат — это `Group` с `kind="chat"`; членство даёт только Telegram (`getChatMember`, ответ кэшируется на 10 минут; событие `chat_member` о выходе закрывает доступ сразу).

```ts
interface ChatRef { code: string; title: string }    // code — случайный код ссылки, не ID чата

interface MarketCard {                                 // + к MVP
  chat: ChatRef | null;      // рынок закрытого чата; в общей ленте, поиске, рекомендациях и похожих таких нет
  created_at: string;        // для пометки «новый» (меньше суток) в рынках чата
}

interface ChatSpace {
  code: string; title: string;
  members: number;           // последний getChatMemberCount
  frozen: boolean;           // бот удалён или снят с админа: новых рынков нет, открытые доживают до итога
  markets: number;           // открытые рынки чата (приём идёт или ждём итог), без скрытых жалобами
  volume: Money;             // объём всех рынков чата
  my_rank: number | null;    // моё место в рейтинге чата по прибыли за всё время; null — нет завершённых прогнозов в чате и прибыль 0 (создатель с доходом место получает)
  creation_cost: Money;      // цена создания рынка в этом чате ("40.00" / "70.00" / "100.00" или своя цена админа 30–200)
  b: number;                 // глубина рынка при этой цене: floor(цена / ln 2)
  assist: boolean;           // ИИ-помощник при создании рынка для этого чата (выключен админом или глобально → false)
  link: string;              // t.me/<бот>/<приложение>?startapp=chat_<code>
}
```

| Метод и путь | Тело / параметры | Ответ |
| --- | --- | --- |
| `GET /api/chats` | — | `{items: ChatSpace[]}` — чаты, где я участник по последней проверке, без запросов к Telegram; сначала с открытыми рынками, дальше по названию |
| `GET /api/chats/{code}` | — | `ChatSpace`. Проверяет членство через `getChatMember` (кэш 10 мин): первый вход по ссылке создаёт членство. Неизвестный код, не участник или вышел → `404` `{error: "Чат не найден."}` — одинаково во всех случаях |
| `GET /api/chats/{code}/markets` | `?page=` (20 на страницу) | `{items: MarketCard[], next_page: number \| null, total: number}` — рынки чата: сначала открытые и ждущие итога (новые сверху), потом завершённые (недавний итог сверху); скрытые жалобами не показываются; `404` как выше |
| `GET /api/chats/{code}/rating` | `?by=profit\|accuracy&period=week\|month\|all` | как `GET /api/rating`; только участники чата и только рынки этого чата; `min_resolved` = 1; неизвестные `by`/`period` — как в `GET /api/rating` (прибыль, всё время) |
| `POST /api/markets` | + `{chat?: string}` — код чата | рынок для чата: `chat` не строка → `400`, пустая строка или `null` — общая лента; не участник или нет чата → `404`, чат заморожен → `400`. Цена и b — по правилам чата на момент создания (`getChatMemberCount`), фиксируются в рынке. В событии `market_create` — `chat: true` (без кода и ID чата). Повтор с тем же `request_key` и другим чатом → `400` |
| `POST /api/assist` | + `{chat?: string}` | в чате с выключенным помощником → `503` как при выключенном помощнике, событие не пишется; не участник → `404`, `chat` не строка → `400` |
| `GET /api/markets/{id}` и действия с рынком | — | рынок чата: членство проверяется (кэш 10 мин); вышел из чата → `404`. Участник чата, открывший ссылку на рынок раньше, чем сам чат, проходит ту же проверку и получает доступ. Выплаты по позициям приходят как обычно |

**Ссылка на чат** — `t.me/<бот>/<приложение>?startapp=chat_<code>`: Mini App после входа открывает `/chat/<code>`. Сервер параметр `chat_` при входе не использует (пригласившего в нём нет).

### Бот в чатах (для бэкенда)

- Callback-данные: `chat:…` — `market/bot_chats.py` (настройки чата), `cdis:…` — `market/chat_disputes.py` (решение спора админом). Маршрутизация — `market/telegram.py`.
- Апдейты групп (`my_chat_member`, `chat_member`, сообщения из `group`/`supergroup`) получает `bot_chats.handle_update`. Бот запрашивает `allowed_updates` с `my_chat_member` и `chat_member`.
- Воркер (`resolution.tick`) раз в минуту вызывает `chat_disputes.tick(now)` и `bot_chats.tick(now)`; оба идемпотентны: наполнение очереди — по уникальным ключам, без хуков в `services.py`.
- Общие функции — `market/chats.py`: `creation_cost`, `depth`, `link`, `check_member`, `set_membership`, `refresh_member_count`, `admins`, `is_admin`, `post_enabled`, `chats_of`, `by_code`. Рейтинг чата — `rating.leaderboard(me_or_None, group=chat)`.
