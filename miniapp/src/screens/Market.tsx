// Market card from docs/mockups/Market.dc.html: open / waiting / review / finished states.
import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, ApiError, type HistoryPeriod, type MarketDetail, type Side } from '../api';
import { lineYAt } from '../chartMotion';
import { AnimatedNumber } from '../components/AnimatedNumber';
import { BottomSheet } from '../components/BottomSheet';
import { ChatTag } from '../components/ChatTag';
import { LiveLine } from '../components/LiveLine';
import { useSell } from '../components/SellSheet';
import { Skeleton } from '../components/Skeleton';
import { useToast } from '../components/Toast';
import { useTradeSheet } from '../components/TradeSheet';
import { formatCompact, formatDayMonth, formatEnds, formatMoney, formatPercent, formatSigned, plural, pluralize } from '../format';
import {
  botChatLink,
  buildChart,
  canSell,
  changeLabel,
  CHART,
  chartY,
  datesLine,
  heldSides,
  initials,
  outcomeLabel,
  periodChange,
  periodWord,
  PERIODS,
  pointIndexAt,
  positionAfterSell,
  positionStats,
  settledResult,
  sharesLabel,
  sharesOf,
  statusView,
  timeAgo,
  timeLeft,
  topMovesView,
  untilResult,
  type HistoryPoint,
} from '../market';
import { shareMarket } from '../links';
import { useSession } from '../session';
import { isTelegram, tg } from '../telegram';
import { applyProbability, sideLabel, splitPercents } from '../trade';
import './market.css';

type LoadState = 'loading' | 'ready' | 'notfound' | 'error';

export function MarketScreen() {
  const { id } = useParams();
  const marketId = Number(id);
  const navigate = useNavigate();
  const toast = useToast();
  const { me } = useSession();

  const [market, setMarket] = useState<MarketDetail | null>(null);
  const [state, setState] = useState<LoadState>('loading');
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [categoryLabel, setCategoryLabel] = useState('');
  const [period, setPeriod] = useState<HistoryPeriod>('w');
  const [history, setHistory] = useState<{ period: HistoryPeriod; points: HistoryPoint[] } | null>(null);
  const [historyKey, setHistoryKey] = useState(0);
  const [reportOpen, setReportOpen] = useState(false);

  const marketRef = useRef<MarketDetail | null>(null);
  marketRef.current = market;
  const snapshot = useRef<MarketDetail | null>(null);

  useEffect(() => {
    if (!Number.isInteger(marketId) || marketId <= 0) {
      setState('notfound');
      return;
    }
    let cancelled = false;
    setState('loading');
    api
      .market(marketId)
      .then((m) => {
        if (cancelled) return;
        setMarket(m);
        setState('ready');
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setState('notfound');
        } else {
          setError(err instanceof ApiError ? err.message : 'Что-то пошло не так. Попробуйте ещё раз.');
          setState('error');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [marketId, attempt]);

  useEffect(() => {
    if (state !== 'ready') return;
    let cancelled = false;
    api
      .history(marketId, period)
      .then((res) => !cancelled && setHistory({ period, points: res.points }))
      .catch(() => !cancelled && setHistory({ period, points: [] }));
    return () => {
      cancelled = true;
    };
  }, [marketId, period, historyKey, state]);

  const category = market?.category;
  useEffect(() => {
    if (!category) return;
    let cancelled = false;
    api
      .categories()
      .then((list) => !cancelled && setCategoryLabel(list.find((c) => c.slug === category)?.label ?? ''))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [category]);

  const remember = () => {
    if (!snapshot.current) snapshot.current = marketRef.current;
  };
  const restore = () => {
    if (snapshot.current) setMarket(snapshot.current);
    snapshot.current = null;
  };
  const commit = (fresh: MarketDetail) => {
    snapshot.current = null;
    setMarket(fresh);
    setHistoryKey((k) => k + 1);
  };

  const trade = useTradeSheet({
    onOptimistic: (_id, prob) => {
      remember();
      setMarket((m) => (m ? applyProbability(m, prob) : m));
    },
    onCommit: (result) => commit(result.market),
    onRollback: restore,
  });

  const sell = useSell({
    onOptimistic: (_id, side, proceeds) => {
      remember();
      setMarket((m) => (m && m.my_position ? { ...m, my_position: positionAfterSell(m.my_position, side, proceeds) } : m));
    },
    onCommit: (result) => commit(result.market),
    onRollback: restore,
  });

  const toggleStar = useCallback(() => {
    const m = marketRef.current;
    if (!m) return;
    const starred = !m.starred;
    tg.HapticFeedback.selection();
    setMarket((prev) => (prev ? { ...prev, starred } : prev));
    api.star(m.id, starred).catch((err: unknown) => {
      setMarket((prev) => (prev ? { ...prev, starred: !starred } : prev));
      toast(err instanceof ApiError ? err.message : 'Не получилось. Попробуйте ещё раз.', 'error');
    });
  }, [toast]);

  const share = useCallback(() => {
    const m = marketRef.current;
    if (!m || !me) return;
    void shareMarket(m, me.referral_link);
  }, [me]);

  const openDispute = useCallback(() => {
    if (!marketRef.current) return;
    const link = botChatLink(me?.referral_link, `dispute_${marketRef.current.id}`);
    if (!link) return;
    tg.openTelegramLink(link);
    api.event('open_bot', { market: marketRef.current.id, reason: 'dispute' }).catch(() => {});
  }, [me]);

  const report = useCallback(() => {
    const m = marketRef.current;
    setReportOpen(false);
    if (!m) return;
    api
      .report(m.id)
      .then(() => toast('Жалоба отправлена. Спасибо, что сообщили!'))
      .catch((err: unknown) => toast(err instanceof ApiError ? err.message : 'Не получилось. Попробуйте ещё раз.', 'error'));
  }, [toast]);

  const back = () => {
    if (window.history.state && window.history.state.idx > 0) navigate(-1);
    else navigate('/', { replace: true });
  };

  const header = (
    <header className="mk-header">
      {isTelegram() ? (
        <span className="mk-header-side" />
      ) : (
        <button type="button" className="mk-icon-button" aria-label="Назад к ленте" onClick={back}>
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M15 5l-7 7 7 7" />
          </svg>
        </button>
      )}
      <span className="mk-header-title">Рынок</span>
      <div className="mk-header-side mk-header-actions">
        {market && (
          <>
            <button type="button" className="mk-icon-button" aria-label="Поделиться" onClick={share} disabled={!me}>
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M12 15V4M7.5 8.5L12 4l4.5 4.5" />
                <path d="M5 13v5.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V13" />
              </svg>
            </button>
            <button
              type="button"
              className="mk-icon-button"
              aria-pressed={market.starred}
              aria-label={market.starred ? 'Не следить за рынком' : 'Следить за рынком'}
              onClick={toggleStar}
            >
              <StarIcon on={market.starred} />
            </button>
          </>
        )}
      </div>
    </header>
  );

  if (state === 'loading' || (state === 'ready' && !market)) {
    return (
      <div className="screen mk" aria-busy="true">
        {header}
        <MarketSkeleton />
      </div>
    );
  }

  if (state === 'notfound' || state === 'error') {
    return (
      <div className="screen mk">
        {header}
        <div className="mk-message" role="alert">
          <h1 className="screen-title">{state === 'notfound' ? 'Рынок не найден' : 'Не удалось загрузить рынок'}</h1>
          <p className="sub">
            {state === 'notfound' ? 'Возможно, его скрыли или ссылка устарела.' : error}
          </p>
          {state === 'error' ? (
            <button type="button" className="button-secondary" onClick={() => setAttempt((n) => n + 1)}>
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

  const m = market!;
  const view = statusView(m.status);
  const pct = splitPercents(m.prob);
  const stats = m.my_position ? positionStats(m.my_position) : null;
  const sides = heldSides(m.my_position);
  const showPosition = !!stats && sides.length > 0 && (view.phase === 'open' || view.phase === 'closed' || view.phase === 'review');
  const sellable = canSell(m);
  const until = untilResult(m.resolves_at);
  const blocked = !m.can_trade || view.phase !== 'open';
  const blockText = m.trade_block ?? (m.is_creator ? 'Создатель рынка не может делать прогнозы на свой рынок.' : 'Приём прогнозов закрыт.');

  return (
    <div className="screen mk">
      {header}

      <div className="mk-body">
        <div className="mk-meta">
          <span>{[categoryLabel, formatEnds(m.closes_at)].filter(Boolean).join(' · ')}</span>
          {m.chat && <ChatTag chat={m.chat} />}
          <span className={`mk-status is-${view.tone}`}>{view.label}</span>
        </div>

        <h1 className="mk-title">{m.title}</h1>

        <div className="mk-author">
          {m.author.photo_url ? (
            <img className="mk-avatar" src={m.author.photo_url} alt="" />
          ) : (
            <span className="mk-avatar" aria-hidden="true">
              {initials(m.author.display_name)}
            </span>
          )}
          <div className="mk-author-text">
            <span className="mk-author-name">{m.author.display_name}</span>
            <span className="sub small">
              Автор · разрешил {pluralize(m.author.resolved, 'рынок', 'рынка', 'рынков')} · {m.author.disputes_lost}{' '}
              {plural(m.author.disputes_lost, 'проигранный спор', 'проигранных спора', 'проигранных споров')}
            </span>
          </div>
        </div>

        {view.phase === 'closed' && (
          <section className="card mk-section" aria-label="Ждём итог">
            <span className="mk-section-title">Приём прогнозов закрыт</span>
            <p className="mk-text sub">
              Ждём итог от создателя — он укажет его в боте с фото-доказательством до {formatDayMonth(m.resolves_at)}.
            </p>
          </section>
        )}

        {view.phase === 'review' && m.resolution && (
          <ReviewSection
            market={m}
            disputed={m.status === 'disputed'}
            disputeCost={me?.limits.dispute_cost}
            onDispute={openDispute}
          />
        )}

        {view.phase === 'resolved' && <ResolvedSection market={m} />}

        {view.phase === 'annulled' && (
          <section className="card mk-section" aria-label="Итог">
            <span className="sub small">Итог</span>
            <span className="mk-outcome">Отменён</span>
            <p className="mk-text">Рынок отменён, монеты вернулись.</p>
          </section>
        )}

        <ChartSection
          prob={m.prob}
          change24h={m.change_24h}
          period={period}
          history={history && history.period === period ? history.points : null}
          onPeriod={(p) => {
            if (p === period) return;
            tg.HapticFeedback.selection();
            setPeriod(p);
          }}
        />

        <div className="mk-tiles">
          <Tile label="Участники" value={formatMoney(m.participants, 0)} />
          <Tile label="Объём" value={formatCompact(m.volume)} />
          {until ? <Tile label="До итога" value={until} /> : <Tile label="Итог" value={formatDayMonth(m.resolves_at)} />}
        </div>

        {showPosition && stats && (
          <section className="card mk-section" aria-label="Моя позиция">
            <div className="mk-row">
              <span className="mk-section-title">Твоя позиция</span>
              <span className="mk-chips">
                {sides.map((side) => (
                  <span key={side} className={`mk-side-chip is-${side}`}>
                    {sideLabel(side)} · {sharesLabel(sharesOf(m.my_position!, side))}
                  </span>
                ))}
              </span>
            </div>
            <div className="mk-figures">
              <Figure label="Вложено" value={formatMoney(stats.spent, 0)} />
              <Figure label="Стоит сейчас" value={formatMoney(stats.value, 0)} />
              {sides.length === 1 ? (
                <Figure label="Если угадаешь" value={formatMoney(Math.floor(stats.payout), 0)} />
              ) : (
                <>
                  <Figure label="Если ДА" value={formatMoney(Math.floor(stats.payoutYes), 0)} />
                  <Figure label="Если НЕТ" value={formatMoney(Math.floor(stats.payoutNo), 0)} />
                </>
              )}
            </div>
            {sellable && (
              <div className={sides.length > 1 ? 'mk-sell-row' : undefined}>
                {sides.map((side) => (
                  <button
                    key={side}
                    type="button"
                    className="mk-outline-button"
                    disabled={sell.sellingId === m.id}
                    onClick={() => sell.request({ id: m.id, title: m.title, prob: m.prob, position: m.my_position!, b: m.b }, side)}
                  >
                    {sides.length === 1 ? 'Продать позицию' : `Продать ${sideLabel(side)}`}
                  </button>
                ))}
              </div>
            )}
          </section>
        )}

        <section className="card mk-section" aria-label="Как определяем результат">
          <h2 className="mk-section-title">Как определяем результат</h2>
          <p className="mk-text sub">{m.criteria}</p>
          <p className="mk-text small sub">{datesLine(m.closes_at, m.resolves_at)}</p>
        </section>

        <TopMovesSection moves={m.top_moves} />

        <TradesSection trades={m.trades} />

        {!m.is_creator && (
          <button type="button" className="mk-link-button" onClick={() => setReportOpen(true)}>
            Пожаловаться
          </button>
        )}
      </div>

      {view.phase === 'open' || view.phase === 'closed' ? (
        <div className="mk-footer">
          {blocked ? (
            <p className="mk-block" role="note">
              {blockText}
            </p>
          ) : (
            <div className="mk-actions">
              <button type="button" className="mk-buy is-yes" onClick={() => void trade.open(m, 'yes')}>
                ДА · {pct.yes}%
              </button>
              <button type="button" className="mk-buy is-no" onClick={() => void trade.open(m, 'no')}>
                НЕТ · {pct.no}%
              </button>
            </div>
          )}
        </div>
      ) : null}

      {trade.sheet}
      {sell.sheet}
      <BottomSheet open={reportOpen} onClose={() => setReportOpen(false)} label="Жалоба на рынок">
        <div className="mk-confirm">
          <div className="mk-section-title">Пожаловаться на рынок?</div>
          <p className="mk-text sub">
            Если вопрос нарушает правила или на него нельзя однозначно ответить, его проверит модератор. После трёх жалоб рынок
            скрывается до решения.
          </p>
          <button type="button" className="button-primary button-block is-ink" onClick={report}>
            Пожаловаться
          </button>
          <button type="button" className="button-secondary button-block" onClick={() => setReportOpen(false)}>
            Отмена
          </button>
        </div>
      </BottomSheet>
    </div>
  );
}

function StarIcon({ on }: { on: boolean }) {
  return (
    <svg
      width="22"
      height="22"
      viewBox="0 0 24 24"
      fill={on ? 'var(--gold)' : 'none'}
      stroke={on ? 'var(--gold)' : 'var(--sub)'}
      strokeWidth="1.8"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" />
    </svg>
  );
}

function PhotoIcon({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" aria-hidden="true">
      <rect x="3.5" y="5" width="17" height="14" rx="2" />
      <circle cx="9" cy="10" r="1.6" />
      <path d="M5 17l4.5-4.5 3 3 2.5-2.5 4 4" />
    </svg>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="mk-tile">
      <span className="sub small">{label}</span>
      <span className="mk-tile-value">{value}</span>
    </div>
  );
}

function Figure({ label, value, extra }: { label: string; value: string; extra?: ReactNode }) {
  return (
    <div className="mk-figure">
      <span className="sub small">{label}</span>
      <span className="mk-figure-value">
        {value}
        {extra}
      </span>
    </div>
  );
}

function Evidence({ url, compact = false }: { url: string | null; compact?: boolean }) {
  if (!url) {
    return compact ? null : (
      <div className="mk-evidence is-empty">
        <PhotoIcon size={32} />
        Фото-доказательство от автора
      </div>
    );
  }
  return (
    <a
      className={`mk-evidence ${compact ? 'is-compact' : ''}`}
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      aria-label="Открыть фото-доказательство"
    >
      <img src={url} alt="Фото-доказательство от автора" loading="lazy" />
    </a>
  );
}

function ReviewSection({
  market,
  disputed,
  disputeCost,
  onDispute,
}: {
  market: MarketDetail;
  disputed: boolean;
  disputeCost?: string;
  onDispute: () => void;
}) {
  const r = market.resolution!;
  const left = timeLeft(r.dispute_until);
  const proposed = r.proposed_outcome;
  const tone = proposed === 'yes' ? 'is-yes' : proposed === 'no' ? 'is-no' : '';
  let payoutNote = '';
  if (market.my_position && (proposed === 'yes' || proposed === 'no')) {
    const { payout } = settledResult(market.my_position, proposed);
    payoutNote = ` Твоя выплата при ${sideLabel(proposed)}: ${formatMoney(Math.floor(payout), 0)}.`;
  }
  return (
    <section className="card mk-section" aria-label="Итог на проверке">
      <div className="mk-row is-baseline">
        <span className="sub small">Итог от автора</span>
        <span className="sub small mk-strong">
          {disputed ? 'Спор у модератора' : left ? `Окно спора: ${left}` : 'Окно спора закрыто'}
        </span>
      </div>
      <span className={`mk-outcome ${tone}`}>{proposed === 'cancel' ? 'Отменить рынок' : outcomeLabel(proposed)}</span>
      <Evidence url={r.evidence_url} />
      {r.note && <p className="mk-text">{r.note}</p>}
      <p className="mk-text sub small">
        {disputed
          ? 'Итог оспорили — решение примет модератор, оно окончательное.'
          : `Если никто не оспорит итог, выплаты придут автоматически.${payoutNote}`}
      </p>
      {r.can_dispute && (
        <>
          <button type="button" className="mk-outline-button" onClick={onDispute}>
            Оспорить итог{disputeCost ? ` · ${formatMoney(disputeCost, 0)} монет` : ''}
          </button>
          <p className="mk-text sub small">Спор принимает бот: он спросит подтверждение перед списанием монет.</p>
        </>
      )}
    </section>
  );
}

function ResolvedSection({ market }: { market: MarketDetail }) {
  const outcome = market.outcome;
  const result = market.my_position && outcome ? settledResult(market.my_position, outcome) : null;
  const evidence = market.resolution?.evidence_url ?? null;
  return (
    <section className="card mk-section mk-resolved" aria-label="Итог">
      <div className="mk-resolved-text">
        <span className="sub small">Итог</span>
        <span className={`mk-outcome ${outcome === 'yes' ? 'is-yes' : outcome === 'no' ? 'is-no' : ''}`}>{outcomeLabel(outcome)}</span>
        {result && Number(market.my_position!.spent) > 0 && (
          <span className="mk-result">
            Ты получил {formatMoney(Math.floor(result.payout), 0)}{' '}
            <span className={result.profit >= 0 ? 'is-up' : 'is-down'}>({formatSigned(Math.round(result.profit), 0)})</span>
          </span>
        )}
      </div>
      <Evidence url={evidence} compact />
    </section>
  );
}

function ChartSection({
  prob,
  change24h,
  period,
  history,
  onPeriod,
}: {
  prob: number;
  change24h: number;
  period: HistoryPeriod;
  history: HistoryPoint[] | null;
  onPeriod: (p: HistoryPeriod) => void;
}) {
  const chart = useMemo(() => buildChart(history ?? []), [history]);
  const probs = useMemo(() => chart.points.map((pt) => ({ x: pt.x, y: pt.p })), [chart]);
  const [hover, setHover] = useState<number | null>(null);
  // While the line draws itself the big number runs along with its tip.
  const [headX, setHeadX] = useState<number | null>(null);
  useEffect(() => setHover(null), [history]);

  const word = periodWord(period);
  const change = history ? periodChange(history) : period === 'd' ? Math.round(change24h) : null;
  const first = history?.[0];
  const last = history?.[history.length - 1];

  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    if (chart.points.length === 0 || headX !== null) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * CHART.width;
    const i = pointIndexAt(chart.points, Math.min(x, CHART.plotWidth));
    if (i !== hover) setHover(i);
  };

  const hp = hover !== null ? chart.points[hover] : null;
  const boxX = hp ? Math.min(Math.max(hp.x - 52, 0), CHART.plotWidth - 104) : 0;

  return (
    <section className="card mk-section mk-chart" aria-label="Вероятность во времени">
      <div className="mk-row is-end">
        <div className="mk-prob">
          {headX !== null && probs.length > 0 ? (
            <span className="mk-pct" style={{ fontVariantNumeric: 'tabular-nums' }}>
              {formatPercent(lineYAt(probs, headX, 'step'))}
            </span>
          ) : (
            <AnimatedNumber value={prob * 100} format={(n) => Math.round(n) + '%'} className="mk-pct" />
          )}
          <span className="sub small">вероятность ДА</span>
        </div>
        {change !== null && <span className={`mk-delta ${change >= 0 ? 'is-up' : 'is-down'}`}>{changeLabel(change, word)}</span>}
      </div>

      {history === null ? (
        <Skeleton height={180} radius={12} />
      ) : (
        <svg
          className="mk-chart-svg live-chart"
          viewBox={`0 0 ${CHART.width} ${CHART.height}`}
          role="img"
          aria-label={
            first && last
              ? `Вероятность ДА ${word}: от ${formatPercent(first.p)} до ${formatPercent(last.p)}`
              : `Вероятность ДА ${word}`
          }
          onPointerMove={onMove}
          onPointerDown={onMove}
          onPointerLeave={() => setHover(null)}
        >
          {[0.75, 0.5, 0.25, 0].map((p) => (
            <line key={p} x1="0" y1={chartY(p)} x2={CHART.plotWidth} y2={chartY(p)} className="mk-grid" />
          ))}
          {[0.75, 0.5, 0.25].map((p) => (
            <text key={p} x={CHART.width} y={chartY(p) + 4} textAnchor="end" className="mk-axis">
              {Math.round(p * 100)}%
            </text>
          ))}
          <LiveLine
            points={chart.points}
            line={chart.line}
            area={chart.area}
            curve="step"
            showEnd={hp === null}
            onDraw={setHeadX}
          />
          {hp && (
            <g>
              <line x1={hp.x} y1="10" x2={hp.x} y2={CHART.bottom} className="mk-cursor" />
              <circle cx={hp.x} cy={hp.y} r="4" className="mk-dot" />
              <rect x={boxX} y="0" width="104" height="38" rx="8" className="mk-tip" />
              <text x={boxX + 10} y="16" className="mk-tip-value">
                ДА {formatPercent(hp.p)}
              </text>
              <text x={boxX + 10} y="31" className="mk-tip-ago">
                {timeAgo(hp.t)}
              </text>
            </g>
          )}
          {first && (
            <text x="0" y="172" className="mk-axis">
              {timeAgo(first.t)}
            </text>
          )}
          <text x={CHART.plotWidth} y="172" textAnchor="end" className="mk-axis">
            сейчас
          </text>
        </svg>
      )}

      <div role="group" aria-label="Период" className="mk-periods">
        {PERIODS.map((p) => (
          <button
            key={p.key}
            type="button"
            className={`chip-button ${p.key === period ? 'is-active' : ''}`}
            aria-pressed={p.key === period}
            onClick={() => onPeriod(p.key)}
          >
            {p.label}
          </button>
        ))}
      </div>
    </section>
  );
}

function TopMovesSection({ moves }: { moves: MarketDetail['top_moves'] }) {
  const rows = useMemo(() => topMovesView(moves), [moves]);
  if (rows.length === 0) return null;
  return (
    <section className="card mk-section mk-trades" aria-label="Самые резкие сдвиги">
      <h2 className="mk-section-title">Самые резкие сдвиги</h2>
      <ul className="mk-trade-list">
        {rows.map((r) => (
          <li key={r.key} className="mk-trade">
            <span className={`mk-move-arrow ${r.up ? 'is-up' : 'is-down'}`} aria-hidden="true">
              {r.up ? '▲' : '▼'}
            </span>
            <span className="mk-trade-text sub small">{r.when}</span>
            <span className="mk-trade-move">{r.move}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function TradesSection({ trades }: { trades: MarketDetail['trades'] }) {
  return (
    <section className="card mk-section mk-trades" aria-label="Последние прогнозы">
      <h2 className="mk-section-title">Последние прогнозы</h2>
      {trades.length === 0 ? (
        <p className="mk-text sub">Пока никто не сделал прогноз.</p>
      ) : (
        <ul className="mk-trade-list">
          {trades.map((t, i) => {
            const before = trades[i + 1]?.prob_after;
            return (
              <li key={`${t.at}-${i}`} className="mk-trade">
                <span className={`mk-trade-dot is-${t.side}`} aria-hidden="true" />
                <div className="mk-trade-text">
                  <span className="mk-trade-what">
                    Кто-то{t.sell ? ' продал' : ':'} {sideLabel(t.side as Side)} на {formatMoney(t.amount)}
                  </span>
                  <span className="sub small">{timeAgo(t.at)}</span>
                </div>
                <span className="mk-trade-move sub">
                  {before !== undefined ? `${formatPercent(before)} → ` : ''}
                  {formatPercent(t.prob_after)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function MarketSkeleton() {
  return (
    <div className="mk-body" aria-hidden="true">
      <Skeleton width={160} height={14} />
      <Skeleton height={24} />
      <Skeleton width="70%" height={24} />
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <Skeleton width={32} height={32} radius={999} />
        <Skeleton width={180} height={14} />
      </div>
      <div className="card mk-section">
        <Skeleton width={90} height={44} />
        <Skeleton height={180} radius={12} />
        <Skeleton height={36} radius={10} />
      </div>
      <div className="mk-tiles">
        <Skeleton height={60} radius={14} />
        <Skeleton height={60} radius={14} />
        <Skeleton height={60} radius={14} />
      </div>
    </div>
  );
}
