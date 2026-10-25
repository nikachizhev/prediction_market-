// Russian number and word formatting.

const NBSP = ' ';

/**
 * Formats an amount: thousands separated by a non-breaking space, comma as the
 * decimal mark, trailing zeros dropped (1234.5 -> "1 234,5", "100.00" -> "100").
 */
export function formatMoney(value: number | string, maxFractionDigits = 2): string {
  const num = typeof value === 'string' ? Number(value) : value;
  if (!Number.isFinite(num)) return '0';
  const factor = 10 ** maxFractionDigits;
  const rounded = Math.round(Math.abs(num) * factor) / factor;
  const [intPart, fracPart = ''] = rounded.toFixed(maxFractionDigits).split('.');
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
  const frac = fracPart.replace(/0+$/, '');
  const sign = num < 0 && rounded !== 0 ? '−' : '';
  return sign + grouped + (frac ? ',' + frac : '');
}

/** Same as formatMoney but always shows the sign: "+20", "−5,5". */
export function formatSigned(value: number | string, maxFractionDigits = 2): string {
  const num = typeof value === 'string' ? Number(value) : value;
  const body = formatMoney(num, maxFractionDigits);
  return num > 0 && body !== '0' ? '+' + body : body;
}

/** Probability 0..1 as a whole percent: 0.634 -> "63%". */
export function formatPercent(prob: number): string {
  if (!Number.isFinite(prob)) return '0%';
  return Math.round(prob * 100) + '%';
}

/** Change in percentage points with sign: 4.2 -> "+4 п.п.". */
export function formatPoints(points: number): string {
  const n = Math.round(points);
  if (n === 0) return '0 п.п.';
  return (n > 0 ? '+' : '−') + Math.abs(n) + ' п.п.';
}

/** Russian plural: plural(5, 'участник', 'участника', 'участников') -> 'участников'. */
export function plural(n: number, one: string, few: string, many: string): string {
  const abs = Math.abs(Math.trunc(n));
  const mod10 = abs % 10;
  const mod100 = abs % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

/** Number with its word: pluralize(3, 'рынок', 'рынка', 'рынков') -> '3 рынка'. */
export function pluralize(n: number, one: string, few: string, many: string): string {
  return `${formatMoney(n, 0)} ${plural(n, one, few, many)}`;
}
