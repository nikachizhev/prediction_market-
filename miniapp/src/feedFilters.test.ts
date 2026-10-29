import { describe, expect, it } from 'vitest';
import { readFeedFilters, updateFeedFilter } from './feedFilters';

describe('feed history filters', () => {
  it('restores New, category and search from the previous history URL', () => {
    let params = new URLSearchParams();
    params = updateFeedFilter(params, 'sort', 'new');
    params = updateFeedFilter(params, 'category', 'tech');
    params = updateFeedFilter(params, 'q', 'Новый релиз');
    const previousUrl = `/app/?${params}`;
    expect(readFeedFilters(new URL(previousUrl, 'https://example.test').searchParams)).toEqual({
      sort: 'new', category: 'tech', query: 'Новый релиз',
    });
  });

  it('changing one filter preserves the others and does not mutate history parameters', () => {
    const original = new URLSearchParams('sort=hot&category=games&q=финал');
    const next = updateFeedFilter(original, 'category', '');
    expect(readFeedFilters(next)).toEqual({ sort: 'hot', category: '', query: 'финал' });
    expect(original.get('category')).toBe('games');
  });

  it('uses For You only for a new feed or an unknown sort', () => {
    expect(readFeedFilters(new URLSearchParams()).sort).toBe('recommended');
    expect(readFeedFilters(new URLSearchParams('sort=unknown')).sort).toBe('recommended');
    for (const sort of ['hot', 'new', 'soon']) {
      expect(readFeedFilters(new URLSearchParams(`sort=${sort}`)).sort).toBe(sort);
    }
    expect(updateFeedFilter(new URLSearchParams('sort=new'), 'sort', 'recommended').has('sort')).toBe(false);
  });
});
