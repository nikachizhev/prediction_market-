// 1-on-1 challenge (spec E11): what the screens show, without React. Numbers come from the server.
import type { Challenge, ChallengeAnswer, ChallengeLimits } from './api';
import { formatMoney } from './format';
import { localIso } from './create';

export const TITLE_MIN = 10;
export const TITLE_MAX = 120;
export const CRITERIA_MIN = 15;
export const CRITERIA_MAX = 2000;

const ANSWERS: Record<ChallengeAnswer, string> = { yes: 'ДА', no: 'НЕТ', cancel: 'Отмена' };

export function answerLabel(answer: ChallengeAnswer): string {
  return ANSWERS[answer];
}

export function otherSide(side: 'yes' | 'no'): 'yes' | 'no' {
  return side === 'yes' ? 'no' : 'yes';
}

/** Quick stakes within the limits, the largest possible last; empty when the balance is too small. */
export function stakeOptions(limits: Pick<ChallengeLimits, 'min_stake' | 'max_stake'>): number[] {
  const min = Number(limits.min_stake);
  const max = Math.floor(Number(limits.max_stake));
  if (!(max >= min)) return [];
  const usual = [20, 50, 100].filter((value) => value >= min && value < max);
  return [...usual, max];
}

/** What the winner gets: the pot minus the fee, rounded down as on the server. */
export function prizeOf(stake: number, fee: string): number {
  const cents = Math.floor(stake * 2 * (1 - Number(fee)) * 100 + 1e-6);
  return cents / 100;
}

export interface ChallengeDraft {
  title: string;
  criteria: string;
  side: 'yes' | 'no';
  stake: number;
  date: string;
  time: string;
}

/** First thing that keeps the form from being sent; null — all is well. */
export function draftProblem(draft: ChallengeDraft, limits: ChallengeLimits, now: Date = new Date()): string | null {
  const title = draft.title.trim();
  if (title.length < TITLE_MIN || title.length > TITLE_MAX) return `Вопрос — от ${TITLE_MIN} до ${TITLE_MAX} символов.`;
  if (draft.criteria.trim().length < CRITERIA_MIN) return 'Опиши, как определяем результат.';
  if (draft.stake < Number(limits.min_stake)) return `Ставка — от ${formatMoney(limits.min_stake)} монет.`;
  if (draft.stake > Number(limits.max_stake)) return `Ставка не больше ${formatMoney(limits.max_stake)}.`;
  const iso = localIso(draft.date, draft.time);
  if (!iso) return 'Укажи дату итога.';
  const at = new Date(iso).getTime();
  if (at < now.getTime() + limits.min_hours * 3_600_000) return `Дата итога — не раньше чем через ${limits.min_hours} час.`;
  if (at > now.getTime() + limits.max_days * 86_400_000) return `Дата итога — не позже чем через ${limits.max_days} дней.`;
  return null;
}

/** «Ещё 18 часов»: whole hours, at least one; null when the time is over. */
export function hoursLeft(until: string | null | undefined, now: Date = new Date()): number | null {
  if (!until) return null;
  const ms = new Date(until).getTime() - now.getTime();
  return ms > 0 ? Math.max(1, Math.ceil(ms / 3_600_000)) : null;
}

export function hoursText(hours: number): string {
  const tens = hours % 100;
  const ones = hours % 10;
  if (tens >= 11 && tens <= 14) return `${hours} часов`;
  if (ones === 1) return `${hours} час`;
  if (ones >= 2 && ones <= 4) return `${hours} часа`;
  return `${hours} часов`;
}

export type ChallengePhase =
  | 'invite' // a guest can accept
  | 'closed' // a guest came too late
  | 'waiting_rival' // the author waits for someone to accept
  | 'running' // accepted, the outcome date hasn't come
  | 'answer' // the outcome date has come, nobody answered
  | 'rival_answered' // the rival answered, it's my turn
  | 'waiting_answer' // I answered, the rival hasn't
  | 'dispute'
  | 'done';

export function challengePhase(c: Challenge): ChallengePhase {
  if (c.status === 'finished' || c.status === 'cancelled') return 'done';
  if (c.status === 'disputed') return 'dispute';
  if (c.role === 'guest') return c.can_accept ? 'invite' : 'closed';
  if (c.status === 'open') return 'waiting_rival';
  if (c.my_answer) return 'waiting_answer';
  if (c.can_answer) return c.rival_answer ? 'rival_answered' : 'answer';
  return 'running';
}

/** The right part of a row in «Мои → Вызовы»; `alert` — it waits for me. */
export function rowState(c: Challenge, now: Date = new Date()): { text: string; tone: 'alert' | 'plain' | 'good' } {
  switch (challengePhase(c)) {
    case 'answer':
    case 'rival_answered':
      return { text: 'Чем закончилось?', tone: 'alert' };
    case 'dispute':
      return { text: 'Спор у модератора', tone: 'alert' };
    case 'waiting_rival': {
      const left = hoursLeft(c.expires_at, now);
      return { text: left ? `Ещё ${hoursText(left)}` : 'Время вышло', tone: 'plain' };
    }
    case 'waiting_answer':
      return { text: 'Ждём ответа', tone: 'plain' };
    case 'running':
      return { text: 'Идёт', tone: 'plain' };
    case 'done':
      if (c.result === 'won') return { text: `+${formatMoney(c.payout ?? 0)}`, tone: 'good' };
      if (c.result === 'lost') return { text: `−${formatMoney(c.stake)}`, tone: 'plain' };
      return { text: 'Отменён', tone: 'plain' };
    default:
      return { text: '', tone: 'plain' };
  }
}

/** The left part of the row: «Аня · 50» or «ждём соперника · 20». */
export function rowSubtitle(c: Challenge): string {
  const rival = c.role === 'author' ? c.opponent_name : c.author_name;
  return `${rival ?? 'ждём соперника'} · ${formatMoney(c.stake)}`;
}

/** Why the challenge ended, under the result. */
export function closedText(c: Challenge): string {
  switch (c.closed_reason) {
    case 'agreed':
      return 'оба ответили одинаково';
    case 'timeout':
      return 'второй не ответил вовремя';
    case 'moderator':
      return 'решил модератор';
    case 'both_cancelled':
      return 'оба отменили';
    case 'silent':
      return 'никто не ответил за 72 часа';
    case 'expired':
      return 'никто не принял вовремя';
    case 'withdrawn':
      return 'автор отменил вызов';
    case 'rejected':
      return 'вызов нарушает правила';
    default:
      return '';
  }
}

/** Telegram's share sheet with the challenge's link. */
export function challengeShareUrl(c: Pick<Challenge, 'link' | 'title' | 'stake'>): string {
  const text = `Спорим на ${formatMoney(c.stake)}? ${c.title}`;
  return `https://t.me/share/url?url=${encodeURIComponent(c.link ?? '')}&text=${encodeURIComponent(text)}`;
}

/** The question written elsewhere: after @bot or on «Создать». */
export function formTitle(search: string): string {
  return (new URLSearchParams(search).get('text') ?? '').trim().slice(0, TITLE_MAX);
}

/** The form's address for a question already written elsewhere. */
export function newChallengePath(text: string): string {
  const title = text.trim();
  return title ? `/challenge/new?text=${encodeURIComponent(title.slice(0, TITLE_MAX))}` : '/challenge/new';
}

export type ShareWay = 'prepared' | 'link';

/** How to send the card, best first: Telegram's chat picker with the card, or the plain link. */
export function shareWays(can: { prepared: boolean }): ShareWay[] {
  return [...(can.prepared ? (['prepared'] as const) : []), 'link'];
}
