import { describe, expect, it } from 'vitest';
import { sparkPoints } from './components/Sparkline';

describe('sparkPoints', () => {
  it('stretches values to the box', () => {
    expect(sparkPoints([0.2, 0.6], 100, 30)).toBe('2.0,28.0 98.0,2.0');
  });

  it('handles empty and single-point input', () => {
    expect(sparkPoints([], 100, 30)).toBe('');
    expect(sparkPoints([0.5], 100, 30)).toBe('2.0,28.0 98.0,28.0');
  });
});
