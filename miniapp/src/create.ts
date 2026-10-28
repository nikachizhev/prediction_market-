// Pure logic of the market creation wizard (docs/mockups/Create.dc.html, spec E3) with
// templates, the AI rules helper and similar markets (docs/mockups/quick-create, spec E9.1–E9.3).
// The server re-checks everything; these checks only give feedback before the request.
import type {
  AssistOutcome,
  AssistResult,
  AssistState,
  CreateMarketData,
  MarketCard,
  MarketTemplate,
  TemplateBlank,
} from './api';
import { formatDayMonth, formatPercent, plural, pluralize } from './format';

export type CreateStep = 1 | 2 | 3;

/** Field groups the helper or a template fills; each shows an «ИИ» / «Шаблон» mark until edited. */
export type FilledGroup = 'dates' | 'criteria' | 'category';
export type FillSource = 'ai' | 'template';

const ALL_GROUPS: FilledGroup[] = ['dates', 'criteria', 'category'];

/** Fields as they were before the helper's suggestion, for «Вернуть как было». */
export interface FieldsSnapshot {
  criteria: string;
  category: string;
  closeDate: string;
  closeTime: string;
  resultDate: string;
  resultTime: string;
  marks: FilledGroup[];
  filledBy: FillSource | null;
}

/** The helper's clearer wording of the question («Точнее так»): offered, never put in by itself. */
export interface QuestionSuggestion {
  question: string;
  why: string;
  /** open — offered; accepted — «Заменить»; dismissed — «Оставить мой» or «Вернуть мой». */
  state: 'open' | 'accepted' | 'dismissed';
  /** The creator's own question, for «Вернуть мой». */
  own: string;
}

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
  /** Groups filled by the helper or a template and not edited since. */
  marks: FilledGroup[];
  /** Who filled the marked groups. */
  filledBy: FillSource | null;
  /** Applied template, sent as `template`. */
  templateId: string | null;
  /** Fields before the helper's suggestion; null when there is nothing to undo. */
  before: FieldsSnapshot | null;
  /** What became of the latest suggestion, sent as `assist`; null when the helper was not used. */
  assist: AssistOutcome | null;
  suggestion: QuestionSuggestion | null;
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
    marks: [],
    filledBy: null,
    templateId: null,
    before: null,
    assist: null,
    suggestion: null,
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

/** ISO moment -> local "YYYY-MM-DD" and "HH:MM"; null when it does not parse. */
export function localParts(iso: string): { date: string; time: string } | null {
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return null;
  return { date: dateInputValue(value), time: `${pad(value.getHours())}:${pad(value.getMinutes())}` };
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

/** Body of POST /api/markets, or null while the draft is incomplete; `assist` and `template` only when used. */
export function createPayload(draft: CreateDraft): CreateMarketData | null {
  const closes_at = closesAtIso(draft);
  const resolves_at = resolvesAtIso(draft);
  if (!closes_at || !resolves_at) return null;
  return {
    title: draft.title.trim(),
    criteria: draft.criteria.trim(),
    category: draft.category,
    closes_at,
    resolves_at,
    ...(draft.assist ? { assist: draft.assist } : {}),
    ...(draft.templateId ? { template: draft.templateId } : {}),
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

/** Fields whose server errors go stale after the change from `prev` to `next`. */
export function changedFields(prev: CreateDraft, next: CreateDraft): CreateField[] {
  const fields = new Set<CreateField>();
  if (prev.title !== next.title) fields.add('title');
  if (prev.criteria !== next.criteria) fields.add('criteria');
  if (prev.category !== next.category) fields.add('category');
  if (prev.closeDate !== next.closeDate || prev.closeTime !== next.closeTime) {
    fields.add('closes');
    fields.add('resolves');
  }
  if (prev.resultDate !== next.resultDate || prev.resultTime !== next.resultTime) fields.add('resolves');
  return [...fields];
}

// ---- Edits by the creator: marks go away, the helper's outcome becomes «edited» ----

export type DraftEdit = Partial<
  Pick<CreateDraft, 'step' | 'title' | 'criteria' | 'category' | 'closeDate' | 'closeTime' | 'resultDate' | 'resultTime'>
>;

const GROUP_OF: Partial<Record<keyof DraftEdit, FilledGroup>> = {
  closeDate: 'dates',
  closeTime: 'dates',
  resultDate: 'dates',
  resultTime: 'dates',
  criteria: 'criteria',
  category: 'category',
};

/** The creator changes fields by hand. Re-entering the same value changes nothing. */
export function editDraft(draft: CreateDraft, patch: DraftEdit): CreateDraft {
  const changed = (Object.keys(patch) as (keyof DraftEdit)[]).filter(
    (k) => patch[k] !== undefined && patch[k] !== draft[k],
  );
  if (changed.length === 0) return draft;
  const next: CreateDraft = { ...draft, ...patch };
  const groups = new Set(changed.map((k) => GROUP_OF[k]).filter((g): g is FilledGroup => !!g));
  if (groups.size > 0) {
    next.marks = draft.marks.filter((g) => !groups.has(g));
    // The helper fills every group, so a change to any of them corrects its suggestion.
    if (draft.assist === 'accepted') next.assist = 'edited';
  }
  if (changed.includes('title')) {
    // A new question needs a new suggestion: the helper card starts over.
    next.before = null;
    next.suggestion = null;
  }
  return next;
}

// ---- AI rules helper (spec E9.1) ----

function snapshot(draft: CreateDraft): FieldsSnapshot {
  return {
    criteria: draft.criteria,
    category: draft.category,
    closeDate: draft.closeDate,
    closeTime: draft.closeTime,
    resultDate: draft.resultDate,
    resultTime: draft.resultTime,
    marks: draft.marks,
    filledBy: draft.filledBy,
  };
}

/**
 * Fills rules, category and both dates with times from the helper's answer, in the user's local
 * time; the clearer question is only offered. «Вернуть как было» restores what is replaced here.
 */
export function applyAssist(draft: CreateDraft, result: AssistResult): CreateDraft {
  const closes = localParts(result.closes_at);
  const resolves = localParts(result.resolves_at);
  const question = (result.question ?? '').trim();
  const offer = question.length >= TITLE_MIN && question.length <= TITLE_MAX && question !== draft.title.trim();
  return {
    ...draft,
    criteria: result.criteria,
    category: result.category,
    ...(closes && resolves
      ? { closeDate: closes.date, closeTime: closes.time, resultDate: resolves.date, resultTime: resolves.time }
      : {}),
    marks: closes && resolves ? ALL_GROUPS : ['criteria', 'category'],
    filledBy: 'ai',
    before: snapshot(draft),
    assist: 'accepted',
    suggestion: offer ? { question, why: (result.why ?? '').trim(), state: 'open', own: draft.title } : null,
  };
}

/** «Вернуть как было»: every field the helper filled, and the own question if it was replaced. */
export function undoAssist(draft: CreateDraft): CreateDraft {
  const before = draft.before;
  if (!before) return draft;
  const { marks, filledBy, ...fields } = before;
  return {
    ...draft,
    ...fields,
    marks,
    filledBy,
    title: draft.suggestion?.state === 'accepted' ? draft.suggestion.own : draft.title,
    before: null,
    suggestion: null,
    assist: 'undone',
  };
}

/** «Заменить»: the suggested question goes into the question field. */
export function acceptSuggestion(draft: CreateDraft): CreateDraft {
  const s = draft.suggestion;
  if (!s || s.state !== 'open') return draft;
  return { ...draft, title: s.question, suggestion: { ...s, state: 'accepted', own: draft.title } };
}

/** «Оставить мой». */
export function dismissSuggestion(draft: CreateDraft): CreateDraft {
  const s = draft.suggestion;
  if (!s || s.state !== 'open') return draft;
  return { ...draft, suggestion: { ...s, state: 'dismissed' } };
}

/** «Вернуть мой» after «Заменить». */
export function revertSuggestion(draft: CreateDraft): CreateDraft {
  const s = draft.suggestion;
  if (!s || s.state !== 'accepted') return draft;
  return { ...draft, title: s.own, suggestion: { ...s, state: 'dismissed' } };
}

/** Helper card state that lives only on the screen, not in the draft. */
export type AssistUi = 'idle' | 'loading' | 'warn' | 'off' | 'limit' | 'manual';
/** What the helper card on step 2 shows; `none` — no card. */
export type AssistCard = AssistUi | 'done' | 'template' | 'none';

/** Shown in «Их 20 в день»; the server's ASSIST_DAILY_LIMIT. */
export const ASSIST_DAILY_LIMIT = 20;

export function assistCard(draft: CreateDraft, ui: AssistUi, assist: AssistState | undefined): AssistCard {
  const fromTemplate = draft.filledBy === 'template' && draft.templateId !== null;
  if (!assist?.enabled) return fromTemplate ? 'template' : 'none';
  if (ui === 'loading') return 'loading';
  if (fromTemplate) return 'template';
  if (draft.before && draft.filledBy === 'ai') return 'done';
  if (ui !== 'idle') return ui;
  return assist.left > 0 ? 'idle' : 'limit';
}

/** «Осталось 19 подсказок на сегодня». */
export function assistLeftLabel(left: number): string {
  return `Осталось ${left} ${plural(left, 'подсказка', 'подсказки', 'подсказок')} на сегодня`;
}

// ---- Templates (spec E9.2): blanks, rules and dates from the server's catalog ----

export const TEMPLATE_TIME = '23:59';

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Calendar arithmetic on "YYYY-MM-DD", independent of the time zone. */
export function shiftDate(date: string, days: number): string {
  const m = ISO_DATE.exec(date);
  if (!m) return date;
  return new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + days)).toISOString().slice(0, 10);
}

/** "2026-11-16" -> «16 ноября» (with the year when it is not this one). */
export function dayMonthOf(date: string, now: Date = new Date()): string {
  const m = ISO_DATE.exec(date);
  if (!m) return date;
  return formatDayMonth(new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).toISOString(), now);
}

/** Puts the values into `{key}`; an empty blank shows its hint («[предмет]»), a date reads «16 ноября». */
export function fillTemplate(
  text: string,
  blanks: TemplateBlank[],
  values: Record<string, string>,
  now: Date = new Date(),
): string {
  const byKey = new Map(blanks.map((b) => [b.key, b]));
  return text.replace(/\{(\w+)\}/g, (whole, key: string) => {
    const blank = byKey.get(key);
    const value = (values[key] ?? '').trim();
    if (!value) return blank ? blank.hint : whole;
    return blank?.type === 'date' ? dayMonthOf(value, now) : value;
  });
}

/** «Подставить в вопрос» is available once every blank is filled (dates must be real dates). */
export function templateFilled(template: MarketTemplate, values: Record<string, string>): boolean {
  return template.blanks.every((b) => {
    const value = (values[b.key] ?? '').trim();
    return b.type === 'date' ? localIso(value, TEMPLATE_TIME) !== null : value !== '';
  });
}

/** Template categories in the server's order. */
export function templateCategories(templates: MarketTemplate[]): string[] {
  return [...new Set(templates.map((t) => t.category))];
}

/** Earliest day for a template's date: tomorrow, so that predictions are accepted until tonight. */
export function templateMinDate(now: Date = new Date()): string {
  return dateInputValue(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1));
}

/**
 * Question (cut to 120), rules and category from the template; predictions until 23:59 the day
 * before {date}, the result at 23:59 the day after it.
 */
export function applyTemplate(
  draft: CreateDraft,
  template: MarketTemplate,
  values: Record<string, string>,
  now: Date = new Date(),
): CreateDraft {
  const dateBlank = template.blanks.find((b) => b.type === 'date');
  const date = dateBlank ? (values[dateBlank.key] ?? '').trim() : '';
  const dated = ISO_DATE.test(date);
  return {
    ...draft,
    title: fillTemplate(template.text, template.blanks, values, now).slice(0, TITLE_MAX),
    criteria: fillTemplate(template.criteria, template.blanks, values, now),
    category: template.category,
    ...(dated
      ? { closeDate: shiftDate(date, -1), closeTime: TEMPLATE_TIME, resultDate: shiftDate(date, 1), resultTime: TEMPLATE_TIME }
      : {}),
    marks: dated ? ALL_GROUPS : ['criteria', 'category'],
    filledBy: 'template',
    templateId: template.id,
    before: null,
    suggestion: null,
    // The template replaced what the helper had filled in.
    assist: draft.assist === 'accepted' ? 'edited' : draft.assist,
  };
}

// ---- Similar markets (spec E9.3) ----

/** «41% ДА · 23 участника». */
export function similarLine(card: MarketCard): string {
  return `${formatPercent(card.prob)} ДА · ${pluralize(card.participants, 'участник', 'участника', 'участников')}`;
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

const isText = (v: unknown): v is string => typeof v === 'string';
const isRecord = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object';

function readMarks(value: unknown): FilledGroup[] {
  return Array.isArray(value) ? ALL_GROUPS.filter((g) => value.includes(g)) : [];
}

function readSource(value: unknown): FillSource | null {
  return value === 'ai' || value === 'template' ? value : null;
}

function readSnapshot(value: unknown): FieldsSnapshot | null {
  if (!isRecord(value)) return null;
  const { criteria, category, closeDate, closeTime, resultDate, resultTime } = value;
  if (!isText(criteria) || !isText(category) || !isText(closeDate) || !isText(closeTime)) return null;
  if (!isText(resultDate) || !isText(resultTime)) return null;
  return {
    criteria,
    category,
    closeDate,
    closeTime,
    resultDate,
    resultTime,
    marks: readMarks(value.marks),
    filledBy: readSource(value.filledBy),
  };
}

function readSuggestion(value: unknown): QuestionSuggestion | null {
  if (!isRecord(value) || !isText(value.question) || !isText(value.own)) return null;
  const state = value.state === 'accepted' || value.state === 'dismissed' ? value.state : 'open';
  return { question: value.question, why: isText(value.why) ? value.why : '', state, own: value.own };
}

/** A stored draft; one saved before templates and the helper gets the new fields empty. */
export function readDraft(value: unknown): CreateDraft | null {
  if (!isRecord(value)) return null;
  const { step, title, criteria, category, closeDate, closeTime, resultDate, resultTime, assist, templateId } = value;
  if (step !== 1 && step !== 2 && step !== 3) return null;
  if (!isText(title) || !isText(criteria) || !isText(category)) return null;
  if (!isText(closeDate) || !isText(closeTime) || !isText(resultDate)) return null;
  return {
    step,
    title,
    criteria,
    category,
    closeDate,
    closeTime,
    resultDate,
    resultTime: isText(resultTime) ? resultTime : RESULT_TIME,
    marks: readMarks(value.marks),
    filledBy: readSource(value.filledBy),
    templateId: isText(templateId) && templateId !== '' ? templateId : null,
    before: readSnapshot(value.before),
    assist: assist === 'accepted' || assist === 'edited' || assist === 'undone' ? assist : null,
    suggestion: readSuggestion(value.suggestion),
  };
}

export function loadDraft(now: Date = new Date()): CreateDraft {
  if (memoryDraft) return memoryDraft;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    const parsed = readDraft(raw ? JSON.parse(raw) : null);
    if (parsed) return (memoryDraft = parsed);
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
