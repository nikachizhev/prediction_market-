import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from './api';

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('api client', () => {
  it('requests the personalized feed and reports dismiss/undo with booleans', async () => {
    const fetch = mockFetch(200, {});
    await api.markets({ sort: 'recommended' });
    expect(fetch.mock.calls[0][0]).toBe('/api/markets?sort=recommended');
    await api.dismissMarket(7);
    await api.dismissMarket(7, false);
    expect(fetch.mock.calls[1][0]).toBe('/api/markets/7/dismiss');
    expect(JSON.parse(fetch.mock.calls[1][1]!.body as string)).toEqual({ dismissed: true });
    expect(JSON.parse(fetch.mock.calls[2][1]!.body as string)).toEqual({ dismissed: false });
    await api.resetHiddenMarkets();
    expect(fetch.mock.calls[3][0]).toBe('/api/feed/reset-hidden');
    expect(fetch.mock.calls[3][1]!.method).toBe('POST');
  });

  it('can clear all interests and send a market impression', async () => {
    const fetch = mockFetch(200, {});
    await api.updateMe({ interests: [] });
    expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toEqual({ interests: [] });
    await api.event('feed_impression', { market: 8, sort: 'recommended' });
    expect(JSON.parse(fetch.mock.calls[1][1]!.body as string)).toEqual({ name: 'feed_impression', props: { market: 8, sort: 'recommended' } });
  });
  it('sends initData in the Authorization header', async () => {
    vi.stubEnv('VITE_DEV_INIT_DATA', 'query_id=1&hash=abc');
    const fetch = mockFetch(200, { id: 1 });
    await api.me();
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/me');
    expect((init.headers as Record<string, string>).Authorization).toBe('tma query_id=1&hash=abc');
  });

  it('sends the buy request_key in the body', async () => {
    const fetch = mockFetch(200, {});
    await api.buy(7, 'yes', '50.00', 'key-1');
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/markets/7/buy');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ side: 'yes', amount: '50.00', request_key: 'key-1' });
  });

  it('generates a request_key when none is given', async () => {
    vi.stubGlobal('crypto', { randomUUID: () => 'generated-uuid' });
    const fetch = mockFetch(200, {});
    await api.buy(7, 'no', '10.00');
    const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(init.body as string).request_key).toBe('generated-uuid');
  });

  it('sends the create request_key in the body', async () => {
    const fetch = mockFetch(200, {});
    const data = { title: 'Q?', criteria: 'c', category: 'games', closes_at: 'a', resolves_at: 'b' };
    await api.createMarket(data, 'create-key');
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/markets');
    expect(JSON.parse(init.body as string)).toEqual({ ...data, request_key: 'create-key' });
  });

  it('sends onboarding interests with PATCH /me', async () => {
    const fetch = mockFetch(200, {});
    await api.updateMe({ onboarded: true, interests: ['games', 'sport'] });
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/me');
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body as string)).toEqual({ onboarded: true, interests: ['games', 'sport'] });
  });

  it('builds query strings without empty params', async () => {
    const fetch = mockFetch(200, { items: [], next_page: null });
    await api.markets({ category: 'games', q: '', sort: 'hot' });
    expect(fetch.mock.calls[0][0]).toBe('/api/markets?category=games&sort=hot');
  });

  it('throws ApiError with status and server message', async () => {
    mockFetch(400, { error: 'Недостаточно монет' });
    const err = await api.quote(1, 'yes', '10.00').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(400);
    expect((err as ApiError).message).toBe('Недостаточно монет');
  });

  it('falls back to a generic Russian message', async () => {
    mockFetch(500, null);
    const err = (await api.me().catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(500);
    expect(err.message).toMatch(/Что-то пошло не так/);
  });
});
