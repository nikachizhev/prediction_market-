// Pure helpers of «Уведомления» and their settings (docs/mockups/Notifications.dc.html, Profile.dc.html).
import type { Notice, NoticeFilter, NotifyKind, NotifySettings } from './api';
import { dayLabel } from './format';

// ---- Inbox ----

/** Filter chips; «Чаты» is phase 3. */
export const NOTICE_FILTERS: { key: NoticeFilter; label: string }[] = [
  { key: 'all', label: 'Все' },
  { key: 'mine', label: 'Мои рынки' },
  { key: 'pay', label: 'Выплаты' },
  { key: 'markets', label: 'Слежу' },
];

export function filterNotices(items: Notice[], filter: NoticeFilter): Notice[] {
  return filter === 'all' ? items : items.filter((n) => n.group === filter);
}

export interface NoticeDay {
  title: string;
  items: Notice[];
}

/** Groups notices by local calendar day ("Сегодня", "Вчера", "25 октября"), newest first. */
export function groupNoticesByDay(items: Notice[], now: Date = new Date()): NoticeDay[] {
  const sorted = [...items].sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  const days: NoticeDay[] = [];
  for (const n of sorted) {
    const title = dayLabel(n.at, now);
    const last = days[days.length - 1];
    if (last && last.title === title) last.items.push(n);
    else days.push({ title, items: [n] });
  }
  return days;
}

export type NoticeIcon = 'win' | 'move' | 'task' | 'people' | 'coin';

/** Icon from the mockup: win/move on a blue tint, task/coin on gold, people neutral. */
export function noticeIcon(n: Pick<Notice, 'kind' | 'group' | 'action'>): NoticeIcon {
  if (n.action === 'resolve') return 'task';
  switch (n.kind) {
    case 'results':
      return 'win';
    case 'moves':
      return 'move';
    case 'closing':
      return 'task';
    case 'referral':
      return 'people';
    case 'creator':
      return 'move';
    default:
      return n.group === 'pay' ? 'coin' : n.group === 'mine' ? 'task' : 'move';
  }
}

export interface NoticeActionView {
  label: string;
  /** Dark filled button («Указать итог в боте») vs neutral. */
  strong: boolean;
}

export function noticeAction(n: Pick<Notice, 'action' | 'market'>): NoticeActionView | null {
  if (!n.market) return null;
  if (n.action === 'resolve') return { label: 'Указать итог в боте', strong: true };
  if (n.action === 'market') return { label: 'Открыть рынок', strong: false };
  return null;
}

/** Text of the bell badge: nothing at zero, "99+" above 99. */
export function unreadBadge(count: number | null | undefined): string | null {
  if (!count || count <= 0) return null;
  return count > 99 ? '99+' : String(count);
}

/** Marks `ids` (or everything, without ids) as read. */
export function markNoticesRead(items: Notice[], ids?: number[]): Notice[] {
  const set = ids ? new Set(ids) : null;
  return items.map((n) => (!n.read && (!set || set.has(n.id)) ? { ...n, read: true } : n));
}

export const countUnread = (items: Notice[]): number => items.filter((n) => !n.read).length;

// ---- Settings ----

export const NOTIFY_OPTIONS: { key: NotifyKind; label: string; hint: string }[] = [
  { key: 'results', label: 'Итоги и выплаты', hint: 'Чем закончился рынок и сколько ты получил' },
  { key: 'moves', label: 'Сдвиги вероятности', hint: 'Шанс на отслеживаемом рынке сдвинулся на 10+ п.п.' },
  { key: 'closing', label: 'Скоро конец приёма прогнозов', hint: 'За сутки до закрытия рынка, где ты участвуешь' },
  { key: 'referral', label: 'Награды за друзей', hint: 'Друг сделал 5 прогнозов — монеты вам обоим' },
  { key: 'creator', label: 'Мои рынки', hint: 'Споры и решения модератора по твоим рынкам' },
];

export const NOTIFY_NOTE = 'Не больше 5 сообщений в день, ночью (23:00–09:00 МСК) бот молчит.';

/** «4 из 5». */
export function notifyCountLabel(notify: Partial<NotifySettings> | null | undefined): string {
  const on = NOTIFY_OPTIONS.filter((o) => notify?.[o.key]).length;
  return `${on} из ${NOTIFY_OPTIONS.length}`;
}

export function toggleNotify(notify: NotifySettings, key: NotifyKind): NotifySettings {
  return { ...notify, [key]: !notify[key] };
}
