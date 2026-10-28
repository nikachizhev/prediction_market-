// Pure trading helpers shared by the feed, the trade panel and onboarding.
import {
  ApiError,
  type MarketCard,
  type MarketDetail,
  type Me,
  type Position,
  type Money,
  type Quote,
  type Side,
  type TradeResult,
} from './api';
import { formatMoney } from './format';

/** Default stake when the panel opens (mockup: 100, but never above the limit). */
export const DEFAULT_STAKE = 100;
export const QUICK_STAKES = [10, 50, 100] as const;

export const sideLabel = (side: Side): string => (side === 'yes' ? 'ДА' : 'НЕТ');

/** Coins as an API amount: 50 -> "50.00". */
export const toMoney = (amount: number): Money => amount.toFixed(2);

/** Whole-coin maximum for the slider; the server value may have cents. */
export function maxStake(maxAmount: Money | number): number {
  const n = typeof maxAmount === 'string' ? Number(maxAmount) : maxAmount;
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

/** Keeps a stake a whole number within 1..max (0 when nothing can be staked). */
export function clampStake(amount: number, max: number): number {
  if (max < 1) return 0;
  if (!Number.isFinite(amount)) return 1;
  return Math.min(max, Math.max(1, Math.round(amount)));
}

export function defaultStake(max: number, preferred: number = DEFAULT_STAKE): number {
  return clampStake(preferred, max);
}

export interface QuickStake {
  label: string;
  value: number;
  disabled: boolean;
}

/** Quick buttons 10 / 50 / 100 / Макс; fixed ones above the limit are disabled. */
export function quickStakes(max: number): QuickStake[] {
  return [
    ...QUICK_STAKES.map((v) => ({ label: String(v), value: v, disabled: v > max })),
    { label: 'Макс', value: max, disabled: max < 1 },
  ];
}

/** Whole percents for the ДА / НЕТ buttons; they always add up to 100. */
export function splitPercents(prob: number): { yes: number; no: number } {
  const yes = Math.min(100, Math.max(0, Math.round(prob * 100)));
  return { yes, no: 100 - yes };
}

/** Why the trade panel cannot open for a card, known without asking the server. */
export function cardBlockReason(card: Pick<MarketCard, 'status' | 'closes_at'>, now: Date = new Date()): string | null {
  const expired = new Date(card.closes_at).getTime() <= now.getTime();
  return card.status === 'open' && !expired ? null : 'Приём прогнозов на этот рынок закрыт.';
}

/** Why the trade panel cannot open for a market, or null when trading is possible. */
export function tradeBlockReason(
  market: Pick<MarketDetail, 'id' | 'status' | 'closes_at' | 'is_creator' | 'can_trade' | 'max_amount'>,
  me?: Pick<Me, 'invested' | 'limits'> | null,
): string | null {
  if (market.is_creator) return 'Создатель рынка не может делать прогнозы на свой рынок.';
  const closed = cardBlockReason(market);
  if (closed) return closed;
  if (!market.can_trade) return 'На этот рынок нельзя делать прогнозы.';
  if (maxStake(market.max_amount) < 1) {
    const cap = me ? Number(me.limits.market_cap) : NaN;
    const invested = me ? Number(me.invested[market.id] ?? 0) : 0;
    if (Number.isFinite(cap) && invested >= cap - 1) {
      return `В один рынок можно вложить не больше ${formatMoney(cap)} монет.`;
    }
    return 'Недостаточно монет для прогноза.';
  }
  return null;
}

/**
 * Optimistic view of a card right after a trade: new probability, the day's
 * change and the last sparkline point move with it.
 */
export function applyProbability<T extends MarketCard>(card: T, probAfter: number): T {
  const shift = (probAfter - card.prob) * 100;
  const spark = card.spark.length ? [...card.spark.slice(0, -1), probAfter] : card.spark;
  return { ...card, prob: probAfter, change_24h: card.change_24h + shift, spark };
}

/** Copies the market fields shown on a card from a fresh server response. */
export function mergeMarket<T extends MarketCard>(card: T, market: MarketCard): T {
  return {
    ...card,
    prob: market.prob,
    change_24h: market.change_24h,
    spark: market.spark,
    participants: market.participants,
    volume: market.volume,
    status: market.status,
    outcome: market.outcome,
    starred: market.starred,
  };
}

/** Replaces the card with the same id (a no-op when it is not in the list). */
export function replaceCard<T extends MarketCard>(cards: T[], card: T): T[] {
  return cards.map((c) => (c.id === card.id ? card : c));
}

/** The side of a position and what it pays if that side wins. */
export function positionSummary(position: Position | null | undefined): { side: Side; payout: number } | null {
  if (!position) return null;
  const yes = Number(position.yes_shares);
  const no = Number(position.no_shares);
  if (yes <= 0 && no <= 0) return null;
  return yes >= no
    ? { side: 'yes', payout: Number(position.payout_if_yes) }
    : { side: 'no', payout: Number(position.payout_if_no) };
}

/** Toast after a confirmed purchase (text from docs/mockups/Main.dc.html). */
export function acceptedMessage(side: Side, amount: number | string, shares: string): string {
  const payout = Math.floor(Number(shares));
  return `Принято: ${sideLabel(side)} на ${formatMoney(amount)}. Выплата, если угадаешь: ${formatMoney(payout, 0)}`;
}

/** Optimistic charge: the quoted cost when the quote matches the stake, else the stake itself. */
export function optimisticCost(amount: number, side: Side, quote: Pick<Quote, 'side' | 'amount' | 'cost'> | null): number {
  if (quote && quote.side === side && Number(quote.amount) === amount) return Number(quote.cost);
  return amount;
}

export interface OptimisticBuy {
  /** Sends the purchase; must reuse one request_key across calls. */
  send: () => Promise<TradeResult>;
  /** Coins to take from the balance right away. */
  cost: number;
  /** Probability of ДА to show right away, or null when unknown. */
  probAfter: number | null;
  adjustBalance: (delta: number) => void;
  setBalance: (balance: Money) => void;
  onOptimistic?: (probAfter: number) => void;
  onCommit?: (result: TradeResult) => void;
  onRollback?: () => void;
  retryDelays?: number[];
  sleep?: (ms: number) => Promise<void>;
}

export type BuyOutcome = { ok: true; result: TradeResult } | { ok: false; error: ApiError };

/**
 * Shows the trade immediately (balance and price), then confirms it with the
 * server. On refusal everything shown optimistically is rolled back.
 */
export async function runOptimisticBuy(op: OptimisticBuy): Promise<BuyOutcome> {
  op.adjustBalance(-op.cost);
  if (op.probAfter !== null) op.onOptimistic?.(op.probAfter);
  try {
    const result = await withNetworkRetry(op.send, op.retryDelays, op.sleep);
    op.setBalance(result.balance);
    op.onCommit?.(result);
    return { ok: true, result };
  } catch (err) {
    op.adjustBalance(op.cost);
    op.onRollback?.();
    const error = err instanceof ApiError ? err : new ApiError(0, 'Что-то пошло не так. Попробуйте ещё раз.');
    return { ok: false, error };
  }
}

export const isNetworkError =(err: unknown): boolean => err instanceof ApiError && err.status === 0;

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Runs `attempt` and repeats it after network failures only. The caller keeps
 * the same request_key inside `attempt`, so a repeat never charges twice.
 */
export async function withNetworkRetry<T>(
  attempt: () => Promise<T>,
  delays: number[] = [600, 1500],
  sleep: (ms: number) => Promise<void> = wait,
): Promise<T> {
  for (let i = 0; ; i++) {
    try {
      return await attempt();
    } catch (err) {
      if (!isNetworkError(err) || i >= delays.length) throw err;
      await sleep(delays[i]);
    }
  }
}
