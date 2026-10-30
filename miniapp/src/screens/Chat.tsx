// Closed chat space from docs/mockups/Chat.dc.html (spec E8): its markets, its rating, «Создать рынок для чата».
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, ApiError, type ChatSpace, type MarketCard, type RatingResponse, type Side } from '../api';
import { chatDelta, chatInitials, chatMarketMeta, chatPlace, chatRatingLines, chatSubtitle, isNewMarket } from '../chat';
import { LockIcon } from '../components/ChatTag';
import { CardSkeleton, Skeleton } from '../components/Skeleton';
import { useTradeSheet } from '../components/TradeSheet';
import { formatCompact, formatPercent } from '../format';
import { statusView } from '../market';
import { isTelegram, tg } from '../telegram';
import { applyProbability, mergeMarket, replaceCard } from '../trade';
import './chat.css';
import './rating.css';

type Tab = 'markets' | 'rating';
type SpaceState = 'loading' | 'ready' | 'notfound' | 'error';
type LoadState = 'loading' | 'ready' | 'error';

const TABS: { key: Tab; label: string }[] = [
  { key: 'markets', label: 'Рынки чата' },
  { key: 'rating', label: 'Рейтинг чата' },
];

const errorText = (err: unknown) => (err instanceof ApiError ? err.message : 'Что-то пошло не так. Попробуйте ещё раз.');

export function ChatScreen() {
  const { code = '' } = useParams();
  const navigate = useNavigate();
  const [space, setSpace] = useState<ChatSpace | null>(null);
  const [spaceState, setSpaceState] = useState<SpaceState>('loading');
  const [spaceError, setSpaceError] = useState('');
  const [spaceAttempt, setSpaceAttempt] = useState(0);
  const [tab, setTab] = useState<Tab>('markets');
  const [labels, setLabels] = useState<Map<string, string>>(new Map());

  const [items, setItems] = useState<MarketCard[]>([]);
  const [nextPage, setNextPage] = useState<number | null>(null);
  const [marketsState, setMarketsState] = useState<LoadState>('loading');
  const [marketsError, setMarketsError] = useState('');
  const [marketsAttempt, setMarketsAttempt] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreFailed, setMoreFailed] = useState(false);

  const [rating, setRating] = useState<RatingResponse | null>(null);
  const [ratingState, setRatingState] = useState<LoadState>('loading');
  const [ratingError, setRatingError] = useState('');
  const [ratingAttempt, setRatingAttempt] = useState(0);

  const generation = useRef(0);
  const snapshots = useRef(new Map<number, MarketCard>());
  const sentinel = useRef<HTMLDivElement>(null);

  // Another chat opened in the same screen (a link from a market): start clean.
  useEffect(() => {
    setSpace(null);
    setItems([]);
    setNextPage(null);
    setRating(null);
    setTab('markets');
  }, [code]);

  // The chat itself: checks the membership, 404 for strangers.
  useEffect(() => {
    let cancelled = false;
    setSpaceState('loading');
    api
      .chat(code)
      .then((res) => {
        if (cancelled) return;
        setSpace(res);
        setSpaceState('ready');
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setSpaceState('notfound');
        } else {
          setSpaceError(errorText(err));
          setSpaceState('error');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [code, spaceAttempt]);

  useEffect(() => {
    api
      .categories()
      .then((list) => setLabels(new Map(list.map((c) => [c.slug, c.label]))))
      .catch(() => {});
  }, []);

  const ready = spaceState === 'ready';

  // First page of the chat's markets once the membership is confirmed.
  useEffect(() => {
    if (!ready) return;
    const gen = ++generation.current;
    setMarketsState('loading');
    setMoreFailed(false);
    setLoadingMore(false);
    api
      .chatMarkets(code, 1)
      .then((res) => {
        if (gen !== generation.current) return;
        setItems(res.items);
        setNextPage(res.next_page);
        setMarketsState('ready');
      })
      .catch((err: unknown) => {
        if (gen !== generation.current) return;
        setMarketsError(errorText(err));
        setMarketsState('error');
      });
  }, [code, ready, marketsAttempt]);

  const loadMore = useCallback(() => {
    if (marketsState !== 'ready' || nextPage === null || loadingMore) return;
    const gen = generation.current;
    setLoadingMore(true);
    setMoreFailed(false);
    api
      .chatMarkets(code, nextPage)
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
  }, [code, marketsState, nextPage, loadingMore]);

  useEffect(() => {
    const el = sentinel.current;
    if (!el || tab !== 'markets' || nextPage === null || moreFailed || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && loadMore(), {
      rootMargin: '400px 0px',
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [loadMore, nextPage, moreFailed, tab]);

  // The chat rating: profit on this chat's markets, all time.
  useEffect(() => {
    if (!ready || tab !== 'rating') return;
    let cancelled = false;
    setRatingState('loading');
    api
      .chatRating(code, 'profit', 'all')
      .then((res) => {
        if (cancelled) return;
        setRating(res);
        setRatingState('ready');
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setRatingError(errorText(err));
        setRatingState('error');
      });
    return () => {
      cancelled = true;
    };
  }, [code, ready, tab, ratingAttempt]);

  const { open, sheet, pendingId } = useTradeSheet({
    onOptimistic: (id, prob) =>
      setItems((prev) => {
        const card = prev.find((m) => m.id === id);
        if (!card) return prev;
        if (!snapshots.current.has(id)) snapshots.current.set(id, card);
        return replaceCard(prev, applyProbability(card, prob));
      }),
    onCommit: ({ market }) => {
      snapshots.current.delete(market.id);
      setItems((prev) => {
        const card = prev.find((m) => m.id === market.id);
        return card ? replaceCard(prev, mergeMarket(card, market)) : prev;
      });
    },
    onRollback: (id) => {
      const before = snapshots.current.get(id);
      snapshots.current.delete(id);
      if (before) setItems((prev) => replaceCard(prev, before));
    },
  });

  const pick = (key: Tab) => {
    if (key === tab) return;
    tg.HapticFeedback.selection();
    setTab(key);
  };

  const back = () => {
    if (window.history.state && window.history.state.idx > 0) navigate(-1);
    else navigate('/', { replace: true });
  };

  const createForChat = () => {
    tg.HapticFeedback.impact('light');
    navigate(`/create?chat=${encodeURIComponent(code)}`);
  };

  const lines = useMemo(() => (rating ? chatRatingLines(rating) : []), [rating]);
  const now = new Date();

  const backButton = isTelegram() ? null : (
    <button type="button" className="chat-back" aria-label="Назад к ленте" onClick={back}>
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M15 5l-7 7 7 7" />
      </svg>
    </button>
  );

  if (spaceState === 'notfound' || spaceState === 'error') {
    return (
      <div className="screen chat">
        <header className={`chat-header ${backButton ? '' : 'is-native'}`}>{backButton}</header>
        <div className="chat-message" role="alert">
          <h1 className="screen-title">{spaceState === 'notfound' ? 'Чат не найден' : 'Не удалось открыть чат'}</h1>
          <p className="sub">
            {spaceState === 'notfound'
              ? 'Рынки закрытого чата видят только его участники. Если ты в чате, открой ссылку из закреплённого сообщения бота.'
              : spaceError}
          </p>
          {spaceState === 'error' ? (
            <button type="button" className="button-secondary" onClick={() => setSpaceAttempt((n) => n + 1)}>
              Повторить
            </button>
          ) : (
            <button type="button" className="button-secondary" onClick={() => navigate('/', { replace: true })}>
              К ленте
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="screen chat">
      <header className={`chat-header ${backButton ? '' : 'is-native'}`}>
        {backButton}
        {space ? (
          <>
            <span className="chat-avatar" aria-hidden="true">
              {chatInitials(space.title)}
            </span>
            <div className="chat-heading">
              <h1 className="chat-title">{space.title}</h1>
              <span className="sub chat-subtitle">
                <LockIcon />
                {chatSubtitle(space)}
              </span>
            </div>
          </>
        ) : (
          <>
            <Skeleton width={44} height={44} radius={14} />
            <div className="chat-heading">
              <Skeleton width="60%" height={18} />
              <Skeleton width="45%" height={13} />
            </div>
          </>
        )}
      </header>

      <div className="chat-top">
        <div className="chat-tiles">
          <Tile label="Рынков" value={space ? String(space.markets) : null} />
          <Tile label="Объём" value={space ? formatCompact(space.volume) : null} />
          <Tile label="Ты в чате" value={space ? chatPlace(space.my_rank) : null} />
        </div>
        <div role="tablist" aria-label="Разделы чата" className="chat-tabs">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={t.key === tab}
              className={`chat-tab ${t.key === tab ? 'is-active' : ''}`}
              onClick={() => pick(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="chat-body" role="tabpanel">
        {tab === 'markets' && (
          <>
            {(!ready || marketsState === 'loading') && [0, 1, 2].map((i) => <CardSkeleton key={i} />)}

            {ready && marketsState === 'error' && (
              <div className="feed-message" role="alert">
                <p>Не удалось загрузить рынки. {marketsError}</p>
                <button type="button" className="button-secondary" onClick={() => setMarketsAttempt((n) => n + 1)}>
                  Повторить
                </button>
              </div>
            )}

            {ready && marketsState === 'ready' && items.length === 0 && (
              <div className="feed-message sub">
                {space?.frozen
                  ? 'В этом чате пока нет рынков.'
                  : 'В этом чате пока нет рынков. Задай первый вопрос — его увидят только участники чата.'}
              </div>
            )}

            {ready &&
              marketsState === 'ready' &&
              items.map((m) => (
                <ChatMarket
                  key={m.id}
                  market={m}
                  meta={chatMarketMeta(m, labels.get(m.category) ?? m.category, now)}
                  isNew={isNewMarket(m, now)}
                  pending={pendingId === m.id}
                  onTrade={(side) => void open(m, side)}
                />
              ))}

            {ready && marketsState === 'ready' && nextPage !== null && (
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

            {ready && marketsState === 'ready' && (
              <div className="chat-note sub">
                <LockIcon size={18} strokeWidth={2} />
                <span>
                  Эти рынки видят только участники чата: бот проверяет членство при каждом входе. Выйдешь из чата — доступ
                  закроется, а открытые прогнозы доживут до итога.
                </span>
              </div>
            )}
          </>
        )}

        {tab === 'rating' && (
          <>
            {(!ready || ratingState === 'loading') && (
              <div className="rating-list" aria-busy="true">
                {[0, 1, 2, 3, 4].map((i) => (
                  <div key={i} className="rating-row">
                    <Skeleton width={28} height={28} radius={999} />
                    <Skeleton width="55%" height={16} radius={6} />
                  </div>
                ))}
              </div>
            )}

            {ready && ratingState === 'error' && (
              <div className="feed-message" role="alert">
                <p>Не удалось загрузить рейтинг. {ratingError}</p>
                <button type="button" className="button-secondary" onClick={() => setRatingAttempt((n) => n + 1)}>
                  Повторить
                </button>
              </div>
            )}

            {ready && ratingState === 'ready' && lines.length === 0 && (
              <div className="feed-message sub">
                Рейтинг появится, когда завершится первый рынок чата.
              </div>
            )}

            {ready && ratingState === 'ready' && lines.length > 0 && (
              <ol aria-label="Рейтинг чата" className="rating-list">
                {lines.map((r) => (
                  <li key={r.key} className={`rating-row chat-rating-row ${r.me ? 'is-me' : ''}`}>
                    <span className={`rating-rank ${r.medal ? `is-${r.medal}` : ''}`}>{r.rank}</span>
                    <span className="rating-text">
                      <span className="rating-name">{r.name}</span>
                      <span className="sub rating-sub">{r.sub}</span>
                    </span>
                    <span className="rating-value">{r.value}</span>
                  </li>
                ))}
              </ol>
            )}

            {ready && ratingState === 'ready' && (
              <p className="sub chat-footnote">
                Считается только прибыль на рынках этого чата. Баланс у тебя один на всё приложение.
              </p>
            )}
          </>
        )}
      </div>

      {space && (
        <div className="chat-footer">
          {space.frozen ? (
            <p className="chat-frozen" role="note">
              Бот больше не администратор в этом чате, поэтому новые рынки создать нельзя. Открытые рынки доживут до итога.
            </p>
          ) : (
            <button type="button" className="chat-create" onClick={createForChat}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
                <path d="M12 5v14M5 12h14" />
              </svg>
              Создать рынок для чата
            </button>
          )}
        </div>
      )}

      {sheet}
    </div>
  );
}

function Tile({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="chat-tile">
      <span className="sub chat-tile-label">{label}</span>
      {value === null ? <Skeleton width="60%" height={20} /> : <span className="chat-tile-value">{value}</span>}
    </div>
  );
}

function ChatMarket({
  market,
  meta,
  isNew,
  pending,
  onTrade,
}: {
  market: MarketCard;
  meta: string;
  isNew: boolean;
  pending: boolean;
  onTrade: (side: Side) => void;
}) {
  const delta = chatDelta(market.change_24h);
  const open = market.status === 'open';
  return (
    <article className="chat-market" aria-busy={pending || undefined}>
      <div className="sub chat-market-meta">
        <span>{meta}</span>
        {isNew && <span className="chat-new">новый</span>}
      </div>
      <h2 className="chat-market-title">
        <Link to={`/market/${market.id}`} className="stretched-link">
          {market.title}
        </Link>
      </h2>
      <div className="chat-market-row">
        <div className="chat-market-prob">
          <span className="chat-market-pct">{formatPercent(market.prob)}</span>
          <span className={`chat-market-delta ${delta.up ? 'is-up' : 'is-down'}`}>{delta.text}</span>
        </div>
        {open ? (
          <div className="chat-market-actions">
            <button type="button" className="chat-side is-yes" disabled={pending} onClick={() => onTrade('yes')}>
              ДА
            </button>
            <button type="button" className="chat-side is-no" disabled={pending} onClick={() => onTrade('no')}>
              НЕТ
            </button>
          </div>
        ) : (
          <span className="sub chat-market-status">{statusView(market.status).label}</span>
        )}
      </div>
    </article>
  );
}
