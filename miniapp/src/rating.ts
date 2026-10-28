// Pure helpers of the rating screen (docs/mockups/Rating.dc.html).
import type { RatingResponse } from './api';
import { formatSigned } from './format';

export type RatingScope = 'all' | 'friends';
export type RatingBy = 'profit' | 'accuracy';
export type RatingPeriod = 'week' | 'month' | 'all';

/** Profit "123.45" -> "+123", accuracy share "0.6300" -> "63%". */
export function formatRatingValue(value: string, by: RatingBy): string {
  const num = Number(value);
  if (!Number.isFinite(num)) return '—';
  if (by === 'accuracy') return Math.round(num * 100) + '%';
  return formatSigned(num, 0);
}

export type Medal = 'gold' | 'silver' | 'bronze';

export function medalOf(rank: number): Medal | null {
  return rank === 1 ? 'gold' : rank === 2 ? 'silver' : rank === 3 ? 'bronze' : null;
}

/** One or two capital letters for an avatar without a photo: "Саша М." -> "СМ". */
export function initials(name: string): string {
  const words = name
    .split(/\s+/)
    .map((w) => w.replace(/[^\p{L}\p{N}]/gu, ''))
    .filter(Boolean);
  if (words.length === 0) return '?';
  return words
    .slice(0, 2)
    .map((w) => w.charAt(0).toUpperCase())
    .join('');
}

/** Among friends the list always contains me; with nobody else it is "empty". */
export function hasOthers(rating: RatingResponse): boolean {
  return rating.items.some((item) => !item.is_me);
}
