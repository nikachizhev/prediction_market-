// Market card from the feed (docs/mockups/Main.dc.html).
import { memo, useEffect, useMemo, useRef } from 'react';
import { Link } from 'react-router-dom';
import type { MarketCard, Position, Side, SubmarketCard } from '../api';
import { formatCompact, formatEnds, formatMoney, pluralize } from '../format';
import { positionSummary, sideLabel, splitPercents } from '../trade';
import { AnimatedNumber } from './AnimatedNumber';
import { ChatTag } from './ChatTag';
import { Sparkline } from './Sparkline';
import './multi-card.css';

interface MarketCardViewProps {
  market: MarketCard;
  categoryLabel: string;
  /** Text after the meta line, e.g. « · Группа ПИ-24» in the creation preview. */
  metaSuffix?: string;
  /** Own position learned from a trade in this session, if any. */
  position?: Position | null;
  pending?: boolean;
  /** Pause before the sparkline draws, ms (the feed cascade). */
  drawDelay?: number;
  onTrade: (market: MarketCard, side: Side) => void;
  onStar: (market: MarketCard) => void;
  onImpression?: (market: MarketCard) => void;
  onOpen?: (market: MarketCard) => void;
}

export const MarketCardView = memo(function MarketCardView({
  market,
  categoryLabel,
  metaSuffix = '',
  position,
  pending = false,
  drawDelay = 0,
  onTrade,
  onStar,
  onImpression,
  onOpen,
}: MarketCardViewProps) {
  const article = useRef<HTMLElement>(null);
  const seen = useRef(false);
  useEffect(() => {
    if (!article.current || !onImpression || seen.current || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver((entries) => {
      if (!seen.current && entries.some((entry) => entry.isIntersecting && entry.intersectionRatio >= 0.5)) {
        seen.current = true;
        onImpression(market);
        observer.disconnect();
      }
    }, { threshold: 0.5 });
    observer.observe(article.current);
    return () => observer.disconnect();
  }, [market, onImpression]);
  const pct = splitPercents(market.prob);
  const delta = Math.round(market.change_24h);
  const up = delta >= 0;
  const mine = positionSummary(position);

  return (
    <article ref={article} className="card market-card" aria-busy={pending || undefined}>
      <div className="market-meta">
        <div className="market-meta-start">
          <span className="sub market-meta-text">
            {[categoryLabel, formatEnds(market.closes_at)].filter(Boolean).join(' · ')}
            {metaSuffix}
          </span>
          {market.chat && <ChatTag chat={market.chat} />}
        </div>
        <button
          type="button"
          className="star-button"
          aria-pressed={market.starred}
          aria-label={market.starred ? 'Не следить за рынком' : 'Следить за рынком'}
          onClick={() => onStar(market)}
        >
          <svg
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill={market.starred ? 'var(--gold)' : 'none'}
            stroke={market.starred ? 'var(--gold)' : 'var(--sub)'}
            strokeWidth="1.8"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" />
          </svg>
        </button>
      </div>

      <h2 className="market-title">
        <Link to={`/market/${market.id}`} className="stretched-link" onClick={() => onOpen?.(market)}>
          {market.title}
        </Link>
      </h2>

      {market.submarkets && market.submarkets.length > 0 ? (
        <MultiCardContent
          market={market}
          submarkets={market.submarkets}
          pending={pending}
          onTrade={onTrade}
        />
      ) : (
        <>
          <div className="market-stats">
            <div className="market-prob">
              <AnimatedNumber value={market.prob * 100} format={(n) => Math.round(n) + '%'} className="market-pct" />
              <span className={`market-delta ${up ? 'is-up' : 'is-down'}`}>
                {up ? '▲' : '▼'} {Math.abs(delta)} за сутки
              </span>
            </div>
            <Sparkline points={market.spark} color={up ? 'var(--yes)' : 'var(--no)'} delay={drawDelay} />
          </div>

          <div className="sub market-people">
            {pluralize(market.participants, 'участник', 'участника', 'участников')} · объём {formatCompact(market.volume)}
          </div>

          {mine && (
            <div className="market-position">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--yes)" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M5 12.5l4.5 4.5L19 7.5" />
              </svg>
              Твой прогноз: {sideLabel(mine.side)} · выплата {formatMoney(Math.floor(mine.payout), 0)}
            </div>
          )}

          <div className="market-actions">
            <button type="button" className="side-button is-yes" disabled={pending} onClick={() => onTrade(market, 'yes')}>
              ДА · {pct.yes}%
            </button>
            <button type="button" className="side-button is-no" disabled={pending} onClick={() => onTrade(market, 'no')}>
              НЕТ · {pct.no}%
            </button>
          </div>
        </>
      )}
    </article>
  );
});

interface MultiCardContentProps {
  market: MarketCard;
  submarkets: SubmarketCard[];
  pending: boolean;
  onTrade: (market: MarketCard, side: Side) => void;
}

function MultiCardContent({ market, submarkets, pending, onTrade }: MultiCardContentProps) {
  // Show at most 2 options with the highest volume on the feed card
  const visible = useMemo(() => {
    return [...submarkets]
      .sort((a, b) => Number(b.volume || 0) - Number(a.volume || 0))
      .slice(0, 2);
  }, [submarkets]);

  const remaining = submarkets.length - visible.length;

  return (
    <>
      <div className="multi-card-options">
        {visible.map((sub) => {
          const subPct = splitPercents(sub.prob);
          const subDelta = Math.round(sub.change_24h);
          const subUp = subDelta >= 0;

          return (
            <div key={sub.id} className="multi-option-row">
              <div className="multi-option-left">
                <span className="multi-option-title" title={sub.label}>
                  {sub.label}
                </span>
                <span className={`market-delta multi-option-delta ${subUp ? 'is-up' : 'is-down'}`}>
                  {subUp ? '▲' : '▼'} {Math.abs(subDelta)} за сутки
                </span>
              </div>

              <div className="multi-option-actions">
                <button
                  type="button"
                  className="multi-btn is-yes"
                  disabled={pending}
                  onClick={() => onTrade(sub, 'yes')}
                >
                  ДА · {subPct.yes}%
                </button>
                <button
                  type="button"
                  className="multi-btn is-no"
                  disabled={pending}
                  onClick={() => onTrade(sub, 'no')}
                >
                  НЕТ · {subPct.no}%
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {remaining > 0 && (
        <Link to={`/market/${market.id}`} className="multi-more-link">
          Показать ещё →
        </Link>
      )}

      <div className="sub market-people">
        {pluralize(market.participants, 'участник', 'участника', 'участников')} · объём {formatCompact(market.volume)}
      </div>
    </>
  );
}
