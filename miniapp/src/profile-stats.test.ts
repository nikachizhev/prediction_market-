import { describe, expect, it } from 'vitest';
import type { BestPrediction } from './api';
import {
  bestPredictionsOf,
  categoryRows,
  historySeries,
  inviteText,
  legacyHistory,
  periodWindow,
  scrubLabel,
  seriesChangeLabel,
} from './profile';

const DAY = 24 * 60 * 60 * 1000;
const NOW = new Date('2026-10-27T12:00:00Z');
const ago = (days: number) => new Date(NOW.getTime() - days * DAY).toISOString();

const points = [
  { t: ago(40), balance: '1000.00', profit: '0.00' },
  { t: ago(5), balance: '1100.00', profit: '60.00' },
  { t: ago(0.5), balance: '1180.00', profit: '80.00' },
];

describe('periodWindow', () => {
  it('maps periods to days', () => {
    expect(periodWindow('d', [], NOW).start).toBe(NOW.getTime() - DAY);
    expect(periodWindow('w', [], NOW).start).toBe(NOW.getTime() - 7 * DAY);
    expect(periodWindow('m', [], NOW).start).toBe(NOW.getTime() - 30 * DAY);
  });

  it('starts «Всё» at the first point', () => {
    expect(periodWindow('all', points, NOW).start).toBe(NOW.getTime() - 40 * DAY);
    expect(periodWindow('all', [], NOW).start).toBe(NOW.getTime() - DAY);
  });
});

describe('historySeries', () => {
  it('builds the balance series ending with the live balance', () => {
    expect(historySeries(points, 'balance', 'w', 1200, NOW)).toEqual([
      { t: NOW.getTime() - 7 * DAY, v: 1000 },
      { t: NOW.getTime() - 5 * DAY, v: 1100 },
      { t: NOW.getTime() - 0.5 * DAY, v: 1180 },
      { t: NOW.getTime(), v: 1200 },
    ]);
  });

  it('builds the profit series ending with the last known profit', () => {
    const series = historySeries(points, 'profit', 'all', null, NOW);
    expect(series.map((p) => p.v)).toEqual([0, 60, 80, 80]);
    expect(series[0].t).toBe(NOW.getTime() - 40 * DAY);
  });

  it('is flat without points', () => {
    expect(historySeries([], 'profit', 'd', null, NOW)).toEqual([
      { t: NOW.getTime() - DAY, v: 0 },
      { t: NOW.getTime(), v: 0 },
    ]);
  });

  it('accepts the legacy balance history', () => {
    const legacy = legacyHistory([{ t: ago(2), balance: '900.00' }]);
    expect(historySeries(legacy, 'balance', 'w', 950, NOW).map((p) => p.v)).toEqual([900, 950]);
  });
});

describe('labels', () => {
  it('describes the change over the period', () => {
    const series = historySeries(points, 'balance', 'm', 1240, NOW);
    expect(seriesChangeLabel(series, 'm')).toBe('+240 за месяц');
    expect(seriesChangeLabel(historySeries(points, 'profit', 'd', null, NOW), 'd')).toBe('+20 за сутки');
  });

  it('shows the time for «Д» and days otherwise', () => {
    const t = new Date(2026, 9, 27, 9, 30).getTime();
    expect(scrubLabel(t, 'd', NOW)).toBe('09:30');
    expect(scrubLabel(NOW.getTime() - 3 * DAY, 'w', NOW)).toBe('3 дн. назад');
  });
});

describe('categoryRows', () => {
  it('formats accuracy, predictions and profit, most predictions first', () => {
    const rows = categoryRows([
      { slug: 'sport', label: 'Спорт', predictions: 3, correct: 1, accuracy: 0.333, profit: '-40.00' },
      { slug: 'study', label: 'Учёба', predictions: 21, correct: 14, accuracy: 0.6667, profit: '120.50' },
      { slug: 'games', label: 'Игры', predictions: 0, correct: 0, accuracy: null, profit: '0.00' },
      { slug: 'tech', label: 'Технологии', predictions: 1, correct: 0, accuracy: null, profit: '0.00' },
    ]);
    expect(rows.map((r) => [r.label, r.accuracy, r.predictions, r.profit, r.tone])).toEqual([
      ['Учёба', '67%', '21 прогноз', '+121', 'plus'],
      ['Спорт', '33%', '3 прогноза', '−40', 'minus'],
      ['Технологии', '—', '1 прогноз', '0', 'zero'],
    ]);
    expect(rows[2].bar).toBe(0);
  });

  it('is empty for an older server', () => {
    expect(categoryRows(undefined)).toEqual([]);
  });
});

describe('bestPredictionsOf', () => {
  const best = (id: number): BestPrediction => ({ market: { id, title: 'Q' }, side: 'yes', price: 0.12, gain: '218.00' });

  it('takes up to three, falling back to best_prediction', () => {
    expect(bestPredictionsOf({ best_predictions: [best(1), best(2), best(3), best(4)], best_prediction: best(1) })).toHaveLength(3);
    expect(bestPredictionsOf({ best_prediction: best(7) }).map((b) => b.market.id)).toEqual([7]);
    expect(bestPredictionsOf({ best_predictions: [], best_prediction: null })).toEqual([]);
  });
});

describe('inviteText', () => {
  it('uses the referral summary like the mockup', () => {
    expect(
      inviteText({ invited: 3, rewarded: 2, earned: '600.00', reward: '300.00', predictions_needed: 5 }, 3),
    ).toBe('По 300 монет вам обоим, когда друг сделает 5 прогнозов. Приглашено 3 · получено 600.');
  });

  it('falls back to the old text', () => {
    expect(inviteText(undefined, 2)).toBe('Вы оба получите монеты, когда друг начнёт делать прогнозы. Приглашено 2.');
    expect(inviteText(undefined, null)).toBe('Вы оба получите монеты, когда друг начнёт делать прогнозы.');
  });
});
