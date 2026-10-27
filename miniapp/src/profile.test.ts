import { describe, expect, it } from 'vitest';
import {
  amountTone,
  balanceChangeLabel,
  balanceChart,
  balanceSeries,
  bestPredictionLabel,
  calibrationLabel,
  calibrationPoints,
  calibrationTip,
  daysAgo,
  formatLedgerDate,
  nearestPoint,
  niceStep,
  pseudonymError,
} from './profile';

const DAY = 24 * 60 * 60 * 1000;
const NOW = new Date('2026-10-25T12:00:00Z');
const ago = (days: number) => new Date(NOW.getTime() - days * DAY).toISOString();

describe('balanceSeries', () => {
  it('starts with the balance carried into the window and ends with the current one', () => {
    const series = balanceSeries(
      [
        { t: ago(60), balance: '1000.00' },
        { t: ago(40), balance: '950.00' },
        { t: ago(10), balance: '1020.00' },
      ],
      1040,
      NOW,
      30,
    );
    expect(series).toEqual([
      { t: NOW.getTime() - 30 * DAY, v: 950 },
      { t: NOW.getTime() - 10 * DAY, v: 1020 },
      { t: NOW.getTime(), v: 1040 },
    ]);
  });

  it('starts at the first operation for a new account', () => {
    const series = balanceSeries([{ t: ago(3), balance: '1000.00' }], 1000, NOW, 30);
    expect(series[0]).toEqual({ t: NOW.getTime() - 3 * DAY, v: 1000 });
    expect(series).toHaveLength(2);
  });

  it('draws a flat line without history', () => {
    expect(balanceSeries([], 500, NOW, 30)).toEqual([
      { t: NOW.getTime() - 30 * DAY, v: 500 },
      { t: NOW.getTime(), v: 500 },
    ]);
  });
});

describe('niceStep', () => {
  it('rounds to 1, 2, 5 × 10^n', () => {
    expect(niceStep(0.7)).toBe(1);
    expect(niceStep(37)).toBe(50);
    expect(niceStep(120)).toBe(200);
    expect(niceStep(0)).toBe(1);
  });
});

describe('balanceChart', () => {
  const box = { width: 300, top: 0, bottom: 100, start: 0, end: 30 };

  it('maps time to x and balance to y between round grid lines', () => {
    const chart = balanceChart(
      [
        { t: 0, v: 1000 },
        { t: 15, v: 1100 },
        { t: 30, v: 1200 },
      ],
      box,
    );
    expect(chart.ticks.map((t) => t.value)).toEqual([1000, 1100, 1200]);
    expect(chart.ticks.map((t) => t.y)).toEqual([100, 50, 0]);
    expect(chart.path).toBe('M0 100 L150 50 L300 0');
  });

  it('keeps every value inside the grid', () => {
    const chart = balanceChart(
      [
        { t: 0, v: 987 },
        { t: 30, v: 1243 },
      ],
      box,
    );
    const values = chart.ticks.map((t) => t.value);
    expect(values[0]).toBeLessThanOrEqual(987);
    expect(values[2]).toBeGreaterThanOrEqual(1243);
    for (const p of chart.points) {
      expect(p.y).toBeGreaterThanOrEqual(0);
      expect(p.y).toBeLessThanOrEqual(100);
    }
  });

  it('handles a flat line', () => {
    const chart = balanceChart(
      [
        { t: 0, v: 500 },
        { t: 30, v: 500 },
      ],
      box,
    );
    expect(chart.points[0].y).toBe(chart.points[1].y);
    expect(chart.path.startsWith('M0 ')).toBe(true);
  });

  it('finds the point under the pointer', () => {
    expect(nearestPoint([{ x: 0 }, { x: 150 }, { x: 300 }], 140)).toBe(1);
    expect(nearestPoint([{ x: 0 }, { x: 150 }, { x: 300 }], 290)).toBe(2);
  });
});

describe('labels', () => {
  it('describes the change over the window', () => {
    expect(
      balanceChangeLabel([
        { t: 0, v: 1000 },
        { t: 30 * DAY, v: 1240 },
      ]),
    ).toBe('+240 за 30 дней');
    expect(
      balanceChangeLabel([
        { t: 0, v: 1000 },
        { t: 2 * DAY, v: 900 },
      ]),
    ).toBe('−100 за 2 дня');
    expect(
      balanceChangeLabel([
        { t: 0, v: 1000 },
        { t: 3600_000, v: 900 },
      ]),
    ).toBe('−100 за сегодня');
  });

  it('says how long ago a point was', () => {
    expect(daysAgo(NOW.getTime() - 3600_000, NOW)).toBe('сегодня');
    expect(daysAgo(NOW.getTime() - 3 * DAY, NOW)).toBe('3 дн. назад');
  });

  it('formats ledger dates', () => {
    const at = new Date(2026, 9, 25, 9, 5).toISOString();
    expect(formatLedgerDate(at, new Date(2026, 9, 26))).toBe('25 октября, 09:05');
  });
});

describe('amountTone', () => {
  it('colours income, spending and zero', () => {
    expect(amountTone('20.00')).toBe('plus');
    expect(amountTone('-100.00')).toBe('minus');
    expect(amountTone('0.00')).toBe('zero');
    expect(amountTone(-0.001)).toBe('zero');
  });
});

describe('pseudonymError', () => {
  it('accepts 2..32 characters after trimming', () => {
    expect(pseudonymError('Ёж')).toBeNull();
    expect(pseudonymError('x'.repeat(32))).toBeNull();
    expect(pseudonymError(' a ')).toBe('Псевдоним — от 2 до 32 символов.');
    expect(pseudonymError('x'.repeat(33))).toBe('Псевдоним — от 2 до 32 символов.');
  });
});

describe('calibration', () => {
  const buckets = [
    { price: 10, count: 8, hits: 1 },
    { price: 50, count: 0, hits: 0 },
    { price: 70, count: 9, hits: 6 },
  ];

  it('places points like the mockup: price on x, hit rate on y', () => {
    const points = calibrationPoints(buckets);
    expect(points.map((p) => p.price)).toEqual([10, 70]);
    expect(points[0]).toMatchObject({ x: 66.4, y: 167.5, count: 8, hits: 1 });
    expect(points[1].x).toBe(248.8);
    expect(points[1].y).toBe(70);
  });

  it('describes the chart and each point in words', () => {
    const points = calibrationPoints(buckets);
    expect(calibrationLabel(points)).toBe('Калибровка: покупки по 10% сбылись в 13%, по 70% — в 67%');
    expect(calibrationTip(points[1])).toEqual(['Покупал по ~70%', 'сбылось 6 из 9 (67%)']);
  });
});

describe('bestPredictionLabel', () => {
  it('names the side and the price', () => {
    const best = { market: { id: 1, title: 'X' }, side: 'yes' as const, price: 0.12, gain: '218.00' };
    expect(bestPredictionLabel(best)).toBe('Купил ДА по 12% — сбылось');
    expect(bestPredictionLabel({ ...best, side: 'no', price: 0.3 })).toBe('Купил НЕТ по 30% — сбылось');
  });
});
