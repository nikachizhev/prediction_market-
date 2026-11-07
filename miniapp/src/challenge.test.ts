import { describe, expect, it } from 'vitest';
import type { Challenge, ChallengeLimits } from './api';
import {
  challengePhase,
  challengeShareUrl,
  closedText,
  draftProblem,
  hoursLeft,
  hoursText,
  prizeOf,
  rowState,
  rowSubtitle,
  stakeOptions,
  type ChallengeDraft,
} from './challenge';
import { challengeFromStartParam, chatFromStartParam, marketFromStartParam } from './links';

const NOW = new Date('2026-11-07T12:00:00');
const LIMITS: ChallengeLimits = { min_stake: '10', max_stake: '186', fee: '0.02', min_hours: 1, max_days: 30, accept_hours: 24 };

function challenge(patch: Partial<Challenge> = {}): Challenge {
  return {
    code: 'abc',
    title: 'Сдам ли я матанализ?',
    criteria: 'ДА — если сдам.',
    role: 'author',
    my_side: 'yes',
    stake: '50',
    pot: '100',
    prize: '98',
    resolves_at: '2026-11-08T12:00:00',
    expires_at: '2026-11-08T12:00:00',
    status: 'active',
    author_name: 'Кирилл',
    opponent_name: 'Аня',
    link: null,
    can_accept: false,
    can_cancel: false,
    can_answer: false,
    my_answer: null,
    rival_answer: null,
    answer_until: null,
    dispute_cost: '20',
    proof_until: null,
    outcome: null,
    result: null,
    payout: null,
    closed_reason: null,
    ...patch,
  };
}

function draft(patch: Partial<ChallengeDraft> = {}): ChallengeDraft {
  return {
    title: 'Сдам ли я матанализ?',
    criteria: 'ДА — если в зачётке 4 или 5.',
    side: 'yes',
    stake: 50,
    date: '2026-11-10',
    time: '18:00',
    ...patch,
  };
}

describe('stakes', () => {
  it('offers usual stakes within the limits and the largest last', () => {
    expect(stakeOptions(LIMITS)).toEqual([20, 50, 100, 186]);
    expect(stakeOptions({ min_stake: '10', max_stake: '45.5' })).toEqual([20, 45]);
    expect(stakeOptions({ min_stake: '10', max_stake: '200' })).toEqual([20, 50, 100, 200]);
  });

  it('offers nothing when the balance is too small', () => {
    expect(stakeOptions({ min_stake: '10', max_stake: '4' })).toEqual([]);
  });

  it('rounds the prize down like the server', () => {
    expect(prizeOf(50, '0.02')).toBe(98);
    expect(prizeOf(33.33, '0.02')).toBe(65.32);
  });
});

describe('the creation form', () => {
  it('passes a good draft', () => {
    expect(draftProblem(draft(), LIMITS, NOW)).toBeNull();
  });

  it.each([
    [{ title: 'Коротко' }, 'Вопрос'],
    [{ criteria: 'мало' }, 'результат'],
    [{ stake: 5 }, 'от 10'],
    [{ stake: 187 }, 'не больше 186'],
    [{ date: '' }, 'дату'],
    [{ date: '2026-11-07', time: '12:30' }, 'не раньше'],
    [{ date: '2026-12-31' }, 'не позже'],
  ])('refuses %j', (patch, expected) => {
    expect(draftProblem(draft(patch), LIMITS, NOW)).toContain(expected);
  });
});

describe('phases', () => {
  it.each<[Partial<Challenge>, string]>([
    [{ role: 'guest', status: 'open', can_accept: true, opponent_name: null }, 'invite'],
    [{ role: 'guest', status: 'open', can_accept: false, opponent_name: null }, 'closed'],
    [{ status: 'open', opponent_name: null, can_cancel: true }, 'waiting_rival'],
    [{}, 'running'],
    [{ can_answer: true }, 'answer'],
    [{ can_answer: true, rival_answer: 'yes' }, 'rival_answered'],
    [{ my_answer: 'yes' }, 'waiting_answer'],
    [{ status: 'disputed', my_answer: 'yes', rival_answer: 'no' }, 'dispute'],
    [{ status: 'finished', result: 'won' }, 'done'],
    [{ status: 'cancelled', result: 'refunded' }, 'done'],
  ])('%j → %s', (patch, phase) => {
    expect(challengePhase(challenge(patch))).toBe(phase);
  });
});

describe('a row in «Мои»', () => {
  it('says when it waits for me', () => {
    expect(rowState(challenge({ can_answer: true }), NOW)).toEqual({ text: 'Чем закончилось?', tone: 'alert' });
    expect(rowState(challenge({ status: 'disputed' }), NOW).text).toBe('Спор у модератора');
  });

  it('counts hours for an unaccepted one', () => {
    const open = challenge({ status: 'open', opponent_name: null, expires_at: '2026-11-08T06:00:00' });
    expect(rowState(open, NOW).text).toBe('Ещё 18 часов');
    expect(rowSubtitle(open)).toBe('ждём соперника · 50');
    expect(rowState(challenge({ status: 'open', expires_at: '2026-11-07T11:00:00' }), NOW).text).toBe('Время вышло');
  });

  it('shows the money at the end', () => {
    expect(rowState(challenge({ status: 'finished', result: 'won', payout: '98' }), NOW)).toEqual({ text: '+98', tone: 'good' });
    expect(rowState(challenge({ status: 'finished', result: 'lost' }), NOW).text).toBe('−50');
    expect(rowState(challenge({ status: 'cancelled', result: 'refunded' }), NOW).text).toBe('Отменён');
  });

  it('names the rival by the role', () => {
    expect(rowSubtitle(challenge())).toBe('Аня · 50');
    expect(rowSubtitle(challenge({ role: 'opponent' }))).toBe('Кирилл · 50');
  });
});

describe('words', () => {
  it('declines hours', () => {
    expect([1, 2, 5, 11, 21, 23].map(hoursText)).toEqual(['1 час', '2 часа', '5 часов', '11 часов', '21 час', '23 часа']);
  });

  it('rounds hours up and stops at the end', () => {
    expect(hoursLeft('2026-11-07T12:10:00', NOW)).toBe(1);
    expect(hoursLeft('2026-11-07T11:00:00', NOW)).toBeNull();
    expect(hoursLeft(null, NOW)).toBeNull();
  });

  it('explains why it ended', () => {
    expect(closedText(challenge({ closed_reason: 'agreed' }))).toBe('оба ответили одинаково');
    expect(closedText(challenge({ closed_reason: 'moderator' }))).toBe('решил модератор');
    expect(closedText(challenge())).toBe('');
  });

  it('shares the link with the question', () => {
    const url = challengeShareUrl({ link: 'https://t.me/b/a?startapp=ch_x', title: 'Сдам?', stake: '50' });
    expect(url).toContain(encodeURIComponent('https://t.me/b/a?startapp=ch_x'));
    expect(decodeURIComponent(url)).toContain('Спорим на 50? Сдам?');
  });
});

describe('the deep link', () => {
  it('takes only a well-formed code', () => {
    expect(challengeFromStartParam('ch_a-b_C9')).toBe('a-b_C9');
    expect(challengeFromStartParam('ch_')).toBeNull();
    expect(challengeFromStartParam('xch_abc')).toBeNull();
    expect(challengeFromStartParam('ch_a b')).toBeNull();
    expect(challengeFromStartParam(null)).toBeNull();
    expect(marketFromStartParam('ch_abc')).toBeNull();
    expect(chatFromStartParam('ch_abc')).toBeNull();
  });
});
