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
}

export interface Me {
  id: number;
  name: string;
  pseudonym: string;
  show_real_name: boolean;
  display_name: string;
  photo_url: string | null;
  balance: Money;
  onboarded: boolean;
  referral_link: string;
  referred_by: string | null;
  invested: { [marketId: number]: Money };
  limits: Limits;
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
}

export interface Position {
  yes_shares: string;
  no_shares: string;
  spent: Money;
  value: Money;
  payout_if_yes: Money;
  payout_if_no: Money;
}

export interface MarketDetail extends MarketCard {
  criteria: string;
  resolves_at: string;
  created_at: string;
  author: { display_name: string; photo_url: string | null; resolved: number; disputes_lost: number };
  is_creator: boolean;
  can_trade: boolean;
  my_position: Position | null;
  trades: { side: Side; amount: Money; prob_after: number; at: string }[];
  resolution: {
    proposed_outcome: Side | 'cancel' | null;
    evidence_url: string | null;
    note: string;
    dispute_until: string | null;
    can_dispute: boolean;
  } | null;
}

export interface Quote {
  side: Side;
  amount: Money;
  fee: Money;
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
  kind: 'starter' | 'daily';
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

export type MarketSort = 'hot' | 'new' | 'soon';
export type HistoryPeriod = 'd' | 'w' | 'm' | 'all';

export interface MineResponse {
  positions: (MarketCard & { position: Position })[];
  watched: MarketCard[];
  created: (MarketCard & { fee_expected: Money; needs_resolution: boolean })[];
}

export interface RatingResponse {
  items: { rank: number; display_name: string; photo_url: string | null; value: string; is_me: boolean }[];
  me: { rank: number; value: string } | null;
}

export interface ProfileResponse {
  me: Me;
  stats: { profit: Money; accuracy: number | null; predictions: number; markets_created: number };
  balance_history: { t: string; balance: Money }[];
  ledger: LedgerEntry[];
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
  const headers: Record<string, string> = { Accept: 'application/json' };
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

  updateMe: (patch: { pseudonym?: string; show_real_name?: boolean; onboarded?: boolean }) =>
    request<Me>('PATCH', '/me', patch),

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

  report: (id: number, reason?: string) => request<{ ok: true }>('POST', `/markets/${id}/report`, { reason }),

  createMarket: (data: { title: string; criteria: string; category: string; closes_at: string; resolves_at: string }) =>
    request<{ market: MarketDetail; balance: Money }>('POST', '/markets', data),

  mine: () => request<MineResponse>('GET', '/mine'),

  rating: (
    params: { scope?: 'all' | 'friends'; by?: 'profit' | 'accuracy'; period?: 'week' | 'month' | 'all' } = {},
  ) => request<RatingResponse>('GET', withQuery('/rating', params)),

  profile: () => request<ProfileResponse>('GET', '/profile'),

  event: (name: string, props?: Record<string, unknown>) => request<{ ok: true }>('POST', '/events', { name, props }),
};

export { newRequestKey };
