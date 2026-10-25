// Pure helpers for the market card and «Мои» (docs/mockups/Market.dc.html, Mine.dc.html).
import type { HistoryPeriod, MarketStatus, Position, Side } from './api';
import { formatDayMonth, pluralize } from './format';

// ---- Chart ----

export interface HistoryPoint {
  t: string;
  p: number;
}

/** Geometry of the chart from the mockup: 358×180, plot area 0..322 × 10..150. */
export const CHART = { width: 358, height: 180, plotWidth: 322, top: 10, bottom: 150 } as const;

export const PERIODS: { key: HistoryPeriod; label: string; word: string }[] = [
  { key: 'd', label: 'Д', word: 'за сутки' },
  { key: 'w', label: 'Н', word: 'за неделю' },
  { key: 'm', label: 'М', word: 'за месяц' },
  { key: 'all', label: 'Всё', word: 'за всё время' },
];

export const periodWord = (period: HistoryPeriod): string => PERIODS.find((p) => p.key === period)?.word ?? '';

export interface ChartPoint {
  x: number;
  y: number;
  t: number;
  p: number;
}

export interface ChartGeometry {
  /** SVG path of the probability line (a step line: the price only moves on trades). */
  line: string;
  /** Closed path for the light fill under the line. */
  area: string;
  points: ChartPoint[];
  end: ChartPoint | null;
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
const fx = (n: number) => n.toFixed(1);

/** Y coordinate of a probability on the chart. */
export const chartY = (p: number): number => CHART.bottom - clamp01(p) * (CHART.bottom - CHART.top);

/**
 * Builds the chart from history points. X is proportional to time; when all
 * points share one moment they are spread evenly. The line holds each value
 * until the next trade, which is how the price actually behaves.
 */
export function buildChart(history: HistoryPoint[]): ChartGeometry {
  const parsed = history
    .map((h) => ({ t: new Date(h.t).getTime(), p: h.p }))
    .filter((h) => Number.isFinite(h.t) && Number.isFinite(h.p));
  if (parsed.length === 0) return { line: '', area: '', points: [], end: null };
  const series = parsed.length === 1 ? [parsed[0], parsed[0]] : parsed;
  const t0 = series[0].t;
  const span = series[series.length - 1].t - t0;
  const points: ChartPoint[] = series.map((h, i) => ({
    x: span > 0 ? ((h.t - t0) / span) * CHART.plotWidth : (i / (series.length - 1)) * CHART.plotWidth,
    y: chartY(h.p),
    t: h.t,
    p: h.p,
  }));
  let line = `M${fx(points[0].x)} ${fx(points[0].y)}`;
  for (let i = 1; i < points.length; i++) {
    const prev = points[i - 1];
    const pt = points[i];
    if (pt.x !== prev.x) line += ` H${fx(pt.x)}`;
    if (pt.y !== prev.y) line += ` V${fx(pt.y)}`;
  }
  const last = points[points.length - 1];
  const area = `${line} V${CHART.bottom} H${fx(points[0].x)} Z`;
  return { line, area, points, end: last };
}

/** Index of the point shown under the finger at chart x (the last one at or left of it). */
export function pointIndexAt(points: ChartPoint[], x: number): number {
  if (points.length === 0) return -1;
  let index = 0;
  for (let i = 0; i < points.length; i++) {
    if (points[i].x <= x + 0.0001) index = i;
    else break;
  }
  return index;
}

/** Change over the shown period in whole percentage points. */
export function periodChange(history: HistoryPoint[]): number {
  if (history.length < 2) return 0;
  return Math.round(history[history.length - 1].p * 100) - Math.round(history[0].p * 100);
}

/** «▲ 4 за неделю» / «▼ 3 за сутки». */
export function changeLabel(points: number, word: string): string {
  return `${points >= 0 ? '▲' : '▼'} ${Math.abs(points)} ${word}`;
}

// ---- Time ----

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** «только что», «5 мин назад», «2 ч назад», «3 дн. назад». */
export function timeAgo(iso: string | number, now: Date = new Date()): string {
  const t = typeof iso === 'number' ? iso : new Date(iso).getTime();
  const diff = now.getTime() - t;
  if (diff < MINUTE) return 'только что';
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)} мин назад`;
  if (diff < DAY) return `${Math.floor(diff / HOUR)} ч назад`;
  return `${Math.round(diff / DAY)} дн. назад`;
}

/** Time left in the dispute window: «ещё 18 ч 24 мин»; null when it is over. */
export function timeLeft(until: string | null, now: Date = new Date()): string | null {
  if (!until) return null;
  const left = new Date(until).getTime() - now.getTime();
  if (!(left > 0)) return null;
  const hours = Math.floor(left / HOUR);
  const minutes = Math.floor((left % HOUR) / MINUTE);
  if (hours >= 48) return `ещё ${pluralize(Math.floor(hours / 24), 'день', 'дня', 'дней')}`;
  if (hours === 0) return `ещё ${Math.max(1, minutes)} мин`;
  return `ещё ${hours} ч ${minutes} мин`;
}

/** Value of the «До итога» tile: «67 дней», «меньше суток» or null when the date has passed. */
export function untilResult(resolvesAt: string, now: Date = new Date()): string | null {
  const left = new Date(resolvesAt).getTime() - now.getTime();
  if (!(left > 0)) return null;
  const days = Math.floor(left / DAY);
  return days === 0 ? 'меньше суток' : pluralize(days, 'день', 'дня', 'дней');
}

// ---- Status ----

export type MarketPhase = 'open' | 'closed' | 'review' | 'resolved' | 'annulled';
export type StatusTone = 'yes' | 'gold' | 'muted';

export interface StatusView {
  phase: MarketPhase;
  label: string;
  tone: StatusTone;
}

/** Which state of the market card to show for an API status. */
export function statusView(status: MarketStatus): StatusView {
  switch (status) {
    case 'open':
      return { phase: 'open', label: 'Идёт приём прогнозов', tone: 'yes' };
    case 'closed':
      return { phase: 'closed', label: 'Ждём итог', tone: 'gold' };
    case 'pending':
      return { phase: 'review', label: 'Итог на проверке', tone: 'gold' };
    case 'disputed':
      return { phase: 'review', label: 'Итог оспорен', tone: 'gold' };
    case 'resolved':
      return { phase: 'resolved', label: 'Завершён', tone: 'muted' };
    case 'annulled':
      return { phase: 'annulled', label: 'Отменён', tone: 'muted' };
  }
}

/** Outcome as shown big on the card: «ДА», «НЕТ» or «Отмена». */
export function outcomeLabel(outcome: Side | 'cancel' | null | undefined): string {
  if (outcome === 'yes') return 'ДА';
  if (outcome === 'no') return 'НЕТ';
  if (outcome === 'cancel') return 'Отмена';
  return '—';
}

/** Status line of a market in «Созданные мной». */
export function createdStatusLine(
  market: { status: MarketStatus; outcome: Side | null; participants: number; closes_at: string },
  ends: string,
): string {
  const people = pluralize(market.participants, 'участник', 'участника', 'участников');
  switch (market.status) {
    case 'open':
      return `Идёт приём · ${people} · ${ends}`;
    case 'closed':
      return `Приём прогнозов закрыт · ${people}`;
    case 'pending':
      return `Итог на проверке · ${people}`;
    case 'disputed':
      return `Итог оспорен · ${people}`;
    case 'resolved':
      return `Завершён · итог ${outcomeLabel(market.outcome)}`;
    case 'annulled':
      return 'Рынок отменён, монеты вернулись';
  }
}

// ---- Position ----

const num = (v: string | number | null | undefined): number => {
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
};

/** Sides with shares held, ДА first. */
export function heldSides(position: Position | null | undefined): Side[] {
  if (!position) return [];
  const sides: Side[] = [];
  if (num(position.yes_shares) > 0) sides.push('yes');
  if (num(position.no_shares) > 0) sides.push('no');
  return sides;
}

export const sharesOf = (position: Position, side: Side): string =>
  side === 'yes' ? position.yes_shares : position.no_shares;

export interface PositionStats {
  spent: number;
  value: number;
  /** value − spent */
  diff: number;
  payoutYes: number;
  payoutNo: number;
  sides: Side[];
  /** The side the position leans to (more shares). */
  main: Side | null;
  /** Payout if the main side wins. */
  payout: number;
}

export function positionStats(position: Position): PositionStats {
  const spent = num(position.spent);
  const value = num(position.value);
  const payoutYes = num(position.payout_if_yes);
  const payoutNo = num(position.payout_if_no);
  const sides = heldSides(position);
  const main: Side | null = sides.length === 0 ? null : num(position.yes_shares) >= num(position.no_shares) ? 'yes' : 'no';
  return {
    spent,
    value,
    diff: value - spent,
    payoutYes,
    payoutNo,
    sides,
    main,
    payout: main === 'yes' ? payoutYes : main === 'no' ? payoutNo : 0,
  };
}

/** Totals for the summary of «Позиции»: invested, value now, payout if every main side wins. */
export function positionsTotals(positions: Position[]): { spent: number; value: number; payout: number } {
  return positions.reduce(
    (acc, p) => {
      const s = positionStats(p);
      return { spent: acc.spent + s.spent, value: acc.value + s.value, payout: acc.payout + s.payout };
    },
    { spent: 0, value: 0, payout: 0 },
  );
}

/** What a finished market paid for a position: payout and profit against what was invested. */
export function settledResult(position: Position, outcome: Side): { payout: number; profit: number } {
  const payout = num(outcome === 'yes' ? position.payout_if_yes : position.payout_if_no);
  return { payout, profit: payout - num(position.spent) };
}

const floorCent = (v: number) => Math.floor(v * 100 + 1e-9) / 100;
const ceilCent = (v: number) => Math.ceil(v * 100 - 1e-9) / 100;

/**
 * Estimated proceeds of selling `shares` of `side` back to an LMSR market
 * (the same formula as market/services.py: gross rounded down, fee rounded up).
 * Only for the optimistic balance; the server's number replaces it.
 */
export function estimateSellProceeds(shares: number, side: Side, probYes: number, b: number, feeRate: number): number {
  if (!(shares > 0) || !(b > 0)) return 0;
  const p = clamp01(side === 'yes' ? probYes : 1 - probYes);
  const gross = floorCent(-b * Math.log(p * Math.exp(-shares / b) + (1 - p)));
  if (!(gross > 0)) return 0;
  const fee = Math.min(ceilCent(gross * feeRate), gross);
  return Math.max(0, Math.round((gross - fee) * 100) / 100);
}

/**
 * Position right after selling a whole side, before the server answers: the
 * side's shares and payout drop to zero. Null when nothing is left.
 */
export function positionAfterSell(position: Position, side: Side, proceeds: number): Position | null {
  const left = heldSides(position).filter((s) => s !== side);
  if (left.length === 0) return null;
  const valueLeft = Math.max(0, num(position.value) - proceeds);
  return side === 'yes'
    ? { ...position, yes_shares: '0.000000', payout_if_yes: '0.00', value: valueLeft.toFixed(2) }
    : { ...position, no_shares: '0.000000', payout_if_no: '0.00', value: valueLeft.toFixed(2) };
}

/** Selling is possible only while the market takes predictions. */
export function canSell(market: { status: MarketStatus; closes_at: string }, now: Date = new Date()): boolean {
  return market.status === 'open' && new Date(market.closes_at).getTime() > now.getTime();
}

// ---- Telegram links ----

/** Bot username from the referral link `https://t.me/<bot>/<app>?startapp=ref_…`. */
export function botUsername(referralLink: string | null | undefined): string | null {
  const m = /^https?:\/\/t\.me\/([A-Za-z0-9_]+)(?:[/?]|$)/.exec(referralLink ?? '');
  return m ? m[1] : null;
}

/** Chat with the bot; with `start`, the bot opens that dialog (e.g. `dispute_<id>`). */
export function botChatLink(referralLink: string | null | undefined, start?: string): string | null {
  const bot = botUsername(referralLink);
  if (!bot) return null;
  return start ? `https://t.me/${bot}?start=${encodeURIComponent(start)}` : `https://t.me/${bot}`;
}

/** Telegram share dialog with the invite link to the app and the question as text. */
export function shareLink(referralLink: string, text: string): string {
  return `https://t.me/share/url?url=${encodeURIComponent(referralLink)}&text=${encodeURIComponent(text)}`;
}

// ---- Misc ----

/** Initials for an avatar placeholder: «Аня К.» -> «АК». */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return parts
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join('');
}

/** «Приём прогнозов до 31 декабря · итог 2 января». */
export function datesLine(closesAt: string, resolvesAt: string, now: Date = new Date()): string {
  return `Приём прогнозов до ${formatDayMonth(closesAt, now)} · итог ${formatDayMonth(resolvesAt, now)}`;
}

/** «ДА · 152 акции». */
export function sharesLabel(shares: string | number): string {
  const n = Math.floor(num(shares));
  return pluralize(n, 'акция', 'акции', 'акций');
}
