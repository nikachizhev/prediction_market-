import type { MarketSort } from './api';

const SORTS: MarketSort[] = ['recommended', 'hot', 'new', 'soon'];

export function readFeedFilters(params: URLSearchParams) {
  const value = params.get('sort') as MarketSort;
  return {
    sort: SORTS.includes(value) ? value : 'recommended' as MarketSort,
    category: params.get('category') ?? '',
    query: params.get('q') ?? '',
  };
}

export function updateFeedFilter(params: URLSearchParams, key: 'sort' | 'category' | 'q', value: string) {
  const next = new URLSearchParams(params);
  if (!value || (key === 'sort' && value === 'recommended')) next.delete(key);
  else next.set(key, value);
  return next;
}
