// Feed screen from docs/mockups/Main.dc.html.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError, type Category, type MarketCard, type MarketSort, type Position, type Side } from '../api';
import { cascadeDelay } from '../chartMotion';
import { BalancePill } from '../components/BalancePill';
import { NotificationBell } from '../components/NotificationBell';
import { MarketCardView } from '../components/MarketCardView';
import { CardSkeleton } from '../components/Skeleton';
import { useToast } from '../components/Toast';
import { useTradeSheet } from '../components/TradeSheet';
import { pluralize } from '../format';
import { tg } from '../telegram';
import { applyProbability, mergeMarket, replaceCard } from '../trade';

const SORTS: { key: MarketSort; label: string }[] = [
  { key: 'hot', label: 'Горячие' },
  { key: 'new', label: 'Новые' },
  { key: 'soon', label: 'Скоро итог' },
];

const SEARCH_DEBOUNCE_MS = 300;

type LoadState = 'loading' | 'ready' | 'error';

export function FeedScreen() {
  const toast = useToast();
  const [categories, setCategories] = useState<Category[]>([]);
  const [category, setCategory] = useState('');
  const [sort, setSort] = useState<MarketSort>('hot');
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');

  const [items, setItems] = useState<MarketCard[]>([]);
  const [nextPage, setNextPage] = useState<number | null>(null);
  const [state, setState] = useState<LoadState>('loading');
  const [error, setError] = useState('');
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreFailed, setMoreFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [positions, setPositions] = useState<Record<number, Position>>({});

  const generation = useRef(0);
  const snapshots = useRef(new Map<number, MarketCard>());
  const sentinel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api
      .categories()
      .then(setCategories)
      .catch(() => {});
  }, []);

  useEffect(() => {
    const id = window.setTimeout(() => setSearch(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [query]);

  // First page whenever filters change.
  useEffect(() => {
    const gen = ++generation.current;
    setState('loading');
    setMoreFailed(false);
    setLoadingMore(false);
    api
      .markets({ category, q: search, sort, page: 1 })
      .then((res) => {
        if (gen !== generation.current) return;
        setItems(res.items);
        setNextPage(res.next_page);
        setState('ready');
      })
      .catch((err: unknown) => {
        if (gen !== generation.current) return;
        setError(err instanceof ApiError ? err.message : 'Что-то пошло не так. Попробуйте ещё раз.');
        setState('error');
      });
  }, [category, search, sort, attempt]);

  const loadMore = useCallback(() => {
    if (state !== 'ready' || nextPage === null || loadingMore) return;
    const gen = generation.current;
    setLoadingMore(true);
    setMoreFailed(false);
    api
      .markets({ category, q: search, sort, page: nextPage })
      .then((res) => {
        if (gen !== generation.current) return;
        setItems((prev) => {
          const seen = new Set(prev.map((m) => m.id));
          return [...prev, ...res.items.filter((m) => !seen.has(m.id))];
        });
        setNextPage(res.next_page);
      })
      .catch(() => {
        if (gen === generation.current) setMoreFailed(true);
      })
      .finally(() => {
        if (gen === generation.current) setLoadingMore(false);
      });
  }, [state, nextPage, loadingMore, category, search, sort]);

  useEffect(() => {
    const el = sentinel.current;
    if (!el || nextPage === null || moreFailed || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && loadMore(), {
      rootMargin: '400px 0px',
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [loadMore, nextPage, moreFailed]);

  const { open, sheet, pendingId } = useTradeSheet({
    onOptimistic: (id, prob) =>
      setItems((prev) => {
        const card = prev.find((m) => m.id === id);
        if (card) {
          if (!snapshots.current.has(id)) snapshots.current.set(id, card);
          return replaceCard(prev, applyProbability(card, prob));
        }
        return prev.map((m) =>
          m.submarkets?.some((s) => s.id === id)
            ? { ...m, submarkets: m.submarkets.map((s) => (s.id === id ? applyProbability(s, prob) : s)) }
            : m,
        );
      }),
    onCommit: ({ market }) => {
      snapshots.current.delete(market.id);
      setItems((prev) => {
        const card = prev.find((m) => m.id === market.id);
        if (card) return replaceCard(prev, mergeMarket(card, market));
        return prev.map((m) =>
          m.submarkets?.some((s) => s.id === market.id)
            ? { ...m, submarkets: m.submarkets.map((s) => (s.id === market.id ? { ...s, prob: market.prob } : s)) }
            : m,
        );
      });
      if (market.my_position) setPositions((prev) => ({ ...prev, [market.id]: market.my_position! }));
    },
    onRollback: (id) => {
      const before = snapshots.current.get(id);
      snapshots.current.delete(id);
      if (before) setItems((prev) => replaceCard(prev, before));
    },
  });

  const onTrade = useCallback((market: MarketCard, side: Side) => void open(market, side), [open]);

  const onStar = useCallback(
    (market: MarketCard) => {
      const starred = !market.starred;
      tg.HapticFeedback.selection();
      const flip = (value: boolean) =>
        setItems((prev) => prev.map((m) => (m.id === market.id ? { ...m, starred: value } : m)));
      flip(starred);
      api.star(market.id, starred).catch((err: unknown) => {
        flip(!starred);
        toast(err instanceof ApiError ? err.message : 'Не получилось. Попробуйте ещё раз.', 'error');
      });
    },
    [toast],
  );

  const labels = useMemo(() => new Map(categories.map((c) => [c.slug, c.label])), [categories]);
  const pickCategory = (slug: string) => {
    if (slug === category) return;
    tg.HapticFeedback.selection();
    setCategory(slug);
  };
  const pickSort = (key: MarketSort) => {
    if (key === sort) return;
    tg.HapticFeedback.selection();
    setSort(key);
  };

  const chips = [{ slug: '', label: 'Все' }, ...categories];

  return (
    <div className="screen feed">
      <header className="screen-header">
        <BalancePill />
        <NotificationBell />
      </header>

      <div className="feed-search">
        <label htmlFor="feed-search" className="visually-hidden">
          Поиск рынков
        </label>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--sub)" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <circle cx="11" cy="11" r="6.5" />
          <path d="M20 20l-4.2-4.2" />
        </svg>
        <input
          id="feed-search"
          type="search"
          placeholder="Поиск рынков"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          enterKeyHint="search"
          autoComplete="off"
        />
      </div>

      <nav aria-label="Категории" className="chips-row">
        {chips.map((c) => (
          <button
            key={c.slug || 'all'}
            type="button"
            className={`chip ${c.slug === category ? 'is-active' : ''}`}
            aria-pressed={c.slug === category}
            onClick={() => pickCategory(c.slug)}
          >
            {c.label}
          </button>
        ))}
      </nav>

      <div className="sort-row">
        <div className="sort-tabs">
          {SORTS.map((o) => (
            <button
              key={o.key}
              type="button"
              className={`sort-tab ${o.key === sort ? 'is-active' : ''}`}
              aria-pressed={o.key === sort}
              onClick={() => pickSort(o.key)}
            >
              {o.label}
            </button>
          ))}
        </div>
        {state === 'ready' && nextPage === null && items.length > 0 && (
          <span className="sub sort-count">{pluralize(items.length, 'рынок', 'рынка', 'рынков')}</span>
        )}
      </div>

      <div className="screen-body">
        {state === 'loading' && [0, 1, 2].map((i) => <CardSkeleton key={i} />)}

        {state === 'error' && (
          <div className="feed-message" role="alert">
            <p>Не удалось загрузить рынки. {error}</p>
            <button type="button" className="button-secondary" onClick={() => setAttempt((n) => n + 1)}>
              Повторить
            </button>
          </div>
        )}

        {state === 'ready' && items.length === 0 && (
          <div className="feed-message sub">Ничего не нашли. Попробуй другую категорию или запрос.</div>
        )}

        {state === 'ready' &&
          items.map((m, i) => (
            <MarketCardView
              key={m.id}
              market={m}
              categoryLabel={labels.get(m.category) ?? m.category}
              position={positions[m.id]}
              pending={pendingId === m.id}
              drawDelay={cascadeDelay(i)}
              onTrade={onTrade}
              onStar={onStar}
            />
          ))}

        {state === 'ready' && nextPage !== null && (
          <div ref={sentinel}>
            {moreFailed ? (
              <div className="feed-message">
                <p className="sub">Не удалось загрузить ещё.</p>
                <button type="button" className="button-secondary" onClick={loadMore}>
                  Повторить
                </button>
              </div>
            ) : (
              <CardSkeleton />
            )}
          </div>
        )}
      </div>

      {sheet}
    </div>
  );
}
