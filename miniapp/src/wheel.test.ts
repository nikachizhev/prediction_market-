import { afterEach, describe, expect, it, vi } from 'vitest';
import type { WheelPrize } from './api';
import { cardView, celebrationLevel, chanceLabel, pickDemoPrize, slicesOf, stopAngle, wheelLayout, winPhrase } from './wheel';
import { tg } from './telegram';

const PRIZES: WheelPrize[] = [
  { prize: '5', chance: 0.6 },
  { prize: '10', chance: 0.25 },
  { prize: '25', chance: 0.1 },
  { prize: '50', chance: 0.04 },
  { prize: '100', chance: 0.01 },
];

/** Where the pointer lands, in degrees clockwise from the start of the first slice. */
const landing = (angle: number) => (((360 - angle) % 360) + 360) % 360;

describe('the wheel as drawn', () => {
  const layout = wheelLayout(PRIZES);

  it('keeps every chance whole: the pieces of a prize add up to its chance', () => {
    for (const p of PRIZES) {
      const sum = layout.filter((s) => s.prize === p.prize).reduce((a, s) => a + s.share, 0);
      expect(sum).toBeCloseTo(p.chance);
    }
  });

  it('cuts the commonest prize into pieces with another prize between each two', () => {
    expect(layout).toHaveLength(16);
    expect(layout.filter((s) => s.prize === '5')).toHaveLength(8);
    layout.forEach((s, i) => expect(s.prize === '5').toBe(i % 2 === 0));
    expect(layout.map((s) => s.prize).filter((p) => p !== '5')).toEqual(['10', '25', '10', '50', '10', '25', '10', '100']);
  });

  it('colours a piece by how rare its prize is', () => {
    expect(layout.find((s) => s.prize === '100')?.rank).toBe(4);
    expect(slicesOf(PRIZES)[1]).toEqual({ label: '10', share: 0.0625, tone: 1 });
  });

  it('is one slice when there is one prize', () => {
    expect(wheelLayout([{ prize: '5', chance: 1 }])).toEqual([{ prize: '5', share: 1, rank: 0 }]);
  });
});

describe('where the wheel stops', () => {
  it('lands inside a piece of the prize', () => {
    expect(landing(stopAngle(PRIZES, '5', 5, 0, 0)!)).toBeCloseTo(13.5); // the first 5 spans 0–27°
    expect(stopAngle(PRIZES, '25', 5, 0, 0)).toBe(1800 + 360 - 85.5); // the first 25 spans 76.5–94.5°
    expect(landing(stopAngle(PRIZES, '100', 5, 0, 0)!)).toBeCloseTo(358.2); // the last sliver, 356.4–360°
  });

  it('chooses among the pieces of the prize', () => {
    const tens = [0, 0.3, 0.6, 0.99].map((pick) => landing(stopAngle(PRIZES, '10', 1, 0, pick)!));
    expect(new Set(tens.map((a) => Math.round(a))).size).toBe(4);
  });

  it('never leaves the piece when moved inside it', () => {
    for (const offset of [-1, -0.5, 0.5, 1]) {
      const at = landing(stopAngle(PRIZES, '50', 3, offset, 0.5)!);
      expect(at).toBeGreaterThan(171);
      expect(at).toBeLessThan(185.4);
    }
  });

  it('knows nothing about a prize that is not on the wheel', () => {
    expect(stopAngle(PRIZES, '7')).toBeNull();
  });
});

describe('the wheel card in the feed', () => {
  it('is locked until a prediction, open after it, spun once used', () => {
    expect(cardView({ unlocked: false, spun: null })).toBe('locked');
    expect(cardView({ unlocked: true, spun: null })).toBe('open');
    expect(cardView({ unlocked: true, spun: '25' })).toBe('spun');
  });
});

describe('labels', () => {
  it('turns the table into slices and readable odds', () => {
    expect(chanceLabel(0.6)).toBe('60%');
    expect(chanceLabel(0.005)).toBe('0,5%');
  });
});

describe('the win', () => {
  it('is louder for a rarer prize', () => {
    expect(celebrationLevel(PRIZES, '5')).toBe(0);
    expect(celebrationLevel(PRIZES, '25')).toBe(2);
    expect(celebrationLevel(PRIZES, '100')).toBe(4);
    expect(celebrationLevel(PRIZES, '7')).toBe(0);
  });

  it('has a phrase for every level', () => {
    expect(winPhrase(0)).toBe('Капля удачи');
    expect(winPhrase(4)).toBe('Оракул сегодня щедр!');
    expect(winPhrase(9)).toBe('Оракул сегодня щедр!');
    expect(winPhrase(-1)).toBe('Капля удачи');
  });
});

describe('the demo', () => {
  it('gives the prize asked for in the link', () => {
    expect(pickDemoPrize(PRIZES, '100')).toBe('100');
    expect(pickDemoPrize(PRIZES, '100.00')).toBe('100');
  });

  it('falls back to the odds for an unknown or missing prize', () => {
    expect(pickDemoPrize(PRIZES, null, () => 0)).toBe('5');
    expect(pickDemoPrize(PRIZES, '7', () => 0.7)).toBe('10');
    expect(pickDemoPrize(PRIZES, null, () => 0.999)).toBe('100');
  });
});


describe('the tick under the finger', () => {
  afterEach(() => vi.unstubAllGlobals());

  const stub = (platform: string) => {
    const haptics = { impactOccurred: vi.fn(), selectionChanged: vi.fn(), notificationOccurred: vi.fn() };
    vi.stubGlobal('window', { Telegram: { WebApp: { initData: 'x', platform, HapticFeedback: haptics } } });
    return haptics;
  };

  it('is a light impact on Android, where the selection buzz is too short to feel', () => {
    const haptics = stub('android');
    tg.HapticFeedback.tick();
    expect(haptics.impactOccurred).toHaveBeenCalledWith('light');
    expect(haptics.selectionChanged).not.toHaveBeenCalled();
  });

  it('is the selection tick elsewhere', () => {
    const haptics = stub('ios');
    tg.HapticFeedback.tick();
    expect(haptics.selectionChanged).toHaveBeenCalledTimes(1);
  });
});
