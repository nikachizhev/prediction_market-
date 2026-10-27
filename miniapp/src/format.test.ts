import { describe, expect, it } from 'vitest';
import {
  formatCompact,
  formatEnds,
  formatMoney,
  formatPercent,
  formatPoints,
  formatSigned,
  formatSince,
  plural,
  pluralize,
} from './format';

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

describe('formatCompact', () => {
  it('shortens thousands and millions', () => {
    expect(formatCompact('950.00')).toBe('950');
    expect(formatCompact(52600)).toBe(`52,6${S}тыс.`);
    expect(formatCompact(1000)).toBe(`1${S}тыс.`);
    expect(formatCompact(1_250_000)).toBe(`1,3${S}млн`);
  });
});

describe('formatEnds', () => {
  const now = new Date(2026, 9, 25, 12, 0);

  it('counts days within two months', () => {
    expect(formatEnds(new Date(2026, 10, 6, 13, 0).toISOString(), now)).toBe('ещё 12 дней');
    expect(formatEnds(new Date(2026, 9, 26, 13, 0).toISOString(), now)).toBe('ещё 1 день');
    expect(formatEnds(new Date(2026, 9, 25, 20, 0).toISOString(), now)).toBe('меньше суток');
  });

  it('shows the date for far deadlines', () => {
    expect(formatEnds(new Date(2026, 11, 31, 23, 0).toISOString(), now)).toBe('до 31 декабря');
    expect(formatEnds(new Date(2027, 5, 30, 12, 0).toISOString(), now)).toBe('до 30 июня 2027');
  });

  it('marks closed markets', () => {
    expect(formatEnds(new Date(2026, 9, 1).toISOString(), now)).toBe('приём закрыт');
  });
});

describe('formatSince', () => {
  it('names the month and year of registration', () => {
    expect(formatSince(new Date(2026, 9, 25).toISOString())).toBe('с октября 2026');
    expect(formatSince(new Date(2027, 0, 1, 12).toISOString())).toBe('с января 2027');
  });

  it('is empty for a broken date', () => {
    expect(formatSince('nope')).toBe('');
  });
});
