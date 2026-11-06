// Phase 3d in the market card, settings, links and the API client (spec E4, E6.3, E9.4, E9.5).
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import { draftFromStartParam, chatFromStartParam, marketFromStartParam } from './links';
import { afterStar, authorLine, isNotifyLevel, NOTIFY_LEVELS, notifyLevelLabel, resolutionView } from './market';
import { hasQuietMode, notifySummary, QUIET_OPTION, withQuiet } from './notifications';

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal('fetch', fn);
  return fn;
}

const bodyOf = (fetch: ReturnType<typeof mockFetch>, i: number) => JSON.parse(fetch.mock.calls[i][1]!.body as string);

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('draftFromStartParam', () => {
  it('reads the draft code', () => {
    expect(draftFromStartParam('draft_Dk29x')).toBe('Dk29x');
    expect(draftFromStartParam('draft_a-b_C9')).toBe('a-b_C9');
  });

  it('takes nothing else for a draft, and a draft for nothing else', () => {
    expect(draftFromStartParam('draft_')).toBeNull();
    expect(draftFromStartParam('chat_k7Q2')).toBeNull();
    expect(draftFromStartParam('m42')).toBeNull();
    expect(draftFromStartParam('xdraft_abc')).toBeNull();
    expect(draftFromStartParam('draft_a b')).toBeNull();
    expect(draftFromStartParam(undefined)).toBeNull();
    expect(chatFromStartParam('draft_Dk29x')).toBeNull();
    expect(marketFromStartParam('draft_m12')).toBeNull();
  });
});

describe('bell of the market card', () => {
  it('offers «Всё / Только итог / Ничего» with hints', () => {
    expect(NOTIFY_LEVELS.map((o) => o.label)).toEqual(['Всё', 'Только итог', 'Ничего']);
    expect(NOTIFY_LEVELS.every((o) => o.hint.length > 0)).toBe(true);
    expect(notifyLevelLabel('result')).toBe('Только итог');
    expect(isNotifyLevel('none')).toBe(true);
    expect(isNotifyLevel('loud')).toBe(false);
  });

  it('takes the level from the star answer', () => {
    const market = { starred: false, notify_level: 'result' as const };
    expect(afterStar(market, { starred: true, notify_level: 'all' })).toEqual({ starred: true, notify_level: 'all' });
    expect(afterStar({ starred: true, notify_level: 'all' as const }, { starred: false, notify_level: 'none' })).toEqual({
      starred: false,
      notify_level: 'none',
    });
    // An older server answers only {starred}: the level stays.
    expect(afterStar(market, { starred: true })).toEqual({ starred: true, notify_level: 'result' });
  });

  it('sends the chosen level and reads the star answer', async () => {
    const fetch = mockFetch(200, { level: 'none' });
    expect(await api.setNotifyLevel(7, 'none')).toEqual({ level: 'none' });
    expect(fetch.mock.calls[0][0]).toBe('/api/markets/7/notify');
    expect(fetch.mock.calls[0][1]!.method).toBe('POST');
    expect(bodyOf(fetch, 0)).toEqual({ level: 'none' });
  });
});

describe('result in the market card', () => {
  it('a result without a photo has no photo place', () => {
    const view = resolutionView({ evidence_url: null, by_participant: false });
    expect(view.evidence).toBeNull();
    expect(view.source).toBe('Итог от автора');
    expect(resolutionView({ evidence_url: '', by_participant: false }).evidence).toBeNull();
    expect(resolutionView(null)).toEqual({ source: 'Итог от автора', byParticipant: false, evidence: null });
  });

  it('shows the photo when there is one', () => {
    expect(resolutionView({ evidence_url: '/media/e/1.jpg' }).evidence).toBe('/media/e/1.jpg');
  });

  it('marks a result proposed by a participant', () => {
    expect(resolutionView({ evidence_url: null, by_participant: true })).toEqual({
      source: 'Итог предложил участник',
      byParticipant: true,
      evidence: null,
    });
  });

  it('adds markets resolved by participants to the author reputation', () => {
    expect(authorLine({ resolved: 5, disputes_lost: 0, resolved_by_participants: 2 })).toBe(
      'Автор · разрешил 5 рынков · 0 проигранных споров · 2 рынка разрешили участники',
    );
    expect(authorLine({ resolved: 1, disputes_lost: 1, resolved_by_participants: 1 })).toBe(
      'Автор · разрешил 1 рынок · 1 проигранный спор · 1 рынок разрешили участники',
    );
    expect(authorLine({ resolved: 3, disputes_lost: 2, resolved_by_participants: 0 })).toBe(
      'Автор · разрешил 3 рынка · 2 проигранных спора',
    );
    expect(authorLine({ resolved: 3, disputes_lost: 2 })).toBe('Автор · разрешил 3 рынка · 2 проигранных спора');
  });
});

describe('quiet mode', () => {
  const notify = { results: true, moves: true, closing: true, referral: false, creator: true };

  it('is shown only when the server knows it', () => {
    expect(hasQuietMode({ notify_quiet: false })).toBe(true);
    expect(hasQuietMode({})).toBe(false);
    expect(hasQuietMode(null)).toBe(false);
  });

  it('switches optimistically', () => {
    expect(withQuiet({ notify_quiet: false, id: 1 }, true)).toEqual({ notify_quiet: true, id: 1 });
    const old: { id: number; notify_quiet?: boolean } = { id: 1 };
    expect(withQuiet(old, true)).toBe(old);
  });

  it('names itself in the profile instead of the switch count', () => {
    expect(notifySummary({ notify, notify_quiet: true })).toBe(QUIET_OPTION.label);
    expect(notifySummary({ notify, notify_quiet: false })).toBe('4 из 5');
    expect(QUIET_OPTION.hint).toMatch(/итоги с выплатами/);
  });

  it('is saved with PATCH /api/me', async () => {
    const fetch = mockFetch(200, { id: 1, notify_quiet: true });
    await api.updateMe({ notify_quiet: true });
    expect(fetch.mock.calls[0][0]).toBe('/api/me');
    expect(fetch.mock.calls[0][1]!.method).toBe('PATCH');
    expect(bodyOf(fetch, 0)).toEqual({ notify_quiet: true });
  });
});

describe('API of «О чём спорим?» and chat drafts', () => {
  it('asks the helper in the oneline mode', async () => {
    const fetch = mockFetch(200, {});
    await api.assist('спорим петя не сдаст матан', undefined, 'k7Q2', 'oneline');
    await api.assist('спорим петя не сдаст матан');
    expect(bodyOf(fetch, 0)).toEqual({ title: 'спорим петя не сдаст матан', chat: 'k7Q2', mode: 'oneline' });
    expect(bodyOf(fetch, 1)).toEqual({ title: 'спорим петя не сдаст матан' });
  });

  it('loads a chat draft by its code', async () => {
    const fetch = mockFetch(200, {});
    await api.chatDraft('Dk29x');
    expect(fetch.mock.calls[0][0]).toBe('/api/drafts/Dk29x');
    expect(fetch.mock.calls[0][1]!.method).toBe('GET');
  });

  it('sends the way to the wizard as an event', async () => {
    const fetch = mockFetch(200, { ok: true });
    await api.event('oneline_wizard', { reason: 'manual' });
    expect(bodyOf(fetch, 0)).toEqual({ name: 'oneline_wizard', props: { reason: 'manual' } });
  });

  it('creates a market from a draft with announce and the oneline outcome', async () => {
    const fetch = mockFetch(200, { market: null, balance: '900.00' });
    await api.createMarket(
      {
        title: 'Сдаст ли Петя матан?',
        criteria: 'ДА — если сдаст.',
        category: 'study',
        closes_at: '2026-12-19T23:59:00+03:00',
        resolves_at: '2026-12-21T23:59:00+03:00',
        chat: 'k7Q2',
        draft: 'Dk29x',
        announce: true,
        assist: 'oneline_edited',
      },
      'key-3',
    );
    expect(bodyOf(fetch, 0)).toMatchObject({ chat: 'k7Q2', draft: 'Dk29x', announce: true, assist: 'oneline_edited', request_key: 'key-3' });
  });
});
