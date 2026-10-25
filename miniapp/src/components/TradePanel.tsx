// Trade panel from docs/mockups/Main.dc.html (bottom sheet «Панель прогноза»).
import { useEffect, useRef, useState } from 'react';
import { api, ApiError, type MarketDetail, type Quote, type Side } from '../api';
import { formatMoney, formatPercent } from '../format';
import { useSession } from '../session';
import { tg } from '../telegram';
import { themes } from '../theme';
import { clampStake, defaultStake, maxStake, quickStakes, sideLabel, splitPercents, toMoney } from '../trade';
import { AnimatedNumber } from './AnimatedNumber';
import { CoinIcon } from './CoinIcon';
import { MainAction } from './MainAction';
import { Skeleton } from './Skeleton';

const QUOTE_DEBOUNCE_MS = 250;

/** Fill colours of the confirm button; equal in both themes. */
export const SIDE_FILL: Record<Side, string> = { yes: themes.light.yesFill, no: themes.light.noFill };

interface TradePanelProps {
  market: MarketDetail;
  initialSide: Side;
  onConfirm: (side: Side, amount: number, quote: Quote | null) => void;
}

/** Side switch, stake slider with quick buttons, live quote and the confirm button. */
export function TradePanel({ market, initialSide, onConfirm }: TradePanelProps) {
  const { me } = useSession();
  const max = maxStake(market.max_amount);
  const [side, setSide] = useState<Side>(initialSide);
  const [amount, setAmount] = useState(() => defaultStake(max));
  const [quote, setQuote] = useState<Quote | null>(null);
  const [quoteError, setQuoteError] = useState<string | null>(null);
  const seq = useRef(0);
  const firstQuote = useRef(true);

  useEffect(() => {
    if (amount < 1) return;
    const id = ++seq.current;
    // The first quote is asked at once; later ones wait until the slider settles.
    const delay = firstQuote.current ? 0 : QUOTE_DEBOUNCE_MS;
    const timer = window.setTimeout(() => {
      api
        .quote(market.id, side, toMoney(amount))
        .then((q) => {
          if (id !== seq.current) return;
          firstQuote.current = false;
          setQuote(q);
          setQuoteError(null);
        })
        .catch((err: unknown) => {
          if (id !== seq.current) return;
          setQuoteError(err instanceof ApiError ? err.message : 'Не удалось посчитать выплату.');
        });
    }, delay);
    return () => window.clearTimeout(timer);
  }, [market.id, side, amount]);

  const pickSide = (next: Side) => {
    if (next === side) return;
    tg.HapticFeedback.impact('light');
    setSide(next);
  };

  const pickAmount = (value: number) => {
    const next = clampStake(value, max);
    if (next !== amount) tg.HapticFeedback.selection();
    setAmount(next);
  };

  const pct = splitPercents(market.prob);
  const current = quote && quote.side === side && Number(quote.amount) === amount ? quote : null;
  const shown = quote && quote.side === side ? quote : null;
  const feeRate = me ? me.limits.fee_rate : 0.02;
  const feeLabel = `Комиссия ${formatMoney(feeRate * 100, 1)}%`;

  return (
    <div className="trade-panel">
      <div className="trade-title">{market.title}</div>

      <div className="segmented" role="radiogroup" aria-label="Исход">
        {(['yes', 'no'] as const).map((s) => (
          <button
            key={s}
            type="button"
            role="radio"
            aria-checked={side === s}
            className={`segmented-option ${side === s ? `is-${s}` : ''}`}
            onClick={() => pickSide(s)}
          >
            {sideLabel(s)} · {s === 'yes' ? pct.yes : pct.no}%
          </button>
        ))}
      </div>

      <div className="stake">
        <div className="stake-head">
          <label htmlFor="trade-amount" className="sub stake-label">
            Сумма
          </label>
          <div className="stake-value">
            <CoinIcon size={24} />
            {amount}
          </div>
        </div>
        <input
          id="trade-amount"
          type="range"
          min={1}
          max={max}
          step={1}
          value={amount}
          onChange={(e) => pickAmount(Number(e.target.value))}
          className="stake-range"
          style={{ accentColor: SIDE_FILL[side] }}
        />
        <div className="quick-stakes">
          {quickStakes(max).map((q) => (
            <button
              key={q.label}
              type="button"
              className={`chip-button ${amount === q.value ? 'is-active' : ''}`}
              disabled={q.disabled}
              onClick={() => pickAmount(q.value)}
            >
              {q.label}
            </button>
          ))}
        </div>
      </div>

      <div className="quote-box" aria-live="polite">
        {quoteError && !current ? (
          <div className="quote-error">{quoteError}</div>
        ) : (
          <>
            <div className="quote-row">
              <span className="sub">Выплата, если угадаешь</span>
              {shown ? (
                <span className="quote-strong">
                  <AnimatedNumber value={Math.floor(Number(shown.payout_if_win))} format={(n) => formatMoney(Math.round(n), 0)} />{' '}
                  <span className="quote-profit">
                    (+{formatMoney(Math.max(0, Math.floor(Number(shown.payout_if_win)) - Number(shown.amount)), 0)})
                  </span>
                </span>
              ) : (
                <Skeleton width={72} height={18} />
              )}
            </div>
            <div className="quote-row">
              <span className="sub">Вероятность ДА после сделки</span>
              {shown ? (
                <span className="quote-value">
                  {formatPercent(market.prob)} → {formatPercent(shown.prob_after)}
                </span>
              ) : (
                <Skeleton width={72} height={18} />
              )}
            </div>
            <div className="quote-row">
              <span className="sub">{feeLabel}</span>
              {shown ? <span className="quote-value">{formatMoney(shown.fee)}</span> : <Skeleton width={40} height={18} />}
            </div>
          </>
        )}
      </div>

      <MainAction
        text={`Прогноз ${sideLabel(side)} на ${amount}`}
        color={SIDE_FILL[side]}
        disabled={amount < 1}
        onClick={() => onConfirm(side, amount, current)}
      />
      <div className="limit-note sub">Максимум за сделку — {formatMoney(max)}</div>
    </div>
  );
}
