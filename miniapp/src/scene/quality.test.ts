import { describe, expect, it } from 'vitest';
import { SLOW_FRAMES_LIMIT, dotColor, drawBudgetExceeded, mix, moodOf, sceneSize, type Rgb } from './quality';

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
    ['/rating', 'moon'],
    ['/profile', 'violet'],
    ['/create', 'violet'],
  ])('%s → %s', (path, mood) => {
    expect(moodOf(path)).toBe(mood);
  });
});
