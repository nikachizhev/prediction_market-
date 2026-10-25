// «Мои» from docs/mockups/Mine.dc.html: positions, watched markets, markets I created.
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, ApiError, type MarketCard, type MineResponse, type Side } from '../api';
import { useSell } from '../components/SellSheet';
import { CardSkeleton } from '../components/Skeleton';
import { useToast } from '../components/Toast';
import { formatEnds, formatMoney, formatPercent, formatSigned } from '../format';
import { canSell, createdStatusLine, heldSides, positionAfterSell, positionStats, positionsTotals } from '../market';
import { tg } from '../telegram';
import { mergeMarket, sideLabel } from '../trade';
import './mine.css';

type Tab = 'positions' | 'watch' | 'created';
type LoadState = 'loading' | 'ready' | 'error';
type Held = MineResponse['positions'][number];

export function MineScreen() {
  const toast = useToast();
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>('positions');
  const [data, setData] = useState<MineResponse | null>(null);
  const [state, setState] = useState<LoadState>('loading');
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const snapshots = useRef(new Map<number, Held>());

  useEffect(() => {
    let cancelled = false;
    setState('loading');
    api
      .mine()
      .then((res) => {
        if (cancelled) return;
        setData(res);
        setState('ready');
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : 'Что-то пошло не так. Попробуйте ещё раз.');
        setState('error');
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const updatePositions = (fn: (items: Held[]) => Held[]) =>
    setData((prev) => (prev ? { ...prev, positions: fn(prev.positions) } : prev));

  const sell = useSell({
    onOptimistic: (id, side, proceeds) =>
      updatePositions((items) =>
        items.map((item) => {
          if (item.id !== id) return item;
          if (!snapshots.current.has(id)) snapshots.current.set(id, item);
          const after = positionAfterSell(item.position, side, proceeds);
          // Fully sold positions stay in place, dimmed, until the server confirms.
          return after ? { ...item, position: after } : { ...item, position: { ...item.position, yes_shares: '0', no_shares: '0' } };
        }),
      ),
    onCommit: ({ market }) => {
      snapshots.current.delete(market.id);
      updatePositions((items) =>
        items.flatMap((item) => {
          if (item.id !== market.id) return [item];
          const position = market.my_position;
          if (!position || heldSides(position).length === 0) return [];
          return [{ ...mergeMarket(item, market), position }];
        }),
      );
    },
    onRollback: (id) => {
      const before = snapshots.current.get(id);
      snapshots.current.delete(id);
      if (before) updatePositions((items) => items.map((item) => (item.id === id ? before : item)));
    },
  });

  const toggleStar = useCallback(
    (market: MarketCard) => {
      const starred = !market.starred;
      tg.HapticFeedback.selection();
      const flip = (value: boolean) =>
        setData((prev) =>
          prev ? { ...prev, watched: prev.watched.map((m) => (m.id === market.id ? { ...m, starred: value } : m)) } : prev,
        );
      flip(starred);
      api.star(market.id, starred).catch((err: unknown) => {
        flip(!starred);
        toast(err instanceof ApiError ? err.message : 'Не получилось. Попробуйте ещё раз.', 'error');
      });
    },
    [toast],
  );

  const openResolve = (id: number, link: string) => {
    tg.HapticFeedback.impact('light');
    tg.openTelegramLink(link);
    api.event('open_bot', { market: id, reason: 'resolve' }).catch(() => {});
  };

  const pick = (key: Tab) => {
    if (key === tab) return;
    tg.HapticFeedback.selection();
    setTab(key);
  };

  const count = (n: number | undefined) => (state === 'ready' && n !== undefined ? ` · ${n}` : '');
  const tabs: { key: Tab; label: string }[] = [
    { key: 'positions', label: 'Позиции' + count(data?.positions.length) },
    { key: 'watch', label: 'Слежу' + count(data?.watched.length) },
    { key: 'created', label: 'Мои рынки' + count(data?.created.length) },
  ];

  return (
    <div className="screen mine">
      <header className="mine-header">
        <h1 className="mine-title">Мои</h1>
        <div role="tablist" aria-label="Разделы" className="mine-tabs">
          {tabs.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={t.key === tab}
              className={`mine-tab ${t.key === tab ? 'is-active' : ''}`}
              onClick={() => pick(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </header>

      <div className="mine-body" role="tabpanel">
        {state === 'loading' && [0, 1, 2].map((i) => <CardSkeleton key={i} />)}

        {state === 'error' && (
          <div className="feed-message" role="alert">
            <p>Не удалось загрузить. {error}</p>
            <button type="button" className="button-secondary" onClick={() => setAttempt((n) => n + 1)}>
              Повторить
            </button>
          </div>
        )}

        {state === 'ready' && data && tab === 'positions' && (
          <Positions items={data.positions} sellingId={sell.sellingId} onSell={(item, side) =>
            sell.request({ id: item.id, title: item.title, prob: item.prob, position: item.position }, side)
          } onEmpty={() => navigate('/')} />
        )}

        {state === 'ready' && data && tab === 'watch' && <Watched items={data.watched} onStar={toggleStar} />}

        {state === 'ready' && data && tab === 'created' && (
          <Created items={data.created} onResolve={openResolve} onEmpty={() => navigate('/create')} />
        )}
      </div>

      {sell.sheet}
    </div>
  );
}

function Empty({ text, action, onAction }: { text: string; action: string; onAction: () => void }) {
  return (
    <div className="mine-empty">
      <p className="sub">{text}</p>
      <button type="button" className="button-secondary" onClick={onAction}>
        {action}
      </button>
    </div>
  );
}

function Positions({
  items,
  sellingId,
  onSell,
  onEmpty,
}: {
  items: Held[];
  sellingId: number | null;
  onSell: (item: Held, side: Side) => void;
  onEmpty: () => void;
}) {
  if (items.length === 0) {
    return <Empty text="Пока нет открытых позиций. Сделай первый прогноз в ленте." action="К ленте" onAction={onEmpty} />;
  }
  const totals = positionsTotals(items.map((i) => i.position));
  return (
    <>
      <section aria-label="Итог по позициям" className="mine-card mine-figures is-summary">
        <Figure label="Вложено" value={formatMoney(totals.spent, 0)} big />
        <Figure label="Оценка сейчас" value={formatMoney(totals.value, 0)} big />
        <Figure label="Если всё угадаю" value={formatMoney(Math.floor(totals.payout), 0)} big />
      </section>
      {items.map((item) => {
        const stats = positionStats(item.position);
        const sides = stats.sides;
        const diff = Math.round(stats.diff);
        const sellable = canSell(item) && sides.length > 0;
        return (
          <article key={item.id} className="mine-card mine-position" aria-busy={sellingId === item.id || undefined}>
            <Link to={`/market/${item.id}`} className="mine-q stretched-link">
              {item.title}
            </Link>
            <div className="mine-row">
              <span className="mine-chips">
                {sides.length === 0 ? (
                  <span className="mine-chip">Продаём…</span>
                ) : (
                  sides.map((s) => (
                    <span key={s} className={`mine-chip is-${s}`}>
                      {sideLabel(s)}
                    </span>
                  ))
                )}
              </span>
              <span className="sub small">сейчас ДА {formatPercent(item.prob)}</span>
            </div>
            <div className="mine-figures">
              <Figure label="Вложено" value={formatMoney(stats.spent, 0)} />
              <Figure
                label="Стоит сейчас"
                value={formatMoney(stats.value, 0)}
                extra={<span className={`mine-diff ${diff >= 0 ? 'is-up' : 'is-down'}`}>{formatSigned(diff, 0)}</span>}
              />
              <Figure label="Если угадаю" value={formatMoney(Math.floor(stats.payout), 0)} />
            </div>
            {sellable && (
              <div className={`mine-sell ${sides.length > 1 ? 'is-two' : ''}`}>
                {sides.map((s) => (
                  <button
                    key={s}
                    type="button"
                    className="mine-sell-button"
                    disabled={sellingId === item.id}
                    onClick={() => onSell(item, s)}
                  >
                    {sides.length > 1 ? `Продать ${sideLabel(s)}` : 'Продать'}
                  </button>
                ))}
              </div>
            )}
          </article>
        );
      })}
    </>
  );
}

function Figure({ label, value, extra, big = false }: { label: string; value: string; extra?: ReactNode; big?: boolean }) {
  return (
    <div className="mine-figure">
      <span className="sub small">{label}</span>
      <span className={big ? 'mine-figure-big' : 'mine-figure-value'}>
        {value}
        {extra && <> {extra}</>}
      </span>
    </div>
  );
}

function Watched({ items, onStar }: { items: MarketCard[]; onStar: (m: MarketCard) => void }) {
  if (items.length === 0) {
    return (
      <div className="mine-empty">
        <p className="sub">Нажми ★ на карточке рынка, чтобы следить за ним. Рынки, где у тебя есть позиция, попадают сюда сами.</p>
      </div>
    );
  }
  return (
    <>
      <section aria-label="Отслеживаемые рынки" className="mine-card mine-watch">
        {items.map((m) => {
          const delta = Math.round(m.change_24h);
          return (
            <div key={m.id} className="mine-watch-row">
              <Link to={`/market/${m.id}`} className="mine-watch-link">
                <span className="mine-watch-q">{m.title}</span>
                <span className="sub small">
                  <strong className="mine-watch-pct">{formatPercent(m.prob)}</strong> ·{' '}
                  <span className={`mine-delta ${delta >= 0 ? 'is-up' : 'is-down'}`}>
                    {delta >= 0 ? '▲' : '▼'} {Math.abs(delta)} за сутки
                  </span>
                </span>
              </Link>
              <button
                type="button"
                className="star-button mine-star"
                aria-pressed={m.starred}
                aria-label={m.starred ? 'Не следить за рынком' : 'Следить за рынком'}
                onClick={() => onStar(m)}
              >
                <svg
                  width="22"
                  height="22"
                  viewBox="0 0 24 24"
                  fill={m.starred ? 'var(--gold)' : 'none'}
                  stroke={m.starred ? 'var(--gold)' : 'var(--sub)'}
                  strokeWidth="1.8"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" />
                </svg>
              </button>
            </div>
          );
        })}
      </section>
      <p className="mine-note sub">Этот же список приходит в боте по команде «Мои рынки» — с анимацией графика.</p>
    </>
  );
}

function Created({
  items,
  onResolve,
  onEmpty,
}: {
  items: MineResponse['created'];
  onResolve: (id: number, link: string) => void;
  onEmpty: () => void;
}) {
  if (items.length === 0) {
    return <Empty text="Ты ещё не создал ни одного рынка. Задай свой вопрос — и получай половину комиссии." action="Создать рынок" onAction={onEmpty} />;
  }
  const urgent = items.filter((m) => m.needs_resolution);
  const rest = items.filter((m) => !m.needs_resolution);
  return (
    <>
      {urgent.map((m) => (
        <section key={m.id} aria-label="Нужно указать итог" className="mine-card mine-urgent">
          <span className="mine-urgent-label">НУЖНО УКАЗАТЬ ИТОГ</span>
          <Link to={`/market/${m.id}`} className="mine-q">
            {m.title}
          </Link>
          <span className="sub small">{createdStatusLine(m, formatEnds(m.closes_at))}</span>
          <button type="button" className="mine-resolve" onClick={() => onResolve(m.id, m.resolve_link)}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M21 4L3 11l6 2.5L11.5 20 21 4z" />
              <path d="M9 13.5L21 4" />
            </svg>
            Указать итог в боте
          </button>
        </section>
      ))}
      {rest.map((m) => (
        <Link key={m.id} to={`/market/${m.id}`} className="mine-card mine-created">
          <span className="mine-q">{m.title}</span>
          <span className="mine-row small">
            <span className="sub">{createdStatusLine(m, formatEnds(m.closes_at))}</span>
            <span className="mine-fee">комиссия {formatSigned(Math.floor(Number(m.fee_expected)), 0)}</span>
          </span>
        </Link>
      ))}
      <p className="mine-note sub">Половина комиссии со всех сделок на твоём рынке достаётся тебе.</p>
    </>
  );
}
