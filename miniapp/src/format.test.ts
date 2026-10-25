import { describe, expect, it } from 'vitest';
import { formatMoney, formatPercent, formatPoints, formatSigned, plural, pluralize } from './format';

const S = ' ';

describe('formatMoney', () => {
  it('groups thousands with a space and uses a decimal comma', () => {
    expect(formatMoney(1234.5)).toBe(`1${S}234,5`);
    expect(formatMoney('1234567.89')).toBe(`1${S}234${S}567,89`);
  });

  it('drops trailing zeros', () => {
    expect(formatMoney('100.00')).toBe('100');
    expect(formatMoney('20.50')).toBe('20,5');
    expect(formatMoney(0)).toBe('0');
  });

  it('handles small and negative numbers', () => {
    expect(formatMoney(999)).toBe('999');
    expect(formatMoney(-1500)).toBe(`−1${S}500`);
    expect(formatMoney(-0.001)).toBe('0');
  });

  it('rounds to the requested precision', () => {
    expect(formatMoney(1.005, 0)).toBe('1');
    expect(formatMoney(2.499, 1)).toBe('2,5');
  });

  it('returns 0 for garbage', () => {
    expect(formatMoney('abc')).toBe('0');
  });
});

describe('formatSigned', () => {
  it('adds a plus for positive values', () => {
    expect(formatSigned(20)).toBe('+20');
    expect(formatSigned('-5.5')).toBe('−5,5');
    expect(formatSigned(0)).toBe('0');
  });
});

describe('percent and points', () => {
  it('formats probabilities', () => {
    expect(formatPercent(0.634)).toBe('63%');
    expect(formatPercent(1)).toBe('100%');
    expect(formatPercent(0)).toBe('0%');
  });

  it('formats percentage point changes', () => {
    expect(formatPoints(4.2)).toBe('+4 п.п.');
    expect(formatPoints(-3)).toBe('−3 п.п.');
    expect(formatPoints(0.2)).toBe('0 п.п.');
  });
});

describe('plural', () => {
  const p = (n: number) => plural(n, 'участник', 'участника', 'участников');

  it('picks the singular form', () => {
    expect(p(1)).toBe('участник');
    expect(p(21)).toBe('участник');
    expect(p(101)).toBe('участник');
  });

  it('picks the few form', () => {
    expect(p(2)).toBe('участника');
    expect(p(4)).toBe('участника');
    expect(p(23)).toBe('участника');
  });

  it('picks the many form', () => {
    expect(p(0)).toBe('участников');
    expect(p(5)).toBe('участников');
    expect(p(11)).toBe('участников');
    expect(p(12)).toBe('участников');
    expect(p(14)).toBe('участников');
    expect(p(111)).toBe('участников');
    expect(p(1000)).toBe('участников');
  });

  it('builds a phrase with the number', () => {
    expect(pluralize(3, 'рынок', 'рынка', 'рынков')).toBe('3 рынка');
    expect(pluralize(1500, 'рынок', 'рынка', 'рынков')).toBe(`1${S}500 рынков`);
  });
});
