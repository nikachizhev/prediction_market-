// Pure logic of the market creation wizard (docs/mockups/Create.dc.html, spec E3).
// The server re-checks everything; these checks only give feedback before the request.
import type { MarketCard } from './api';
import { formatDayMonth } from './format';

export type CreateStep = 1 | 2 | 3;

export interface CreateDraft {
  step: CreateStep;
  title: string;
  criteria: string;
  category: string;
  /** "YYYY-MM-DD" in the user's local time. */
  closeDate: string;
  /** "HH:MM" in the user's local time. */
  closeTime: string;
  /** Local date and time when the result becomes known. */
  resultDate: string;
  resultTime: string;
}

export type CreateField = 'title' | 'criteria' | 'category' | 'closes' | 'resolves';

export const TITLE_MAX = 120;
/** Below these lengths «Далее» stays disabled, as in the mockup. */
export const TITLE_MIN = 10;
export const CRITERIA_MIN = 15;

const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;
const MIN_OPEN_MS = HOUR_MS;
const MAX_OPEN_MS = 365 * DAY_MS;
const RESULT_TIME = '23:59';
const DEFAULT_OPEN_DAYS = 7;

const pad = (n: number) => String(n).padStart(2, '0');

/** Local calendar date for <input type="date">. */
export function dateInputValue(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** A fresh draft: predictions accepted for a week, the result known the next day. */
export function defaultDraft(now: Date = new Date()): CreateDraft {
  const close = new Date(now.getTime() + DEFAULT_OPEN_DAYS * DAY_MS);
  const result = new Date(close.getFullYear(), close.getMonth(), close.getDate() + 1);
  return {
    step: 1,
    title: '',
    criteria: '',
    category: '',
    closeDate: dateInputValue(close),
    closeTime: RESULT_TIME,
    resultDate: dateInputValue(result),
    resultTime: RESULT_TIME,
  };
}

/**
 * Local date and time as ISO 8601 with the user's UTC offset:
 * ("2026-11-15", "23:59") -> "2026-11-15T23:59:00+03:00". Null for an invalid
 * or non-existent (DST gap) moment.
 */
export function localIso(date: string, time: string): string | null {
  const d = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  const t = /^(\d{2}):(\d{2})$/.exec(time);
  if (!d || !t) return null;
  const [y, m, day, hh, mm] = [d[1], d[2], d[3], t[1], t[2]].map(Number);
  const value = new Date(y, m - 1, day, hh, mm);
  if (
    Number.isNaN(value.getTime()) ||
    value.getFullYear() !== y ||
    value.getMonth() !== m - 1 ||
    value.getDate() !== day ||
    value.getHours() !== hh ||
    value.getMinutes() !== mm
  ) {
    return null;
  }
  const offset = -value.getTimezoneOffset();
  const sign = offset >= 0 ? '+' : '-';
  const abs = Math.abs(offset);
  return `${date}T${time}:00${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

export function closesAtIso(draft: CreateDraft): string | null {
  return localIso(draft.closeDate, draft.closeTime);
}

export function resolvesAtIso(draft: CreateDraft): string | null {
  return localIso(draft.resultDate, draft.resultTime);
}

/** Problems with the dates, worded like the server's errors. */
export function dateErrors(draft: CreateDraft, now: Date = new Date()): Partial<Record<'closes' | 'resolves', string>> {
  const errors: Partial<Record<'closes' | 'resolves', string>> = {};
  const closes = closesAtIso(draft);
  const resolves = resolvesAtIso(draft);
  if (!closes) errors.closes = 'Укажите дату и время.';
  if (!resolves) errors.resolves = 'Укажите дату и время итога.';
  if (closes) {
    const left = Date.parse(closes) - now.getTime();
    if (left < MIN_OPEN_MS) errors.closes = 'Приём прогнозов — хотя бы час.';
    else if (left > MAX_OPEN_MS) errors.closes = 'Приём прогнозов — не дольше года.';
  }
  if (closes && resolves) {
    const gap = Date.parse(resolves) - Date.parse(closes);
    if (gap < 0) errors.resolves = 'Итог не может стать известен раньше конца приёма прогнозов.';
    else if (gap > MAX_OPEN_MS) errors.resolves = 'Итог должен стать известен не позже чем через год после конца приёма.';
  }
  return errors;
}

/** Whether the step is filled in enough to go on (and, on step 2, the dates are valid). */
export function canProceed(draft: CreateDraft, step: CreateStep, now: Date = new Date()): boolean {
  const title = draft.title.trim();
  const titleOk = title.length >= TITLE_MIN && title.length <= TITLE_MAX;
  if (step === 1) return titleOk;
  const rulesOk =
    draft.criteria.trim().length >= CRITERIA_MIN &&
    draft.category !== '' &&
    Object.keys(dateErrors(draft, now)).length === 0;
  return titleOk && rulesOk;
}

/** Body of POST /api/markets, or null while the draft is incomplete. */
export function createPayload(draft: CreateDraft) {
  const closes_at = closesAtIso(draft);
  const resolves_at = resolvesAtIso(draft);
  if (!closes_at || !resolves_at) return null;
  return {
    title: draft.title.trim(),
    criteria: draft.criteria.trim(),
    category: draft.category,
    closes_at,
    resolves_at,
  };
}

/** Which field a server validation error belongs to; null means "show as a toast". */
export function errorField(message: string): CreateField | null {
  if (/вопрос/i.test(message)) return 'title';
  if (/определяем результат/i.test(message)) return 'criteria';
  if (/категори/i.test(message)) return 'category';
  if (/^Итог/.test(message)) return 'resolves';
  if (/при[её]м прогнозов|принимаем прогнозы|часовым поясом/i.test(message)) return 'closes';
  return null;
}

export function stepOfField(field: CreateField): CreateStep {
  return field === 'title' ? 1 : 2;
}

/** The card exactly as the feed will show a brand-new market: 50%, no history yet. */
export function previewCard(draft: CreateDraft): MarketCard {
  return {
    id: 0,
    title: draft.title.trim() || 'Твой вопрос появится здесь',
    category: draft.category,
    prob: 0.5,
    change_24h: 0,
    spark: [0.5, 0.5],
    participants: 0,
    volume: '0.00',
    closes_at: closesAtIso(draft) ?? new Date().toISOString(),
    status: 'open',
    outcome: null,
    starred: false,
  };
}

/** Balance after paying for the market, or null if it is not enough. */
export function balanceAfterCreation(balance: number | string, cost: number | string): number | null {
  const rest = Math.round((Number(balance) - Number(cost)) * 100) / 100;
  return rest >= 0 ? rest : null;
}

// ---- Draft storage: survives switching tabs (memory) and reloads (sessionStorage). ----

const STORAGE_KEY = 'create-draft';
let memoryDraft: CreateDraft | null = null;

function isDraft(value: unknown): value is CreateDraft {
  if (!value || typeof value !== 'object') return false;
  const d = value as Record<string, unknown>;
  return (
    (d.step === 1 || d.step === 2 || d.step === 3) &&
    ['title', 'criteria', 'category', 'closeDate', 'closeTime', 'resultDate'].every((k) => typeof d[k] === 'string')
  );
}

export function loadDraft(now: Date = new Date()): CreateDraft {
  if (memoryDraft) return memoryDraft;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (isDraft(parsed)) return (memoryDraft = { ...parsed, resultTime: typeof parsed.resultTime === 'string' ? parsed.resultTime : RESULT_TIME });
  } catch {
    // Storage may be unavailable (private mode, WebView restrictions).
  }
  return defaultDraft(now);
}

export function saveDraft(draft: CreateDraft): void {
  memoryDraft = draft;
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(draft));
  } catch {
    // Memory copy is enough for switching tabs.
  }
}

export function clearDraft(): void {
  memoryDraft = null;
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing to clear.
  }
}

/** «16 ноября бот спросит, чем закончилось…» for the review step; the date is the result date. */
export function resolutionRule(draft: CreateDraft, now: Date = new Date()): string {
  const resolves = resolvesAtIso(draft);
  const when = resolves ? `${formatDayMonth(resolves, now)} в ${draft.resultTime}` : 'В дату итога';
  return `${when} бот спросит, чем закончилось: нажмёшь ДА или НЕТ и пришлёшь фото или скриншот`;
}
