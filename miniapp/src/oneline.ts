// «О чём спорим?» — a market in one line (spec E9.5) and a market from a chat message opened by
// startapp=draft_<code> (spec E9.4). Pure logic; the screen is screens/Create.tsx.
import { ApiError, isAssistWarning, type AssistResult, type AssistState, type AssistWarning, type MarketDraftView } from './api';
import {
  CRITERIA_MIN,
  dateErrors,
  defaultDraft,
  localParts,
  optionsOk,
  OPTION_MAX,
  OPTIONS_MAX,
  OPTIONS_MIN,
  TITLE_MAX,
  TITLE_MIN,
  type AssistUi,
  type CreateDraft,
  type CreateField,
} from './create';

/** «Сделать рынок» is available from 10 characters, like the helper's `title`. */
export function lineReady(line: string): boolean {
  const text = line.trim();
  return text.length >= TITLE_MIN && text.length <= TITLE_MAX;
}

/** Whether «Сделать рынок» asks the helper at all: off (server or chat) or no suggestions left — straight to the wizard. */
export function lineUsesAssist(assist: AssistState | undefined): boolean {
  return !!assist?.enabled && assist.left > 0;
}

/** Why «Сделать рынок» ended in the wizard; null — it did not. */
export type WizardReason = 'manual' | 'unavailable';

/** Body of the `oneline_wizard` event. */
export function wizardEvent(reason: WizardReason): { name: 'oneline_wizard'; props: { reason: WizardReason } } {
  return { name: 'oneline_wizard', props: { reason } };
}

/**
 * The wizard from step 1 with the line in the question field; the place (chat) and «Объявить в чате»
 * stay, everything the helper (or a chat draft) filled before is dropped.
 */
export function wizardFromLine(draft: CreateDraft, now: Date = new Date()): CreateDraft {
  return {
    ...defaultDraft(now),
    flow: 'wizard',
    step: 1,
    title: draft.line.trim().slice(0, TITLE_MAX),
    line: draft.line,
    chat: draft.chat,
    announce: draft.announce,
  };
}

/** The helper's options for «кто из…»: trimmed, unique, at most 10, each up to 80 characters; null — a ДА/НЕТ market. */
export function cleanAssistOptions(options: string[] | null | undefined): string[] | null {
  if (!Array.isArray(options)) return null;
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of options) {
    if (typeof raw !== 'string') continue;
    const option = raw.trim().slice(0, OPTION_MAX);
    const key = option.toLowerCase();
    if (!option || seen.has(key)) continue;
    seen.add(key);
    out.push(option);
  }
  return out.length >= OPTIONS_MIN ? out.slice(0, OPTIONS_MAX) : null;
}

/**
 * The ready market from the helper's answer: its question (or the line when there is no clearer one),
 * rules, category, both dates in local time and, for «кто из…», the options. Sent as `assist: "oneline"`
 * until the creator edits a field.
 */
export function onelineDraft(draft: CreateDraft, result: AssistResult, now: Date = new Date()): CreateDraft {
  const base = defaultDraft(now);
  const question = (result.question ?? '').trim();
  const title = question.length >= TITLE_MIN && question.length <= TITLE_MAX ? question : draft.line.trim().slice(0, TITLE_MAX);
  const closes = localParts(result.closes_at);
  const resolves = localParts(result.resolves_at);
  const options = cleanAssistOptions(result.options);
  return {
    ...base,
    flow: 'quick',
    step: 1,
    line: draft.line,
    chat: draft.chat,
    announce: draft.announce,
    title,
    criteria: result.criteria ?? '',
    category: result.category ?? '',
    ...(closes && resolves
      ? { closeDate: closes.date, closeTime: closes.time, resultDate: resolves.date, resultTime: resolves.time }
      : {}),
    ...(options ? { mode: 'multiple' as const, options } : {}),
    assist: 'oneline',
  };
}

/** What «Сделать рынок» leads to. `ui` is the helper card the wizard shows; `warning` — the forbidden-topic text. */
export type LineOutcome =
  | { kind: 'quick'; draft: CreateDraft; left: number | null }
  | { kind: 'wizard'; draft: CreateDraft; ui: AssistUi; warning: string | null; left: number | null };

const leftOf = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? Math.max(0, value) : null);

/** The helper answered: the ready market, or the wizard with the forbidden-topic warning. */
export function lineAnswer(draft: CreateDraft, res: AssistResult | AssistWarning, now: Date = new Date()): LineOutcome {
  if (isAssistWarning(res)) {
    return { kind: 'wizard', draft: wizardFromLine(draft, now), ui: 'warn', warning: res.text, left: leftOf(res.left) };
  }
  return { kind: 'quick', draft: onelineDraft(draft, res, now), left: leftOf(res.left) };
}

/** The helper failed: 429 — no suggestions left today; 503, no connection or anything else — it is not answering. */
export function lineFailure(draft: CreateDraft, err: unknown, now: Date = new Date()): LineOutcome {
  const limit = err instanceof ApiError && err.status === 429;
  return { kind: 'wizard', draft: wizardFromLine(draft, now), ui: limit ? 'limit' : 'off', warning: null, left: limit ? 0 : null };
}

/**
 * The creation preview of a chat draft: an assisted one opens as the ready market, one the helper
 * did not fill (only the message text) — as the wizard with the text in the question field. The chat
 * is the draft's; the server announces such a market in the chat by itself.
 */
export function draftFromChatDraft(view: MarketDraftView, now: Date = new Date()): CreateDraft {
  const base = defaultDraft(now);
  const title = (view.title ?? '').trim().slice(0, TITLE_MAX);
  const common: CreateDraft = { ...base, line: title, title, chat: view.chat.code, draftCode: view.code };
  if (!view.assisted) return { ...common, flow: 'wizard', step: 1 };
  const closes = view.closes_at ? localParts(view.closes_at) : null;
  const resolves = view.resolves_at ? localParts(view.resolves_at) : null;
  const options = cleanAssistOptions(view.options);
  return {
    ...common,
    flow: 'quick',
    criteria: view.criteria ?? '',
    category: view.category ?? '',
    ...(closes ? { closeDate: closes.date, closeTime: closes.time } : {}),
    ...(resolves ? { resultDate: resolves.date, resultTime: resolves.time } : {}),
    ...(options ? { mode: 'multiple' as const, options } : {}),
  };
}

/** What to do with GET /api/drafts/{code}: open the market already made from it, or the preview. */
export function chatDraftTarget(view: MarketDraftView, now: Date = new Date()): { market: number } | { draft: CreateDraft } {
  if (typeof view.market_id === 'number' && view.market_id > 0) return { market: view.market_id };
  return { draft: draftFromChatDraft(view, now) };
}

/** Fields of the ready market a tap opens for editing: the same as the wizard's, so server errors land on them. */
export type QuickField = CreateField;

/**
 * What keeps «Создать» disabled on the ready market, per field, so that the row says what to fix
 * (the wizard just keeps «Далее» disabled). Same rules as canProceed.
 */
export function quickProblems(draft: CreateDraft, now: Date = new Date()): Partial<Record<QuickField, string>> {
  const problems: Partial<Record<QuickField, string>> = {};
  const title = draft.title.trim();
  if (title.length < TITLE_MIN || title.length > TITLE_MAX) problems.title = `Вопрос — от ${TITLE_MIN} до ${TITLE_MAX} символов.`;
  if (draft.mode === 'multiple' && !optionsOk(draft)) problems.options = `Нужно от ${OPTIONS_MIN} до ${OPTIONS_MAX} разных вариантов.`;
  if (draft.criteria.trim().length < CRITERIA_MIN) problems.criteria = 'Опиши, как определяем результат, — хотя бы 15 символов.';
  if (!draft.category) problems.category = 'Выбери категорию.';
  const dates = dateErrors(draft, now);
  if (dates.closes) problems.closes = dates.closes;
  if (dates.resolves) problems.resolves = dates.resolves;
  return problems;
}
