// Client for the Mini App JSON API (contract: docs/api.md).
import { tg } from './telegram';

// ---- Types (docs/api.md, section «Типы») ----

export type Money = string;
export type Side = 'yes' | 'no';
export type MarketStatus = 'open' | 'closed' | 'pending' | 'disputed' | 'resolved' | 'annulled';

export interface Limits {
  max_trade: Money;
  market_cap: Money;
  fee_rate: number;
  creation_cost: Money;
  dispute_cost: Money;
  b: number;
  /** Share of the balance allowed per trade (0.2). */
  trade_limit_share?: number;
}

/** What the bot may send (docs/api.md, «Фаза 2»). */
export type NotifyKind = 'results' | 'moves' | 'closing' | 'referral' | 'creator';
export type NotifySettings = { [k in NotifyKind]: boolean };

export interface Me {
  id: number;
  name: string;
  pseudonym: string;
  show_real_name: boolean;
  display_name: string;
  photo_url: string | null;
  balance: Money;
  onboarded: boolean;
  /** Category slugs picked in onboarding («Что тебе интересно?»). */
  interests: string[];
  referral_link: string;
  referred_by: string | null;
  invested: { [marketId: number]: Money };
  limits: Limits;
  /** Phase 2; missing on an older server. */
  notify?: NotifySettings;
  /** Phase 2; missing on an older server. */
  unread_notifications?: number;
  /** AI rules helper (phase 3a); missing on an older server, which means it is off. */
  assist?: AssistState;
}

export interface SubmarketCard extends MarketCard {
  label: string;
  can_trade?: boolean;
  trade_block?: string | null;
  max_amount?: Money;
  my_position?: Position | null;
  trades?: { side: Side; amount: Money; prob_after: number; at: string; sell: boolean }[];
  top_moves?: TopMove[];
  resolution?: {
    proposed_outcome: Side | 'cancel' | null;
    evidence_url: string | null;
    note: string;
    dispute_until: string | null;
    can_dispute: boolean;
  } | null;
}

export interface MarketCard {
  id: number;
  title: string;
  category: string;
  prob: number;
  change_24h: number;
  spark: number[];
  participants: number;
  volume: Money;
  closes_at: string;
  status: MarketStatus;
  outcome: Side | null;
  starred: boolean;
  /** Where my position leans («Твой прогноз»). */
  my_side?: Side | null;
  /** Child binary options for a multiple-choice market. */
  submarkets?: SubmarketCard[];
  recommendation_reason?: string;
}

export interface Position {
  yes_shares: string;
  no_shares: string;
  spent: Money;
  value: Money;
  payout_if_yes: Money;
  payout_if_no: Money;
}

export type SubmarketDetail = SubmarketCard;

export interface MarketDetail extends MarketCard {
  criteria: string;
  resolves_at: string;
  created_at: string;
  author: { display_name: string; photo_url: string | null; resolved: number; disputes_lost: number };
  is_creator: boolean;
  can_trade: boolean;
  /** Why trading is not possible, in Russian; null when it is. */
  trade_block?: string | null;
  /** How much can be staked right now (trade limit, per-market cap, balance). */
  max_amount: Money;
  my_position: Position | null;
  trades: { side: Side; amount: Money; prob_after: number; at: string; sell: boolean }[];
  /** Up to 3 sharpest single-trade price moves, anonymous (phase 2; may be missing). */
  top_moves?: TopMove[];
  resolution: {
    proposed_outcome: Side | 'cancel' | null;
    evidence_url: string | null;
    note: string;
    dispute_until: string | null;
    can_dispute: boolean;
  } | null;
}

export interface TopMove {
  at: string;
  from: number;
  to: number;
}

export interface Quote {
  side: Side;
  amount: Money;
  fee: Money;
  /** What will actually be charged (<= amount). */
  cost: Money;
  shares: string;
  payout_if_win: Money;
  prob_before: number;
  prob_after: number;
  max_amount: Money;
}

export interface TradeResult {
  balance: Money;
  market: MarketDetail;
  trade: { side: Side; shares: string; cost: Money; fee: Money };
}

export interface LedgerEntry {
  kind: string;
  label: string;
  amount: Money;
  at: string;
  market: { id: number; title: string } | null;
}

export interface Grant {
  kind: 'starter' | 'daily' | 'insurance';
  amount: Money;
}

export interface Session {
  me: Me;
  new_account: boolean;
  granted: Grant[];
}

export interface Category {
  slug: string;
  label: string;
}

export type EventName = 'share' | 'onboarding_done' | 'onboarding_skip' | 'open_bot' | 'feed_impression' | 'market_open';

export type NoticeGroup = 'mine' | 'pay' | 'markets';
export type NoticeFilter = 'all' | NoticeGroup;

export interface Notice {
  id: number;
  kind: NotifyKind | string;
  group: NoticeGroup;
  title: string;
  text: string;
  at: string;
  read: boolean;
  market: { id: number; title: string } | null;
  /** Button: «Указать итог в боте» / «Открыть рынок». */
  action: 'resolve' | 'market' | null;
}

export interface NoticesResponse {
  items: Notice[];
  unread: number;
}

export type MarketSort = 'recommended' | 'hot' | 'new' | 'soon';
export type HistoryPeriod = 'd' | 'w' | 'm' | 'all';

export interface MineResponse {
  positions: (MarketCard & { position: Position })[];
  watched: MarketCard[];
  created: (MarketCard & { fee_expected: Money; needs_resolution: boolean; resolve_link: string })[];
}

/** Aggregates only: nobody's trades are exposed. */
export interface RatingRow {
  rank: number;
  value: string;
  /** Resolved predictions in the period. */
  predictions: number;
  /** Share of correct ones, 0..1; null without resolved predictions. */
  accuracy: number | null;
  /** Profit in the period, bonuses excluded. */
  profit: Money;
}

export interface RatingResponse {
  items: (RatingRow & { display_name: string; photo_url: string | null; is_me: boolean })[];
  me: RatingRow | null;
  /** Resolved predictions needed for the overall accuracy rating. */
  min_resolved: number;
}

export interface CalibrationBucket {
  /** Middle of the price bucket, percent: 10, 30, 50, 70, 90. */
  price: number;
  count: number;
  hits: number;
}

export interface BestPrediction {
  market: { id: number; title: string };
  side: 'yes' | 'no';
  /** Price of the bought side at purchase, 0..1. */
  price: number;
  gain: Money;
}

export interface ReferralSummary {
  /** People who came by my link. */
  invited: number;
  /** Friends a reward was received for. */
  rewarded: number;
  /** Coins received for friends. */
  earned: Money;
  /** Reward for each side, "300.00". */
  reward: Money;
  /** Predictions in different markets a friend needs (5). */
  predictions_needed: number;
}

export interface CategoryStat {
  slug: string;
  label: string;
  predictions: number;
  correct: number;
  accuracy: number | null;
  profit: Money;
}

export interface ProfileHistoryPoint {
  t: string;
  balance: Money;
  /** Realized profit without bonuses. */
  profit: Money;
}

export interface ProfileResponse {
  me: Me;
  stats: {
    profit: Money;
    accuracy: number | null;
    correct: number;
    evaluated: number;
    /** Place in the all-time profit rating. */
    rank: number | null;
    predictions: number;
    markets_created: number;
    disputes_lost: number;
  };
  joined_at: string | null;
  invited: number;
  calibration: CalibrationBucket[];
  best_prediction: BestPrediction | null;
  balance_history: { t: string; balance: Money }[];
  ledger: LedgerEntry[];
  /** Phase 2 fields; missing on an older server. */
  referral?: ReferralSummary;
  categories?: CategoryStat[];
  best_predictions?: BestPrediction[];
}

// ---- Quick market creation (docs/api.md, «Фаза 3a») ----

export interface AssistState {
  /** False: no key on the server or the helper is switched off; no helper card then. */
  enabled: boolean;
  /** Suggestions left today. */
  left: number;
}

export interface AssistResult {
  /** A clearer wording; null when the question is unambiguous already. */
  question: string | null;
  /** Why it was clarified, in one sentence. */
  why: string | null;
  criteria: string;
  /** Category slug from GET /api/categories. */
  category: string;
  closes_at: string;
  resolves_at: string;
  left: number;
}

/** Forbidden topic: nothing is filled in. */
export interface AssistWarning {
  warning: 'forbidden';
  text: string;
  left: number;
}

export function isAssistWarning(value: AssistResult | AssistWarning): value is AssistWarning {
  return 'warning' in value;
}

export interface TemplateBlank {
  key: string;
  label: string;
  type: 'text' | 'date' | 'number';
  placeholder: string;
  /** Shown in the question while the blank is empty: "[предмет]". */
  hint: string;
}

export interface MarketTemplate {
  id: string;
  category: string;
  /** "Сдам ли {subject} на 4 или 5 до {date}?" */
  text: string;
  /** Uses the same {key} blanks. */
  criteria: string;
  blanks: TemplateBlank[];
}

/** How the creator treated the helper's suggestion (analytics only). */
export type AssistOutcome = 'accepted' | 'edited' | 'undone';

export interface CreateMarketData {
  title: string;
  criteria: string;
  category: string;
  closes_at: string;
  resolves_at: string;
  assist?: AssistOutcome;
  /** Id of the template the market was made from. */
  template?: string;
  /** Option names for multiple-choice markets. */
  options?: string[];
}

// ---- Transport ----

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

type Query = Record<string, string | number | undefined | null>;

function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  // The header lets API calls pass ngrok's free-tier browser warning during development; others ignore it.
  const headers: Record<string, string> = { Accept: 'application/json', 'ngrok-skip-browser-warning': '1' };
  const initData = tg.initData;
  if (initData) headers.Authorization = `tma ${initData}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, 'Нет соединения. Проверьте интернет и попробуйте ещё раз.');
  }

  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error =
      data && typeof data === 'object' && 'error' in data && typeof data.error === 'string'
        ? data.error
        : 'Что-то пошло не так. Попробуйте ещё раз.';
    throw new ApiError(response.status, error);
  }
  return data as T;
}

function newRequestKey(): string {
  return crypto.randomUUID();
}

// ---- Endpoints ----

export const api = {
  session: (start_param?: string) => request<Session>('POST', '/session', start_param ? { start_param } : {}),

  me: () => request<Me>('GET', '/me'),

  updateMe: (patch: {
    pseudonym?: string;
    show_real_name?: boolean;
    onboarded?: boolean;
    interests?: string[];
    notify?: Partial<NotifySettings>;
  }) => request<Me>('PATCH', '/me', patch),

  notifications: (filter: NoticeFilter = 'all') =>
    request<NoticesResponse>('GET', withQuery('/notifications', { filter })),

  /** Without ids marks everything read. */
  readNotifications: (ids?: number[]) =>
    request<{ unread: number }>('POST', '/notifications/read', ids ? { ids } : {}),

  profileHistory: (period: HistoryPeriod = 'm') =>
    request<{ points: ProfileHistoryPoint[] }>('GET', withQuery('/profile/history', { period })),

  /** Prepared message for Telegram.WebApp.shareMessage; 503 when Telegram is unavailable. */
  shareMarket: (id: number) => request<{ prepared_message_id: string }>('POST', `/markets/${id}/share`),

  categories: () => request<Category[]>('GET', '/categories'),

  markets: (params: { category?: string; q?: string; sort?: MarketSort; page?: number } = {}) =>
    request<{ items: MarketCard[]; next_page: number | null }>('GET', withQuery('/markets', params)),

  market: (id: number) => request<MarketDetail>('GET', `/markets/${id}`),

  history: (id: number, period: HistoryPeriod = 'w') =>
    request<{ points: { t: string; p: number }[] }>('GET', withQuery(`/markets/${id}/history`, { period })),

  quote: (id: number, side: Side, amount: Money) => request<Quote>('POST', `/markets/${id}/quote`, { side, amount }),

  /** request_key makes the purchase idempotent; pass the same key when retrying. */
  buy: (id: number, side: Side, amount: Money, request_key: string = newRequestKey()) =>
    request<TradeResult>('POST', `/markets/${id}/buy`, { side, amount, request_key }),

  sell: (id: number, side: Side, shares: string) => request<TradeResult>('POST', `/markets/${id}/sell`, { side, shares }),

  star: (id: number, starred: boolean) => request<{ starred: boolean }>('POST', `/markets/${id}/star`, { starred }),
  dismissMarket: (id: number, dismissed = true) => request<{ dismissed: boolean }>('POST', `/markets/${id}/dismiss`, { dismissed }),
  resetHiddenMarkets: () => request<{ restored: number }>('POST', '/feed/reset-hidden'),

  report: (id: number, reason?: string) => request<{ ok: true }>('POST', `/markets/${id}/report`, { reason }),

  /** request_key makes creation idempotent; pass the same key when retrying the same submission. */
  createMarket: (data: CreateMarketData, request_key: string = newRequestKey()) =>
    request<{
      market: MarketDetail;
      balance: Money;
      /** Whether the bot's note about the outcome reached the creator; null on a replayed request, absent on an older server. */
      bot_notified?: boolean | null;
    }>('POST', '/markets', { ...data, request_key }),

  /** AI rules helper; 429 — the daily limit is used up, 503 — off or the model did not answer. */
  assist: (title: string, category?: string) =>
    request<AssistResult | AssistWarning>('POST', '/assist', category ? { title, category } : { title }),

  templates: () => request<MarketTemplate[]>('GET', '/templates'),

  /** Up to 3 open markets with a similar question; `q` of at least 10 characters. */
  similarMarkets: (q: string) => request<{ items: MarketCard[] }>('GET', withQuery('/markets/similar', { q })),

  mine: () => request<MineResponse>('GET', '/mine'),

  rating: (
    params: { scope?: 'all' | 'friends'; by?: 'profit' | 'accuracy'; period?: 'week' | 'month' | 'all' } = {},
  ) => request<RatingResponse>('GET', withQuery('/rating', params)),

  profile: () => request<ProfileResponse>('GET', '/profile'),

  event: (name: EventName, props?: Record<string, unknown>) => request<{ ok: true }>('POST', '/events', { name, props }),
};

export { newRequestKey };
