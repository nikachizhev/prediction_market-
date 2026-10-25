import { describe, expect, it } from 'vitest';
import type { Position } from './api';
import {
  botChatLink,
  botUsername,
  buildChart,
  canSell,
  changeLabel,
  CHART,
  chartY,
  createdStatusLine,
  estimateSellProceeds,
  heldSides,
  initials,
  outcomeLabel,
  periodChange,
  pointIndexAt,
  positionAfterSell,
  positionStats,
  positionsTotals,
  settledResult,
  shareLink,
  sharesLabel,
  statusView,
  timeAgo,
  timeLeft,
  untilResult,
} from './market';

const pos = (over: Partial<Position> = {}): Position => ({
  yes_shares: '152.000000',
  no_shares: '0.000000',
  spent: '100.00',
  value: '108.00',
  payout_if_yes: '152.00',
  payout_if_no: '0.00',
  ...over,
});

describe('buildChart', () => {
  it('places points by time and draws a step line', () => {
    const chart = buildChart([
      { t: '2026-10-01T00:00:00Z', p: 0.5 },
      { t: '2026-10-01T06:00:00Z', p: 0.75 },
      { t: '2026-10-02T00:00:00Z', p: 0.75 },
    ]);
    expect(chart.points.map((p) => p.x)).toEqual([0, CHART.plotWidth / 4, CHART.plotWidth]);
    expect(chart.points[0].y).toBe(80);
    expect(chart.points[1].y).toBe(45);
    expect(chart.line).toBe('M0.0 80.0 H80.5 V45.0 H322.0');
    expect(chart.area).toBe('M0.0 80.0 H80.5 V45.0 H322.0 V150 H0.0 Z');
    expect(chart.end).toMatchObject({ x: CHART.plotWidth, p: 0.75 });
  });

  it('handles empty, single and same-moment input', () => {
    expect(buildChart([])).toEqual({ line: '', area: '', points: [], end: null });
    const single = buildChart([{ t: '2026-10-01T00:00:00Z', p: 0.5 }]);
    expect(single.line).toBe('M0.0 80.0 H322.0');
    const same = buildChart([
      { t: '2026-10-01T00:00:00Z', p: 0.2 },
      { t: '2026-10-01T00:00:00Z', p: 0.4 },
      { t: '2026-10-01T00:00:00Z', p: 0.6 },
    ]);
    expect(same.points.map((p) => p.x)).toEqual([0, 161, 322]);
  });

  it('keeps probabilities inside the plot', () => {
    expect(chartY(1.2)).toBe(CHART.top);
    expect(chartY(-1)).toBe(CHART.bottom);
  });
});

describe('chart helpers', () => {
  const chart = buildChart([
    { t: '2026-10-01T00:00:00Z', p: 0.5 },
    { t: '2026-10-01T12:00:00Z', p: 0.6 },
    { t: '2026-10-02T00:00:00Z', p: 0.7 },
  ]);

  it('finds the point under the finger', () => {
    expect(pointIndexAt(chart.points, 0)).toBe(0);
    expect(pointIndexAt(chart.points, 160)).toBe(0);
    expect(pointIndexAt(chart.points, 161)).toBe(1);
    expect(pointIndexAt(chart.points, 400)).toBe(2);
    expect(pointIndexAt([], 10)).toBe(-1);
  });

  it('measures the change over the period', () => {
    expect(periodChange([{ t: 'a', p: 0.52 }, { t: 'b', p: 0.71 }])).toBe(19);
    expect(periodChange([{ t: 'a', p: 0.5 }])).toBe(0);
    expect(changeLabel(4, 'за неделю')).toBe('▲ 4 за неделю');
    expect(changeLabel(-3, 'за сутки')).toBe('▼ 3 за сутки');
  });
});

describe('time', () => {
  const now = new Date('2026-10-25T12:00:00Z');

  it('formats how long ago', () => {
    expect(timeAgo('2026-10-25T11:59:30Z', now)).toBe('только что');
    expect(timeAgo('2026-10-25T11:45:00Z', now)).toBe('15 мин назад');
    expect(timeAgo('2026-10-25T09:00:00Z', now)).toBe('3 ч назад');
    expect(timeAgo('2026-10-22T12:00:00Z', now)).toBe('3 дн. назад');
  });

  it('formats the dispute window', () => {
    expect(timeLeft('2026-10-26T06:24:00Z', now)).toBe('ещё 18 ч 24 мин');
    expect(timeLeft('2026-10-25T12:10:00Z', now)).toBe('ещё 10 мин');
    expect(timeLeft('2026-10-25T11:00:00Z', now)).toBeNull();
    expect(timeLeft(null, now)).toBeNull();
  });

  it('formats time until the result', () => {
    expect(untilResult('2026-12-31T12:00:00Z', now)).toBe('67 дней');
    expect(untilResult('2026-10-25T20:00:00Z', now)).toBe('меньше суток');
    expect(untilResult('2026-10-20T00:00:00Z', now)).toBeNull();
  });
});

describe('statusView', () => {
  it('maps every status to a card state', () => {
    expect(statusView('open')).toEqual({ phase: 'open', label: 'Идёт приём прогнозов', tone: 'yes' });
    expect(statusView('closed').phase).toBe('closed');
    expect(statusView('pending')).toEqual({ phase: 'review', label: 'Итог на проверке', tone: 'gold' });
    expect(statusView('disputed').phase).toBe('review');
    expect(statusView('resolved')).toEqual({ phase: 'resolved', label: 'Завершён', tone: 'muted' });
    expect(statusView('annulled').phase).toBe('annulled');
  });

  it('names outcomes', () => {
    expect(outcomeLabel('yes')).toBe('ДА');
    expect(outcomeLabel('no')).toBe('НЕТ');
    expect(outcomeLabel('cancel')).toBe('Отмена');
    expect(outcomeLabel(null)).toBe('—');
  });

  it('describes created markets', () => {
    const base = { outcome: null, participants: 27, closes_at: '2026-11-06T00:00:00Z' };
    expect(createdStatusLine({ ...base, status: 'open' }, 'ещё 12 дней')).toBe('Идёт приём · 27 участников · ещё 12 дней');
    expect(createdStatusLine({ ...base, status: 'closed', participants: 41 }, '')).toBe('Приём прогнозов закрыт · 41 участник');
    expect(createdStatusLine({ ...base, status: 'resolved', outcome: 'yes' }, '')).toBe('Завершён · итог ДА');
  });
});

describe('position', () => {
  it('summarises a one-sided position', () => {
    const s = positionStats(pos());
    expect(s).toMatchObject({ spent: 100, value: 108, diff: 8, main: 'yes', payout: 152, sides: ['yes'] });
    expect(heldSides(pos())).toEqual(['yes']);
    expect(heldSides(null)).toEqual([]);
  });

  it('handles both sides and the main one', () => {
    const p = pos({ no_shares: '200.000000', payout_if_no: '200.00' });
    expect(heldSides(p)).toEqual(['yes', 'no']);
    expect(positionStats(p)).toMatchObject({ main: 'no', payout: 200, payoutYes: 152, payoutNo: 200 });
  });

  it('adds up positions', () => {
    expect(positionsTotals([pos(), pos({ spent: '120.00', value: '65.00', payout_if_yes: '0.00', yes_shares: '0', no_shares: '182', payout_if_no: '182.00' })])).toEqual({
      spent: 220,
      value: 173,
      payout: 334,
    });
  });

  it('settles a finished market', () => {
    expect(settledResult(pos(), 'yes')).toEqual({ payout: 152, profit: 52 });
    expect(settledResult(pos(), 'no')).toEqual({ payout: 0, profit: -100 });
  });

  it('drops the sold side optimistically', () => {
    expect(positionAfterSell(pos(), 'yes', 100)).toBeNull();
    const both = pos({ no_shares: '50.000000', payout_if_no: '50.00', value: '120.00' });
    const after = positionAfterSell(both, 'yes', 100)!;
    expect(heldSides(after)).toEqual(['no']);
    expect(after.payout_if_yes).toBe('0.00');
    expect(after.value).toBe('20.00');
  });

  it('allows selling only while the market is open', () => {
    const now = new Date('2026-10-25T12:00:00Z');
    expect(canSell({ status: 'open', closes_at: '2026-11-01T00:00:00Z' }, now)).toBe(true);
    expect(canSell({ status: 'open', closes_at: '2026-10-01T00:00:00Z' }, now)).toBe(false);
    expect(canSell({ status: 'closed', closes_at: '2026-11-01T00:00:00Z' }, now)).toBe(false);
  });
});

describe('estimateSellProceeds', () => {
  it('matches the LMSR refund minus the fee', () => {
    // Selling 100 ДА at p = 0.5, b = 144: gross = -144·ln(0.5·e^(-100/144) + 0.5) = 41.48, fee 0.83.
    expect(estimateSellProceeds(100, 'yes', 0.5, 144, 0.02)).toBe(40.65);
    // Selling НЕТ uses the price of НЕТ.
    expect(estimateSellProceeds(100, 'no', 0.5, 144, 0.02)).toBe(40.65);
    expect(estimateSellProceeds(100, 'no', 0.2, 144, 0)).toBeGreaterThan(estimateSellProceeds(100, 'yes', 0.2, 144, 0));
  });

  it('never exceeds the shares and is zero for nothing', () => {
    expect(estimateSellProceeds(10, 'yes', 0.99, 144, 0)).toBeLessThan(10);
    expect(estimateSellProceeds(0, 'yes', 0.5, 144, 0.02)).toBe(0);
    expect(estimateSellProceeds(10, 'yes', 0.5, 0, 0.02)).toBe(0);
  });
});

describe('links', () => {
  const ref = 'https://t.me/predict_bot/app?startapp=ref_ab12';

  it('builds the share link', () => {
    expect(shareLink(ref, 'Выйдет ли GTA VI?')).toBe(
      'https://t.me/share/url?url=https%3A%2F%2Ft.me%2Fpredict_bot%2Fapp%3Fstartapp%3Dref_ab12&text=' +
        encodeURIComponent('Выйдет ли GTA VI?'),
    );
  });

  it('finds the bot', () => {
    expect(botUsername(ref)).toBe('predict_bot');
    expect(botChatLink(ref)).toBe('https://t.me/predict_bot');
    expect(botUsername('')).toBeNull();
    expect(botChatLink(undefined)).toBeNull();
  });
});

describe('labels', () => {
  it('makes initials and share counts', () => {
    expect(initials('Аня К.')).toBe('АК');
    expect(initials('kirill')).toBe('K');
    expect(sharesLabel('152.4')).toBe('152 акции');
    expect(sharesLabel('5')).toBe('5 акций');
    expect(sharesLabel('1')).toBe('1 акция');
  });
});
