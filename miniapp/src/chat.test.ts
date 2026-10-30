import { describe, expect, it } from 'vitest';
import type { MarketCard, RatingResponse } from './api';
import { chatDelta, chatInitials, chatMarketMeta, chatPlace, chatRatingLines, chatSubtitle, isNewMarket } from './chat';

const NOW = new Date('2026-10-29T12:00:00Z');
const HOUR = 60 * 60 * 1000;

const card = (patch: Partial<MarketCard> = {}): MarketCard => ({
  id: 1,
  title: 'Перенесут ли коллоквиум по алгоритмам?',
  category: 'study',
  prob: 0.57,
  change_24h: 12,
  spark: [0.45, 0.57],
  participants: 19,
  volume: '800.00',
  closes_at: new Date(NOW.getTime() + 4 * 24 * HOUR + HOUR).toISOString(),
  status: 'open',
  outcome: null,
  starred: false,
  chat: { code: 'k7Q2', title: 'Группа ПИ-24' },
  created_at: new Date(NOW.getTime() - 2 * HOUR).toISOString(),
  ...patch,
});

describe('«новый» mark', () => {
  it('marks markets created less than a day ago', () => {
    expect(isNewMarket(card(), NOW)).toBe(true);
    expect(isNewMarket(card({ created_at: new Date(NOW.getTime() - 23.9 * HOUR).toISOString() }), NOW)).toBe(true);
    expect(isNewMarket(card({ created_at: new Date(NOW.getTime() - 24 * HOUR).toISOString() }), NOW)).toBe(false);
    expect(isNewMarket(card({ created_at: new Date(NOW.getTime() - 3 * 24 * HOUR).toISOString() }), NOW)).toBe(false);
  });

  it('is off without a valid date', () => {
    expect(isNewMarket(card({ created_at: undefined }), NOW)).toBe(false);
    expect(isNewMarket(card({ created_at: 'soon' }), NOW)).toBe(false);
  });
});

describe('chat header and tiles', () => {
  it('shows my place or a dash', () => {
    expect(chatPlace(3)).toBe('3 место');
    expect(chatPlace(1)).toBe('1 место');
    expect(chatPlace(null)).toBe('—');
    expect(chatPlace(undefined)).toBe('—');
  });

  it('counts the members', () => {
    expect(chatSubtitle({ members: 28 })).toBe('закрытый чат · 28 участников');
    expect(chatSubtitle({ members: 22 })).toBe('закрытый чат · 22 участника');
    expect(chatSubtitle({ members: 1 })).toBe('закрытый чат · 1 участник');
  });

  it('takes letters for the square from the title', () => {
    expect(chatInitials('Группа ПИ-24')).toBe('ПИ');
    expect(chatInitials('Друзья с дачи')).toBe('ДС');
    expect(chatInitials('Котики')).toBe('К');
  });
});

describe('chat market row', () => {
  it('shows category, term and participants', () => {
    expect(chatMarketMeta(card(), 'Учёба', NOW)).toBe('Учёба · ещё 4 дня · 19 участников');
    expect(chatMarketMeta(card({ participants: 15 }), '', NOW)).toBe('ещё 4 дня · 15 участников');
  });

  it('shows the change over a day with an arrow', () => {
    expect(chatDelta(12)).toEqual({ text: '▲ 12', up: true });
    expect(chatDelta(-6.4)).toEqual({ text: '▼ 6', up: false });
    expect(chatDelta(0)).toEqual({ text: '▲ 0', up: true });
  });
});

describe('chat rating rows', () => {
  const rating: Pick<RatingResponse, 'items'> = {
    items: [
      { rank: 1, value: '410.00', predictions: 9, accuracy: 0.78, profit: '410.00', display_name: 'Сова', photo_url: null, is_me: false },
      { rank: 3, value: '186.00', predictions: 6, accuracy: 0.667, profit: '186.00', display_name: 'Капибара', photo_url: null, is_me: true },
      { rank: 4, value: '140.00', predictions: 1, accuracy: null, profit: '140.00', display_name: 'Барсук', photo_url: null, is_me: false },
      { rank: 7, value: '-38.00', predictions: 3, accuracy: 0.5, profit: '-38.00', display_name: 'Бобр', photo_url: null, is_me: false },
    ],
  };

  it('formats rows as in the mockup', () => {
    const lines = chatRatingLines(rating);
    expect(lines.map((l) => l.medal)).toEqual(['gold', 'bronze', null, null]);
    expect(lines[0]).toMatchObject({ name: 'Сова', sub: '9 прогнозов в чате · точность 78%', value: '+410', me: false });
    expect(lines[1]).toMatchObject({ name: 'Капибара (ты)', sub: '6 прогнозов в чате · точность 67%', value: '+186', me: true });
    expect(lines[2].sub).toBe('1 прогноз в чате');
    expect(lines[3].value).toBe('−38');
  });
});
