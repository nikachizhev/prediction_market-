import { describe, expect, it } from 'vitest';
import { grantMessage } from './grants';

describe('grantMessage', () => {
  it('announces the daily bonus as in the mockup', () => {
    expect(grantMessage([{ kind: 'daily', amount: '20.00' }])).toBe('Бонус дня: +20 монет');
  });

  it('tells the run of days and tomorrow (phase 3f)', () => {
    expect(grantMessage([{ kind: 'daily', amount: '25.00', streak: 2, tomorrow: '30.00' }])).toBe(
      'Бонус дня: +25 монет · 2 дня подряд, завтра +30',
    );
    expect(grantMessage([{ kind: 'daily', amount: '50.00', streak: 21, tomorrow: '50.00' }])).toBe(
      'Бонус дня: +50 монет · 21 день подряд, завтра +50',
    );
    expect(grantMessage([{ kind: 'daily', amount: '20.00', streak: 1, tomorrow: '25.00' }])).toBe(
      'Бонус дня: +20 монет · завтра +25',
    );
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
