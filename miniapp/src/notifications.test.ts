import { describe, expect, it } from 'vitest';
import type { Notice } from './api';
import { dayLabel, formatTime } from './format';
import {
  countUnread,
  filterNotices,
  groupNoticesByDay,
  markNoticesRead,
  NOTICE_FILTERS,
  noticeAction,
  noticeIcon,
  notifyCountLabel,
  toggleNotify,
  unreadBadge,
} from './notifications';

// Local time so that day boundaries do not depend on the test machine's zone.
const NOW = new Date(2026, 9, 27, 15, 0);
const at = (day: number, hour: number) => new Date(2026, 9, day, hour, 5).toISOString();

function notice(id: number, over: Partial<Notice> = {}): Notice {
  return {
    id,
    kind: 'results',
    group: 'pay',
    title: 'Ты угадал!',
    text: 'текст',
    at: at(27, 10),
    read: false,
    market: { id: 5, title: 'Рынок' },
    action: null,
    ...over,
  };
}

describe('dayLabel / formatTime', () => {
  it('names today, yesterday and older dates', () => {
    expect(dayLabel(at(27, 1), NOW)).toBe('Сегодня');
    expect(dayLabel(at(26, 23), NOW)).toBe('Вчера');
    expect(dayLabel(at(25, 12), NOW)).toBe('25 октября');
    expect(formatTime(new Date(2026, 9, 27, 9, 4).toISOString())).toBe('09:04');
  });
});

describe('groupNoticesByDay', () => {
  it('groups newest first by local day', () => {
    const groups = groupNoticesByDay(
      [notice(1, { at: at(26, 9) }), notice(2, { at: at(27, 12) }), notice(3, { at: at(27, 8) }), notice(4, { at: at(20, 8) })],
      NOW,
    );
    expect(groups.map((g) => g.title)).toEqual(['Сегодня', 'Вчера', '20 октября']);
    expect(groups[0].items.map((n) => n.id)).toEqual([2, 3]);
  });

  it('is empty for no notices', () => {
    expect(groupNoticesByDay([], NOW)).toEqual([]);
  });
});

describe('filters', () => {
  it('has the four phase 2 chips without «Чаты»', () => {
    expect(NOTICE_FILTERS.map((f) => f.label)).toEqual(['Все', 'Мои рынки', 'Выплаты', 'Слежу']);
  });

  it('filters by group', () => {
    const items = [notice(1, { group: 'mine' }), notice(2, { group: 'pay' }), notice(3, { group: 'markets' })];
    expect(filterNotices(items, 'all')).toHaveLength(3);
    expect(filterNotices(items, 'mine').map((n) => n.id)).toEqual([1]);
    expect(filterNotices(items, 'markets').map((n) => n.id)).toEqual([3]);
  });
});

describe('read state and badge', () => {
  it('marks some or all notices read', () => {
    const items = [notice(1), notice(2), notice(3, { read: true })];
    expect(countUnread(items)).toBe(2);
    expect(countUnread(markNoticesRead(items, [1]))).toBe(1);
    expect(countUnread(markNoticesRead(items))).toBe(0);
  });

  it('shows the badge only for unread', () => {
    expect(unreadBadge(0)).toBeNull();
    expect(unreadBadge(undefined)).toBeNull();
    expect(unreadBadge(3)).toBe('3');
    expect(unreadBadge(120)).toBe('99+');
  });
});

describe('icons and actions', () => {
  it('picks icons like the mockup', () => {
    expect(noticeIcon(notice(1, { kind: 'results' }))).toBe('win');
    expect(noticeIcon(notice(1, { kind: 'moves', group: 'markets' }))).toBe('move');
    expect(noticeIcon(notice(1, { kind: 'closing', group: 'markets' }))).toBe('task');
    expect(noticeIcon(notice(1, { kind: 'referral' }))).toBe('people');
    expect(noticeIcon(notice(1, { kind: 'creator', group: 'mine', action: 'resolve' }))).toBe('task');
    expect(noticeIcon(notice(1, { kind: 'daily', group: 'pay' }))).toBe('coin');
  });

  it('labels the action buttons', () => {
    expect(noticeAction(notice(1, { action: 'resolve' }))).toEqual({ label: 'Указать итог в боте', strong: true });
    expect(noticeAction(notice(1, { action: 'market' }))).toEqual({ label: 'Открыть рынок', strong: false });
    expect(noticeAction(notice(1, { action: null }))).toBeNull();
    expect(noticeAction(notice(1, { action: 'market', market: null }))).toBeNull();
  });
});

describe('notify settings', () => {
  const all = { results: true, moves: true, closing: true, referral: true, creator: true };

  it('counts switched on kinds', () => {
    expect(notifyCountLabel(all)).toBe('5 из 5');
    expect(notifyCountLabel({ ...all, moves: false })).toBe('4 из 5');
    expect(notifyCountLabel(undefined)).toBe('0 из 5');
  });

  it('toggles one kind', () => {
    expect(toggleNotify(all, 'closing')).toEqual({ ...all, closing: false });
  });
});
