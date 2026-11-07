import { describe, expect, it } from 'vitest';
import type { WheelPrize } from './api';
import { cardView, celebrationLevel, chanceLabel, pickDemoPrize, slicesOf, stopAngle, winPhrase } from './wheel';

const PRIZES: WheelPrize[] = [
  { prize: '5', chance: 0.6 },
  { prize: '10', chance: 0.25 },
  { prize: '25', chance: 0.1 },
  { prize: '50', chance: 0.04 },
  { prize: '100', chance: 0.01 },
];

/** Where the pointer lands, in degrees clockwise from the start of the first slice. */
const landing = (angle: number) => (((360 - angle) % 360) + 360) % 360;

describe('where the wheel stops', () => {
  it('stops in the middle of the prize slice after full turns', () => {
    expect(stopAngle(PRIZES, '25')).toBe(1800 + 36); // the 25 slice spans 306–342°
    expect(landing(stopAngle(PRIZES, 5)!)).toBeCloseTo(108);
    expect(landing(stopAngle(PRIZES, '100')!)).toBeCloseTo(358.2);
  });

  it('never leaves the slice when moved inside it', () => {
    for (const offset of [-1, -0.5, 0.5, 1]) {
      const at = landing(stopAngle(PRIZES, '50', 3, offset)!);
      expect(at).toBeGreaterThan(342);
      expect(at).toBeLessThan(356.4);
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
    expect(slicesOf(PRIZES)[2]).toEqual({ label: '25', share: 0.1 });
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

