import { describe, expect, it, vi } from 'vitest';
import { ApiError, type MarketCard, type MarketDetail, type Me, type TradeResult } from './api';
import {
  acceptedMessage,
  applyProbability,
  cardBlockReason,
  clampStake,
  defaultStake,
  maxStake,
  mergeMarket,
  optimisticCost,
  positionSummary,
  quickStakes,
  replaceCard,
  runOptimisticBuy,
  splitPercents,
  tradeBlockReason,
  withNetworkRetry,
} from './trade';

const card = (patch: Partial<MarketCard> = {}): MarketCard => ({
  id: 1,
  title: 'Выпадет снег?',
  category: 'weather',
  prob: 0.64,
  change_24h: 9,
  spark: [0.5, 0.55, 0.64],
  participants: 356,
  volume: '8900.00',
  closes_at: '2026-12-01T00:00:00+03:00',
  status: 'open',
  outcome: null,
  starred: false,
  ...patch,
});

const detail = (patch: Partial<MarketDetail> = {}): MarketDetail => ({
  ...card(),
  criteria: '',
  resolves_at: '2026-12-02T00:00:00+03:00',
  created_at: '2026-10-01T00:00:00+03:00',
  author: { display_name: 'Енот', photo_url: null, resolved: 0, disputes_lost: 0 },
  is_creator: false,
  can_trade: true,
  max_amount: '200.00',
  my_position: null,
  trades: [],
  resolution: null,
  ...patch,
});

const me = (invested: Record<number, string> = {}): Pick<Me, 'invested' | 'limits'> => ({
  invested,
  limits: { max_trade: '200.00', market_cap: '500.00', fee_rate: 0.02, creation_cost: '100.00', dispute_cost: '20.00', b: 144 },
});

describe('stake limits', () => {
  it('uses whole coins of max_amount', () => {
    expect(maxStake('187.45')).toBe(187);
    expect(maxStake('0.50')).toBe(0);
    expect(maxStake('nope')).toBe(0);
  });

  it('clamps the stake to 1..max', () => {
    expect(clampStake(500, 200)).toBe(200);
    expect(clampStake(0, 200)).toBe(1);
    expect(clampStake(49.6, 200)).toBe(50);
    expect(clampStake(10, 0)).toBe(0);
  });

  it('opens with 100 or the limit if it is lower', () => {
    expect(defaultStake(200)).toBe(100);
    expect(defaultStake(42)).toBe(42);
  });

  it('builds quick buttons 10 / 50 / 100 / Макс', () => {
    expect(quickStakes(200)).toEqual([
      { label: '10', value: 10, disabled: false },
      { label: '50', value: 50, disabled: false },
      { label: '100', value: 100, disabled: false },
      { label: 'Макс', value: 200, disabled: false },
    ]);
    const low = quickStakes(40);
    expect(low.map((q) => q.disabled)).toEqual([false, true, true, false]);
    expect(low[3].value).toBe(40);
  });

  it('splits percents so they add up to 100', () => {
    expect(splitPercents(0.634)).toEqual({ yes: 63, no: 37 });
    expect(splitPercents(0.995)).toEqual({ yes: 100, no: 0 });
  });
});

describe('when the panel cannot open', () => {
  it('blocks the creator and closed markets', () => {
    expect(tradeBlockReason(detail({ is_creator: true, can_trade: false }))).toMatch(/Создатель рынка/);
    expect(tradeBlockReason(detail({ status: 'closed', can_trade: false }))).toMatch(/закрыт/);
    expect(cardBlockReason(card({ status: 'pending' }))).toMatch(/закрыт/);
    expect(cardBlockReason(card({ closes_at: '2026-10-01T00:00:00+03:00' }), new Date('2026-10-25T12:00:00+03:00'))).toMatch(/закрыт/);
    expect(cardBlockReason(card(), new Date('2026-10-25T12:00:00+03:00'))).toBeNull();
  });

  it('blocks when nothing can be staked and says why', () => {
    expect(tradeBlockReason(detail({ max_amount: '0.00' }), me({ 1: '500.00' }))).toMatch(/не больше 500/);
    expect(tradeBlockReason(detail({ max_amount: '0.40' }), me())).toMatch(/Недостаточно монет/);
  });

  it('lets an ordinary user trade', () => {
    expect(tradeBlockReason(detail(), me())).toBeNull();
  });
});

describe('optimistic card update', () => {
  it('moves the price, the day change and the last spark point', () => {
    const next = applyProbability(card(), 0.7);
    expect(next.prob).toBe(0.7);
    expect(next.change_24h).toBeCloseTo(15);
    expect(next.spark).toEqual([0.5, 0.55, 0.7]);
  });

  it('takes the server values on commit', () => {
    const server = detail({ prob: 0.69, change_24h: 14, spark: [0.5, 0.55, 0.69], participants: 357, volume: '8950.00' });
    const merged = mergeMarket(card({ prob: 0.7 }), server);
    expect(merged).toMatchObject({ prob: 0.69, change_24h: 14, participants: 357, volume: '8950.00' });
    expect(merged).not.toHaveProperty('criteria');
  });

  it('replaces only the matching card', () => {
    const list = [card(), card({ id: 2 })];
    const out = replaceCard(list, card({ prob: 0.1 }));
    expect(out[0].prob).toBe(0.1);
    expect(out[1]).toBe(list[1]);
  });
});

describe('runOptimisticBuy', () => {
  const result = { balance: '950.00', market: detail({ prob: 0.69 }), trade: { side: 'yes', shares: '74.3', cost: '50.00', fee: '1.00' } } as TradeResult;

  function setup(send: () => Promise<TradeResult>) {
    const calls: string[] = [];
    const op = {
      send: vi.fn(send),
      cost: 50,
      probAfter: 0.7,
      adjustBalance: vi.fn((d: number) => calls.push(`balance ${d}`)),
      setBalance: vi.fn((b: string) => calls.push(`set ${b}`)),
      onOptimistic: vi.fn((p: number) => calls.push(`prob ${p}`)),
      onCommit: vi.fn(() => calls.push('commit')),
      onRollback: vi.fn(() => calls.push('rollback')),
      sleep: () => Promise.resolve(),
    };
    return { op, calls };
  }

  it('shows the trade first, then applies the server answer', async () => {
    const { op, calls } = setup(async () => result);
    const outcome = await runOptimisticBuy(op);
    expect(outcome).toEqual({ ok: true, result });
    expect(calls).toEqual(['balance -50', 'prob 0.7', 'set 950.00', 'commit']);
  });

  it('rolls everything back when the server refuses', async () => {
    const { op, calls } = setup(async () => {
      throw new ApiError(400, 'Недостаточно монет');
    });
    const outcome = await runOptimisticBuy(op);
    expect(outcome.ok).toBe(false);
    expect(!outcome.ok && outcome.error.message).toBe('Недостаточно монет');
    expect(calls).toEqual(['balance -50', 'prob 0.7', 'balance 50', 'rollback']);
    expect(op.send).toHaveBeenCalledTimes(1);
  });

  it('repeats the same request after a network error', async () => {
    let n = 0;
    const { op } = setup(async () => {
      n += 1;
      if (n === 1) throw new ApiError(0, 'Нет соединения');
      return result;
    });
    const outcome = await runOptimisticBuy(op);
    expect(outcome.ok).toBe(true);
    expect(op.send).toHaveBeenCalledTimes(2);
    expect(op.onRollback).not.toHaveBeenCalled();
  });

  it('skips the price preview without a quote', async () => {
    const { op, calls } = setup(async () => result);
    await runOptimisticBuy({ ...op, probAfter: null });
    expect(calls).not.toContain('prob 0.7');
  });
});

describe('withNetworkRetry', () => {
  it('gives up after the last delay', async () => {
    const attempt = vi.fn(async () => {
      throw new ApiError(0, 'Нет соединения');
    });
    await expect(withNetworkRetry(attempt, [1, 1], () => Promise.resolve())).rejects.toThrow('Нет соединения');
    expect(attempt).toHaveBeenCalledTimes(3);
  });

  it('does not repeat server refusals', async () => {
    const attempt = vi.fn(async () => {
      throw new ApiError(400, 'Рынок закрыт');
    });
    await expect(withNetworkRetry(attempt, [1, 1], () => Promise.resolve())).rejects.toThrow('Рынок закрыт');
    expect(attempt).toHaveBeenCalledTimes(1);
  });
});

describe('texts and small helpers', () => {
  it('uses the quoted cost only when it matches the stake', () => {
    const quote = { side: 'yes' as const, amount: '50.00', cost: '49.99' };
    expect(optimisticCost(50, 'yes', quote)).toBe(49.99);
    expect(optimisticCost(60, 'yes', quote)).toBe(60);
    expect(optimisticCost(50, 'no', quote)).toBe(50);
    expect(optimisticCost(50, 'yes', null)).toBe(50);
  });

  it('formats the «Принято» toast', () => {
    expect(acceptedMessage('yes', 50, '74.83')).toBe('Принято: ДА на 50. Выплата, если угадаешь: 74');
    expect(acceptedMessage('no', '10.00', '18.2')).toBe('Принято: НЕТ на 10. Выплата, если угадаешь: 18');
  });

  it('summarises a position by its larger side', () => {
    const pos = { yes_shares: '0', no_shares: '30.5', spent: '20.00', value: '18.00', payout_if_yes: '0.00', payout_if_no: '30.50' };
    expect(positionSummary(pos)).toEqual({ side: 'no', payout: 30.5 });
    expect(positionSummary(null)).toBeNull();
    expect(positionSummary({ ...pos, no_shares: '0' })).toBeNull();
  });
});
