// Market card from the feed (docs/mockups/Main.dc.html).
import { memo } from 'react';
import { Link } from 'react-router-dom';
import type { MarketCard, Position, Side } from '../api';
import { formatCompact, formatEnds, formatMoney, pluralize } from '../format';
import { positionSummary, sideLabel, splitPercents } from '../trade';
import { AnimatedNumber } from './AnimatedNumber';
import { Sparkline } from './Sparkline';

interface MarketCardViewProps {
  market: MarketCard;
  categoryLabel: string;
  /** Own position learned from a trade in this session, if any. */
  position?: Position | null;
  pending?: boolean;
  /** Pause before the sparkline draws, ms (the feed cascade). */
  drawDelay?: number;
  onTrade: (market: MarketCard, side: Side) => void;
  onStar: (market: MarketCard) => void;
}

export const MarketCardView = memo(function MarketCardView({
  market,
  categoryLabel,
  position,
  pending = false,
  drawDelay = 0,
  onTrade,
  onStar,
}: MarketCardViewProps) {
  const pct = splitPercents(market.prob);
  const delta = Math.round(market.change_24h);
  const up = delta >= 0;
  const mine = positionSummary(position);

  return (
    <article className="card market-card" aria-busy={pending || undefined}>
      <div className="market-meta">
        <span className="sub market-meta-text">
          {[categoryLabel, formatEnds(market.closes_at)].filter(Boolean).join(' · ')}
        </span>
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
        <Link to={`/market/${market.id}`} className="stretched-link">
          {market.title}
        </Link>
      </h2>

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
    </article>
  );
});
