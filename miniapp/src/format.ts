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

/** Compact amount for volumes: 950 -> "950", 52600 -> "52,6 тыс.", 1250000 -> "1,3 млн". */
export function formatCompact(value: number | string): string {
  const num = typeof value === 'string' ? Number(value) : value;
  if (!Number.isFinite(num)) return '0';
  const abs = Math.abs(num);
  if (abs >= 1_000_000) return formatMoney(num / 1_000_000, 1) + NBSP + 'млн';
  if (abs >= 1_000) return formatMoney(num / 1_000, 1) + NBSP + 'тыс.';
  return formatMoney(num, 0);
}

const MONTHS_GENITIVE = [
  'января',
  'февраля',
  'марта',
  'апреля',
  'мая',
  'июня',
  'июля',
  'августа',
  'сентября',
  'октября',
  'ноября',
  'декабря',
];

/** "1 декабря", with the year when it differs from `now`: "30 июня 2027". */
export function formatDayMonth(iso: string, now: Date = new Date()): string {
  const date = new Date(iso);
  const base = `${date.getDate()} ${MONTHS_GENITIVE[date.getMonth()]}`;
  return date.getFullYear() === now.getFullYear() ? base : `${base} ${date.getFullYear()}`;
}

/** Since when, as in the profile mockup: "с октября 2026". */
export function formatSince(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return `с ${MONTHS_GENITIVE[date.getMonth()]} ${date.getFullYear()}`;
}

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * When a market stops taking predictions, as in the feed mockup:
 * "ещё 12 дней" within two months, otherwise "до 31 декабря".
 */
export function formatEnds(closesAt: string, now: Date = new Date()): string {
  const left = new Date(closesAt).getTime() - now.getTime();
  if (left <= 0) return 'приём закрыт';
  const days = Math.floor(left / DAY_MS);
  if (days === 0) return 'меньше суток';
  if (days <= 60) return 'ещё ' + pluralize(days, 'день', 'дня', 'дней');
  return 'до ' + formatDayMonth(closesAt, now);
}
