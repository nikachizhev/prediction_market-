import { describe, expect, it } from 'vitest';
import { grantMessage } from './grants';

describe('grantMessage', () => {
  it('announces the daily bonus as in the mockup', () => {
    expect(grantMessage([{ kind: 'daily', amount: '20.00' }])).toBe('Бонус дня: +20 монет');
  });

  it('joins several grants', () => {
    expect(
      grantMessage([
        { kind: 'daily', amount: '20.00' },
        { kind: 'insurance', amount: '200.00' },
      ]),
    ).toBe('Бонус дня: +20 монет · Страховка: +200 монет');
  });

  it('leaves the starter pack to onboarding', () => {
    expect(grantMessage([{ kind: 'starter', amount: '1000.00' }])).toBeNull();
    expect(grantMessage([])).toBeNull();
  });
});
