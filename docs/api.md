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
| `PATCH /api/me` | `{pseudonym?, show_real_name?, onboarded?}` | `Me` |
| `GET /api/categories` | — | `{slug, label}[]`: games «Игры», study «Учёба», weather «Погода», tech «Технологии», sport «Спорт» |
| `GET /api/markets` | `?category=&q=&sort=hot\|new\|soon&page=` (20 на страницу) | `{items: MarketCard[], next_page: number \| null, total: number}` |
| `GET /api/markets/{id}` | — | `MarketDetail` |
| `GET /api/markets/{id}/history` | `?period=d\|w\|m\|all` | `{points: {t: string, p: number}[]}` |
| `POST /api/markets/{id}/quote` | `{side, amount}` | `Quote` |
| `POST /api/markets/{id}/buy` | `{side, amount, request_key: uuid}` — повтор с тем же ключом не списывает второй раз | `TradeResult` |
| `POST /api/markets/{id}/sell` | `{side, shares}` | `TradeResult` |
| `POST /api/markets/{id}/star` | `{starred: boolean}` | `{starred}` |
| `POST /api/markets/{id}/report` | `{reason?: string}` | `{ok: true}` |
| `POST /api/markets` | `{title, criteria, category, closes_at, resolves_at}` | `{market: MarketDetail, balance: Money}` |
| `GET /api/mine` | — | `{positions: (MarketCard & {position: Position})[], watched: MarketCard[], created: (MarketCard & {fee_expected: Money, needs_resolution: boolean, resolve_link: string})[]}` |
| `GET /api/rating` | `?scope=all\|friends&by=profit\|accuracy&period=week\|month\|all` | `{items: {rank, display_name, photo_url, value: string, is_me}[], me: {rank, value} \| null}`; `value` — прибыль (`"123.45"`) или доля верных (`"0.6300"`). Прибыль за неделю/месяц — по рынкам, завершённым за период |
| `GET /api/profile` | — | `{me: Me, stats: {profit: Money, accuracy: number \| null, predictions: number, markets_created: number}, balance_history: {t, balance}[], ledger: LedgerEntry[]}` |
| `POST /api/events` | `{name: "share" \| "onboarding_done" \| "onboarding_skip" \| "open_bot", props?: object}` | `{ok: true}` |

«Указать итог» в «Моих» открывает бота: `https://t.me/<бот>?start=resolve_<id>`; «Оспорить итог» — `https://t.me/<бот>?start=dispute_<id>`.
