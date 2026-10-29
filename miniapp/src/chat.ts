// Pure helpers of the closed chat screen (docs/mockups/Chat.dc.html, spec E8).
import type { ChatSpace, MarketCard, RatingResponse } from './api';
import { formatEnds, formatSigned, pluralize } from './format';
import { initials, medalOf, type Medal } from './rating';

const DAY_MS = 24 * 60 * 60 * 1000;

/** «новый»: the market was created less than a day ago. */
export function isNewMarket(card: Pick<MarketCard, 'created_at'>, now: Date = new Date()): boolean {
  if (!card.created_at) return false;
  const created = Date.parse(card.created_at);
  if (Number.isNaN(created)) return false;
  const age = now.getTime() - created;
  return age >= 0 && age < DAY_MS;
}

/** «Ты в чате»: «3 место», or «—» without resolved predictions in the chat. */
export function chatPlace(rank: number | null | undefined): string {
  return typeof rank === 'number' && rank > 0 ? `${rank} место` : '—';
}

/** «закрытый чат · 28 участников». */
export function chatSubtitle(space: Pick<ChatSpace, 'members'>): string {
  return `закрытый чат · ${pluralize(space.members, 'участник', 'участника', 'участников')}`;
}

/** Up to two letters for the chat's square: «Группа ПИ-24» -> «ПИ». */
export function chatInitials(title: string): string {
  const words = title
    .split(/\s+/)
    .map((w) => w.replace(/[^\p{L}\p{N}]/gu, ''))
    .filter(Boolean);
  // «Группа ПИ-24»: a word in capitals names the chat better than the generic first word.
  const caps = words.find((w) => w.length >= 2 && /^\p{Lu}{2,}/u.test(w));
  if (caps) return caps.slice(0, 2);
  return initials(title);
}

/** «Учёба · ещё 4 дня · 19 участников». */
export function chatMarketMeta(card: MarketCard, categoryLabel: string, now: Date = new Date()): string {
  return [categoryLabel, formatEnds(card.closes_at, now), pluralize(card.participants, 'участник', 'участника', 'участников')]
    .filter(Boolean)
    .join(' · ');
}

/** Change over a day as in the chat mockup: «▲ 12», «▼ 6». */
export function chatDelta(change24h: number): { text: string; up: boolean } {
  const delta = Math.round(change24h);
  const up = delta >= 0;
  return { text: `${up ? '▲' : '▼'} ${Math.abs(delta)}`, up };
}

export interface ChatRatingLine {
  key: string;
  rank: number;
  medal: Medal | null;
  /** «Капибара (ты)» for my own row. */
  name: string;
  /** «9 прогнозов в чате · точность 78%». */
  sub: string;
  /** «+410», «−38». */
  value: string;
  me: boolean;
}

/** Rows of the chat rating (by profit): medals for the top 3, my row marked. */
export function chatRatingLines(rating: Pick<RatingResponse, 'items'>): ChatRatingLine[] {
  return rating.items.map((item) => {
    const count = pluralize(item.predictions, 'прогноз', 'прогноза', 'прогнозов') + ' в чате';
    return {
      key: `${item.rank}-${item.display_name}`,
      rank: item.rank,
      medal: medalOf(item.rank),
      name: item.is_me ? `${item.display_name} (ты)` : item.display_name,
      sub: item.accuracy === null ? count : `${count} · точность ${Math.round(item.accuracy * 100)}%`,
      value: formatSigned(Math.round(Number(item.profit)), 0),
      me: item.is_me,
    };
  });
}
