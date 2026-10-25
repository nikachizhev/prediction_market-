import { describe, expect, it } from 'vitest';
import type { RatingResponse } from './api';
import { inviteShareUrl } from './invite';
import { formatRatingValue, hasOthers, initials, medalOf } from './rating';

describe('formatRatingValue', () => {
  it('shows profit with a sign, whole coins', () => {
    expect(formatRatingValue('1240.00', 'profit')).toBe('+1 240');
    expect(formatRatingValue('-35.60', 'profit')).toBe('−36');
    expect(formatRatingValue('0.00', 'profit')).toBe('0');
  });

  it('shows accuracy as a percent', () => {
    expect(formatRatingValue('0.6300', 'accuracy')).toBe('63%');
    expect(formatRatingValue('1.0000', 'accuracy')).toBe('100%');
    expect(formatRatingValue('0.0000', 'accuracy')).toBe('0%');
  });

  it('survives garbage', () => {
    expect(formatRatingValue('abc', 'profit')).toBe('—');
  });
});

describe('medalOf', () => {
  it('gives medals to the top three', () => {
    expect([1, 2, 3, 4].map(medalOf)).toEqual(['gold', 'silver', 'bronze', null]);
  });
});

describe('initials', () => {
  it('takes up to two letters', () => {
    expect(initials('Саша М.')).toBe('СМ');
    expect(initials('капибара')).toBe('К');
    expect(initials('Анна Мария Петрова')).toBe('АМ');
    expect(initials('  ')).toBe('?');
  });
});

describe('hasOthers', () => {
  const item = (is_me: boolean) => ({ rank: 1, display_name: 'X', photo_url: null, value: '0.00', is_me });
  it('treats a friends list with only me as empty', () => {
    expect(hasOthers({ items: [item(true)], me: { rank: 1, value: '0.00' } } as RatingResponse)).toBe(false);
    expect(hasOthers({ items: [], me: null })).toBe(false);
    expect(hasOthers({ items: [item(true), item(false)], me: null })).toBe(true);
  });
});

describe('inviteShareUrl', () => {
  it('builds the Telegram share link with the encoded referral link', () => {
    const url = inviteShareUrl('https://t.me/bot/app?startapp=ref_7Q2K', 'Привет & пока');
    expect(url).toBe(
      'https://t.me/share/url?url=https%3A%2F%2Ft.me%2Fbot%2Fapp%3Fstartapp%3Dref_7Q2K&text=' +
        encodeURIComponent('Привет & пока'),
    );
  });
});
