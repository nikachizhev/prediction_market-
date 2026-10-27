// Pure helpers of the profile screen (docs/mockups/Profile.dc.html).
import type { BestPrediction, CalibrationBucket, Money } from './api';
import { formatDayMonth, formatSigned, pluralize } from './format';

const DAY_MS = 24 * 60 * 60 * 1000;

export interface BalancePoint {
  /** Milliseconds since epoch. */
  t: number;
  v: number;
}

/**
 * Balance over the last `days` days from GET /api/profile `balance_history`
 * (balance after each operation). Starts with the balance carried into the
 * window and ends with the current balance at `now`.
 */
export function balanceSeries(
  history: { t: string; balance: Money }[],
  current: number,
  now: Date = new Date(),
  days = 30,
): BalancePoint[] {
  const end = now.getTime();
  const start = end - days * DAY_MS;
  const all = history
    .map((h) => ({ t: Date.parse(h.t), v: Number(h.balance) }))
    .filter((p) => Number.isFinite(p.t) && Number.isFinite(p.v) && p.t <= end)
    .sort((a, b) => a.t - b.t);
  const before = all.filter((p) => p.t < start);
  const inside = all.filter((p) => p.t >= start);
  const points: BalancePoint[] = [];
  if (before.length > 0) points.push({ t: start, v: before[before.length - 1].v });
  else if (inside.length === 0) points.push({ t: start, v: current });
  points.push(...inside);
  points.push({ t: end, v: current });
  return points;
}

/** Round step for axis ticks: 1, 2, 5 × 10^n. */
export function niceStep(raw: number): number {
  if (!(raw > 0)) return 1;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const m = raw / pow;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * pow;
}

export interface ChartBox {
  /** Width of the plot (labels go to the right of it). */
  width: number;
  top: number;
  bottom: number;
  start: number;
  end: number;
}

export interface BalanceChart {
  path: string;
  points: { x: number; y: number; t: number; v: number }[];
  /** Three horizontal grid lines with round values. */
  ticks: { y: number; value: number }[];
}

/** SVG path and grid for the balance chart. */
export function balanceChart(series: BalancePoint[], box: ChartBox): BalanceChart {
  const values = series.map((p) => p.v);
  const lo = values.length ? Math.min(...values) : 0;
  const hi = values.length ? Math.max(...values) : 0;
  let step = niceStep(Math.max(hi - lo, 10) / 2);
  let floor = Math.floor(lo / step) * step;
  while (floor + 2 * step < hi) {
    step = niceStep(step * 1.5);
    floor = Math.floor(lo / step) * step;
  }
  const top = floor + 2 * step;
  const span = box.end - box.start || 1;
  const x = (t: number) => ((t - box.start) / span) * box.width;
  const y = (v: number) => box.bottom - ((v - floor) / (top - floor)) * (box.bottom - box.top);
  const points = series.map((p) => ({ x: round1(x(p.t)), y: round1(y(p.v)), t: p.t, v: p.v }));
  const path = points.length ? 'M' + points.map((p) => `${p.x} ${p.y}`).join(' L') : '';
  const ticks = [0, 1, 2].map((i) => ({ y: round1(y(floor + i * step)), value: floor + i * step }));
  return { path, points, ticks };
}

const round1 = (n: number) => Math.round(n * 10) / 10;

/** Index of the point closest to `x` (pointer scrubbing). */
export function nearestPoint(points: { x: number }[], x: number): number {
  let best = 0;
  for (let i = 1; i < points.length; i += 1) {
    if (Math.abs(points[i].x - x) < Math.abs(points[best].x - x)) best = i;
  }
  return best;
}

/** "сегодня" or "3 дн. назад", as in the mockup. */
export function daysAgo(t: number, now: Date = new Date()): string {
  const days = Math.floor((now.getTime() - t) / DAY_MS);
  return days <= 0 ? 'сегодня' : `${days} дн. назад`;
}

/** "+240 за 30 дней"; a younger account gets its real span ("−100 за сегодня"). */
export function balanceChangeLabel(series: BalancePoint[]): string {
  if (series.length === 0) return '';
  const first = series[0];
  const last = series[series.length - 1];
  const delta = formatSigned(last.v - first.v, 0);
  const days = Math.round((last.t - first.t) / DAY_MS);
  return days < 1 ? `${delta} за сегодня` : `${delta} за ${pluralize(days, 'день', 'дня', 'дней')}`;
}

export type Tone = 'plus' | 'minus' | 'zero';

/** Colour of an operation amount: income, spending or nothing. */
export function amountTone(amount: Money | number): Tone {
  const num = Number(amount);
  if (!Number.isFinite(num) || Math.abs(num) < 0.005) return 'zero';
  return num > 0 ? 'plus' : 'minus';
}

/** "25 октября, 14:05". */
export function formatLedgerDate(iso: string, now: Date = new Date()): string {
  const date = new Date(iso);
  const time = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
  return `${formatDayMonth(iso, now)}, ${time}`;
}

/** Plot area of the calibration chart, as in the mockup (358×232). */
export const CALIBRATION_BOX = { left: 36, right: 340, top: 10, bottom: 190 };

export interface CalibrationPoint {
  x: number;
  y: number;
  price: number;
  count: number;
  hits: number;
  /** Share of buys that came true, 0..1. */
  rate: number;
}

/** Points of «Насколько точны твои вероятности»: price of the buy → how often it came true. */
export function calibrationPoints(buckets: CalibrationBucket[]): CalibrationPoint[] {
  const { left, right, top, bottom } = CALIBRATION_BOX;
  return buckets
    .filter((b) => b.count > 0)
    .map((b) => {
      const rate = b.hits / b.count;
      return {
        x: round1(left + (b.price / 100) * (right - left)),
        y: round1(bottom - rate * (bottom - top)),
        price: b.price,
        count: b.count,
        hits: b.hits,
        rate,
      };
    });
}

/** "Калибровка: покупки по 10% сбылись в 13%, по 30% — в 25%". */
export function calibrationLabel(points: CalibrationPoint[]): string {
  const parts = points.map((p, i) =>
    i === 0
      ? `покупки по ${p.price}% сбылись в ${Math.round(p.rate * 100)}%`
      : `по ${p.price}% — в ${Math.round(p.rate * 100)}%`,
  );
  return 'Калибровка: ' + parts.join(', ');
}

/** Tooltip lines: "Покупал по ~70%", "сбылось 6 из 9 (67%)". */
export function calibrationTip(p: CalibrationPoint): [string, string] {
  return [`Покупал по ~${p.price}%`, `сбылось ${p.hits} из ${p.count} (${Math.round(p.rate * 100)}%)`];
}

/** "Купил ДА по 12% — сбылось". */
export function bestPredictionLabel(best: BestPrediction): string {
  return `Купил ${best.side === 'yes' ? 'ДА' : 'НЕТ'} по ${Math.round(best.price * 100)}% — сбылось`;
}

export const PSEUDONYM_MIN = 2;
export const PSEUDONYM_MAX = 32;

/** Same rule as PATCH /api/me; null when the pseudonym is fine. */
export function pseudonymError(value: string): string | null {
  const len = value.trim().length;
  return len >= PSEUDONYM_MIN && len <= PSEUDONYM_MAX ? null : 'Псевдоним — от 2 до 32 символов.';
}
