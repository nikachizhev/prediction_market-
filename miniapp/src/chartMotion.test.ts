import { describe, expect, it } from 'vitest';
import { CASCADE_CARDS, CASCADE_STEP_MS, cascadeDelay, easeOutCubic, headAt, lineYAt } from './chartMotion';

const points = [
  { x: 0, y: 100 },
  { x: 50, y: 40 },
  { x: 100, y: 60 },
];

describe('lineYAt', () => {
  it('holds the previous value on a step line', () => {
    expect(lineYAt(points, 25, 'step')).toBe(100);
    expect(lineYAt(points, 50, 'step')).toBe(40);
    expect(lineYAt(points, 99, 'step')).toBe(40);
    expect(lineYAt(points, 100, 'step')).toBe(60);
  });

  it('interpolates a linear line', () => {
    expect(lineYAt(points, 25, 'linear')).toBe(70);
    expect(lineYAt(points, 75, 'linear')).toBe(50);
  });

  it('clamps outside the points and survives empty input', () => {
    expect(lineYAt(points, -10, 'linear')).toBe(100);
    expect(lineYAt(points, 500, 'step')).toBe(60);
    expect(lineYAt([], 10, 'step')).toBe(0);
  });

  it('skips points that share one moment', () => {
    const same = [
      { x: 0, y: 10 },
      { x: 0, y: 20 },
      { x: 10, y: 30 },
    ];
    expect(lineYAt(same, 5, 'step')).toBe(20);
  });
});

describe('headAt', () => {
  it('runs from the first point to the last one', () => {
    const late = [
      { x: 40, y: 10 },
      { x: 140, y: 30 },
    ];
    expect(headAt(late, 0, 'linear')).toEqual({ x: 40, y: 10 });
    expect(headAt(late, 0.5, 'linear')).toEqual({ x: 90, y: 20 });
    expect(headAt(late, 1, 'linear')).toEqual({ x: 140, y: 30 });
    expect(headAt(late, 2, 'linear')).toEqual({ x: 140, y: 30 });
  });

  it('handles empty input', () => {
    expect(headAt([], 0.5, 'step')).toEqual({ x: 0, y: 0 });
  });
});

describe('easeOutCubic', () => {
  it('starts at 0, ends at 1 and clamps', () => {
    expect(easeOutCubic(0)).toBe(0);
    expect(easeOutCubic(1)).toBe(1);
    expect(easeOutCubic(0.5)).toBeCloseTo(0.875);
    expect(easeOutCubic(-1)).toBe(0);
    expect(easeOutCubic(3)).toBe(1);
  });
});

describe('cascadeDelay', () => {
  it('staggers the first cards and lets the rest draw at once', () => {
    expect(cascadeDelay(0)).toBe(0);
    expect(cascadeDelay(2)).toBe(2 * CASCADE_STEP_MS);
    expect(cascadeDelay(CASCADE_CARDS - 1)).toBe((CASCADE_CARDS - 1) * CASCADE_STEP_MS);
    expect(cascadeDelay(CASCADE_CARDS)).toBe(0);
    expect(cascadeDelay(25)).toBe(0);
  });
});
