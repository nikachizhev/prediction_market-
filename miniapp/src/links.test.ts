import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import { marketFromStartParam, marketShareLink, shareMarket } from './links';
import { topMovesView } from './market';

describe('marketShareLink', () => {
  it('appends -m<id> to the referral start_param', () => {
    expect(marketShareLink('https://t.me/bot/app?startapp=ref_7Q2K', 12)).toBe('https://t.me/bot/app?startapp=ref_7Q2K-m12');
  });

  it('works without a referral code', () => {
    expect(marketShareLink('https://t.me/bot/app?startapp=', 3)).toBe('https://t.me/bot/app?startapp=m3');
    expect(marketShareLink('https://t.me/bot/app', 3)).toBe('https://t.me/bot/app?startapp=m3');
  });
});

describe('marketFromStartParam', () => {
  it('reads the market id', () => {
    expect(marketFromStartParam('m42')).toBe(42);
    expect(marketFromStartParam('ref_7Q2K-m42')).toBe(42);
  });

  it('ignores other params', () => {
    expect(marketFromStartParam('ref_7Q2K')).toBeNull();
    expect(marketFromStartParam(undefined)).toBeNull();
    expect(marketFromStartParam('m0')).toBeNull();
    expect(marketFromStartParam('mx1')).toBeNull();
    expect(marketFromStartParam('ref_abc-m12x')).toBeNull();
  });
});

describe('shareMarket', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('opens t.me/share with the market link outside Telegram', async () => {
    vi.spyOn(api, 'event').mockResolvedValue({ ok: true });
    const prepare = vi.spyOn(api, 'shareMarket');
    const open = vi.fn();
    vi.stubGlobal('window', { open, location: { search: '' } });
    await shareMarket({ id: 9, title: 'Снег?' }, 'https://t.me/bot/app?startapp=ref_A');
    expect(prepare).not.toHaveBeenCalled();
    expect(open.mock.calls[0][0]).toBe(
      'https://t.me/share/url?url=' + encodeURIComponent('https://t.me/bot/app?startapp=ref_A-m9') + '&text=' + encodeURIComponent('Снег?'),
    );
  });

  it('uses shareMessage when Telegram supports it and falls back on error', async () => {
    vi.spyOn(api, 'event').mockResolvedValue({ ok: true });
    const shareMessage = vi.fn((_id: string, cb: (sent: boolean) => void) => cb(true));
    const openTelegramLink = vi.fn();
    const webApp = {
      initData: 'x',
      isVersionAtLeast: () => true,
      shareMessage,
      openTelegramLink,
      HapticFeedback: { impactOccurred: vi.fn() },
    };
    vi.stubGlobal('window', { Telegram: { WebApp: webApp } });

    vi.spyOn(api, 'shareMarket').mockResolvedValueOnce({ prepared_message_id: 'pm1' });
    await shareMarket({ id: 9, title: 'Q' }, 'https://t.me/bot/app?startapp=ref_A');
    expect(shareMessage.mock.calls[0][0]).toBe('pm1');
    expect(openTelegramLink).not.toHaveBeenCalled();

    vi.spyOn(api, 'shareMarket').mockRejectedValueOnce(new Error('503'));
    await shareMarket({ id: 9, title: 'Q' }, 'https://t.me/bot/app?startapp=ref_A');
    expect(openTelegramLink).toHaveBeenCalledTimes(1);
    expect(openTelegramLink.mock.calls[0][0]).toContain(encodeURIComponent('ref_A-m9'));
  });
});

describe('topMovesView', () => {
  const now = new Date(2026, 9, 27, 15, 0);
  it('formats up to three moves', () => {
    const rows = topMovesView(
      [
        { at: new Date(2026, 9, 27, 9, 40).toISOString(), from: 0.41, to: 0.63 },
        { at: new Date(2026, 9, 26, 18, 0).toISOString(), from: 0.7, to: 0.52 },
      ],
      now,
    );
    expect(rows.map((r) => [r.when, r.move, r.up])).toEqual([
      ['Сегодня, 09:40', '41% → 63%', true],
      ['Вчера, 18:00', '70% → 52%', false],
    ]);
  });

  it('is empty when missing', () => {
    expect(topMovesView(undefined, now)).toEqual([]);
  });
});

describe('phase 2 api client', () => {
  afterEach(() => vi.unstubAllGlobals());

  function mockFetch(body: unknown) {
    const fn = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(body), { status: 200 }));
    vi.stubGlobal('fetch', fn);
    return fn;
  }

  it('builds the new requests', async () => {
    const fetch = mockFetch({});
    await api.notifications('pay');
    await api.readNotifications([1, 2]);
    await api.readNotifications();
    await api.profileHistory('w');
    await api.shareMarket(7);
    await api.updateMe({ notify: { moves: false } });
    const calls = fetch.mock.calls as unknown as [string, RequestInit][];
    expect(calls.map(([url, init]) => `${init.method} ${url}`)).toEqual([
      'GET /api/notifications?filter=pay',
      'POST /api/notifications/read',
      'POST /api/notifications/read',
      'GET /api/profile/history?period=w',
      'POST /api/markets/7/share',
      'PATCH /api/me',
    ]);
    expect(JSON.parse(calls[1][1].body as string)).toEqual({ ids: [1, 2] });
    expect(JSON.parse(calls[2][1].body as string)).toEqual({});
    expect(JSON.parse(calls[5][1].body as string)).toEqual({ notify: { moves: false } });
  });
});
