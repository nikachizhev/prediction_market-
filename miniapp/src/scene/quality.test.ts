import { describe, expect, it } from 'vitest';
import { SLOW_FRAMES_LIMIT, dotColor, drawBudgetExceeded, mix, moodOf, sceneSize, sliceIndexAt, spinCurve, type Rgb } from './quality';

const NO: Rgb = [0, 0, 100];
const YES: Rgb = [100, 0, 0];

describe('the orb light', () => {
  it('follows the probability', () => {
    expect(dotColor(0, NO, YES)).toEqual(NO);
    expect(dotColor(1, NO, YES)).toEqual(YES);
    expect(dotColor(0.5, NO, YES)).toEqual([50, 0, 50]);
  });

  it('survives a broken probability', () => {
    expect(dotColor(Number.NaN, NO, YES)).toEqual([50, 0, 50]);
    expect(dotColor(7, NO, YES)).toEqual(YES);
    expect(dotColor(-1, NO, YES)).toEqual(NO);
  });

  it('mixes channels and rounds', () => {
    expect(mix([0, 10, 20], [10, 20, 30], 0.25)).toEqual([3, 13, 23]);
  });
});

describe('the scene on a device', () => {
  it('is half the screen, within limits', () => {
    expect(sceneSize(390, 844)).toEqual({ width: 195, height: 422 });
    expect(sceneSize(2000, 3000)).toEqual({ width: 320, height: 560 });
    expect(sceneSize(100, 100)).toEqual({ width: 120, height: 200 });
  });

  it('gives up animating only after many slow frames', () => {
    expect(drawBudgetExceeded(SLOW_FRAMES_LIMIT - 1)).toBe(false);
    expect(drawBudgetExceeded(SLOW_FRAMES_LIMIT)).toBe(true);
  });
});

describe('the light of each part of the app', () => {
  it.each([
    ['/', 'amber'],
    ['/market/5', 'amber'],
    ['/challenge/abc', 'ember'],
    ['/wheel', 'ember'],
    ['/rating', 'moon'],
    ['/profile', 'violet'],
    ['/create', 'violet'],
  ])('%s → %s', (path, mood) => {
    expect(moodOf(path)).toBe(mood);
  });
});

describe('the spin', () => {
  it('starts at rest and ends exactly on the target', () => {
    expect(spinCurve(0, 1836)).toBeCloseTo(0);
    expect(spinCurve(1, 1836)).toBe(1836);
    expect(spinCurve(2, 1836)).toBe(1836);
  });

  it('winds up backwards first', () => {
    expect(spinCurve(0.05, 1836)).toBeLessThan(0);
  });

  it('runs past the slice a little and settles back', () => {
    const peak = Math.max(...[0.9, 0.93, 0.95, 0.97].map((k) => spinCurve(k, 1836)));
    expect(peak).toBeGreaterThan(1836);
    expect(peak).toBeLessThan(1836 + 8);
  });

  it('only moves forward between the wind-up and the overshoot', () => {
    let previous = spinCurve(0.1, 1836);
    for (let k = 0.12; k < 0.75; k += 0.02) {
      const now = spinCurve(k, 1836);
      expect(now).toBeGreaterThanOrEqual(previous);
      previous = now;
    }
  });
});

describe('the slice under the pointer', () => {
  const shares = [0.6, 0.25, 0.1, 0.04, 0.01];

  it('follows the turn', () => {
    expect(sliceIndexAt(shares, 0)).toBe(0);
    expect(sliceIndexAt(shares, 1836)).toBe(2); // the pointer at 324°: the 25 slice
    expect(sliceIndexAt(shares, 360 - 350)).toBe(3);
    expect(sliceIndexAt(shares, 360 - 359)).toBe(4);
  });
});
