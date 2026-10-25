// First launch from docs/mockups/Onboarding.dc.html:
// welcome with the starter pack -> first prediction in 3 taps -> how to show me in the rating.
import { useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError, newRequestKey, type Category, type MarketDetail, type Quote, type Side } from '../api';
import { AnimatedNumber } from '../components/AnimatedNumber';
import { CoinIcon } from '../components/CoinIcon';
import { MainAction } from '../components/MainAction';
import { Skeleton } from '../components/Skeleton';
import { useToast } from '../components/Toast';
import { SIDE_FILL } from '../components/TradePanel';
import { formatDayMonth, formatEnds, formatMoney, plural, pluralize } from '../format';
import { useSession } from '../session';
import { tg } from '../telegram';
import {
  cardBlockReason,
  clampStake,
  maxStake,
  optimisticCost,
  QUICK_STAKES,
  runOptimisticBuy,
  sideLabel,
  splitPercents,
  toMoney,
  tradeBlockReason,
} from '../trade';

type Step = 0 | 1 | 2;
const FIRST_STAKE = 50;
const STARTER_FALLBACK = 1000;
/** How many feed markets to check for one the newcomer can trade on. */
const CANDIDATES = 5;

interface FirstTrade {
  side: Side;
  amount: number;
  market: MarketDetail;
}

/** Finds an open market the user can stake the smallest quick amount on. */
async function pickFirstMarket(me: Parameters<typeof tradeBlockReason>[1]): Promise<MarketDetail | null> {
  const { items } = await api.markets({ sort: 'hot' });
  for (const card of items.filter((m) => !cardBlockReason(m)).slice(0, CANDIDATES)) {
    const detail = await api.market(card.id).catch(() => null);
    if (detail && !tradeBlockReason(detail, me) && maxStake(detail.max_amount) >= QUICK_STAKES[0]) return detail;
  }
  return null;
}

/** "Ты поставил 50 на ДА. Итог узнаем до 1 декабря, а пока — баланс 950." */
function doneLine(trade: FirstTrade, balance: string): string {
  const when = trade.market.resolves_at || trade.market.closes_at;
  const result = when ? `Итог узнаем до ${formatDayMonth(when)}, а пока — баланс` : 'А пока — баланс';
  return `Ты поставил ${trade.amount} на ${sideLabel(trade.side)}. ${result} ${formatMoney(balance)}.`;
}

export function Onboarding() {
  const { me, granted, setMe, adjustBalance, setBalance } = useSession();
  const toast = useToast();
  const [step, setStep] = useState<Step>(0);
  const [market, setMarket] = useState<MarketDetail | null | undefined>(undefined);
  const [categories, setCategories] = useState<Category[]>([]);
  const [side, setSide] = useState<Side | null>(null);
  const [amount, setAmount] = useState(FIRST_STAKE);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [buying, setBuying] = useState(false);
  const [trade, setTrade] = useState<FirstTrade | null>(null);
  const [showRealName, setShowRealName] = useState(false);
  const [botAllowed, setBotAllowed] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [coins, setCoins] = useState(0);
  const requestKey = useRef(newRequestKey());

  const starter = Number(granted.find((g) => g.kind === 'starter')?.amount ?? STARTER_FALLBACK);

  // Coins pour in on the welcome screen.
  useEffect(() => {
    const id = window.setTimeout(() => {
      setCoins(starter);
      tg.HapticFeedback.notification('success');
    }, 250);
    return () => window.clearTimeout(id);
  }, [starter]);

  // Pick the first market while the user reads the welcome screen.
  useEffect(() => {
    let cancelled = false;
    pickFirstMarket(me)
      .then((m) => !cancelled && setMarket(m))
      .catch(() => !cancelled && setMarket(null));
    api
      .categories()
      .then((c) => !cancelled && setCategories(c))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // Only once: `me` changes with the balance after the trade.
  }, []);

  const max = market ? maxStake(market.max_amount) : 0;
  const stake = clampStake(amount, max);

  useEffect(() => {
    if (!market || !side || stake < 1) return;
    let cancelled = false;
    api
      .quote(market.id, side, toMoney(stake))
      .then((q) => !cancelled && setQuote(q))
      .catch(() => !cancelled && setQuote(null));
    return () => {
      cancelled = true;
    };
  }, [market, side, stake]);

  const categoryLabel = useMemo(
    () => (market ? categories.find((c) => c.slug === market.category)?.label ?? market.category : ''),
    [categories, market],
  );

  if (!me) return null;

  const finish = async (skipped: boolean) => {
    if (finishing) return;
    setFinishing(true);
    const patch = skipped ? { onboarded: true } : { onboarded: true, show_real_name: showRealName };
    api
      .event(skipped ? 'onboarding_skip' : 'onboarding_done', { step, traded: trade !== null })
      .catch(() => {});
    try {
      setMe(await api.updateMe(patch));
    } catch {
      // Do not trap the user in onboarding; the flag is saved next time.
      setMe({ ...me, ...patch, onboarded: true });
    }
  };

  const pickSide = (next: Side) => {
    tg.HapticFeedback.impact('light');
    setSide(next);
  };

  const confirm = async () => {
    if (!market || !side || stake < 1 || buying) return;
    setBuying(true);
    const key = requestKey.current;
    const outcome = await runOptimisticBuy({
      send: () => api.buy(market.id, side, toMoney(stake), key),
      cost: optimisticCost(stake, side, quote),
      probAfter: null,
      adjustBalance,
      setBalance,
    });
    setBuying(false);
    if (outcome.ok) {
      tg.HapticFeedback.notification('success');
      setTrade({ side, amount: stake, market: outcome.result.market });
      setStep(2);
    } else {
      tg.HapticFeedback.notification('error');
      toast(outcome.error.message, 'error');
      // A refused purchase is final; the next attempt is a new request.
      if (!(outcome.error instanceof ApiError && outcome.error.status === 0)) requestKey.current = newRequestKey();
    }
  };

  const allowBot = async () => {
    const allowed = await tg.requestWriteAccess();
    if (allowed) tg.HapticFeedback.notification('success');
    setBotAllowed(allowed);
  };

  const pct = market ? splitPercents(market.prob) : { yes: 0, no: 0 };
  const payout = quote && side && quote.side === side ? Math.floor(Number(quote.payout_if_win)) : null;

  return (
    <div className="onboarding">
      <div className="onboarding-top">
        <div className="steps-dots" aria-hidden="true">
          {[0, 1, 2].map((n) => (
            <span key={n} className={n === step ? 'is-active' : ''} />
          ))}
        </div>
        {step < 2 && (
          <button type="button" className="skip-button" onClick={() => finish(true)} disabled={finishing}>
            Пропустить
          </button>
        )}
      </div>

      {step === 0 && (
        <>
          <main className="onboarding-body">
            <div className="welcome-hero">
              <span className="welcome-coin">
                <CoinIcon size={56} strokeWidth={1.8} />
              </span>
              <AnimatedNumber
                value={coins}
                duration={900}
                format={(n) => '+' + formatMoney(Math.round(n), 0)}
                className="welcome-amount"
              />
              <span className="sub">монет на старт уже у тебя</span>
            </div>
            <h1 className="onboarding-title is-centered">Прогнозируй события и проверяй своё чутьё</h1>
            <ul className="welcome-list">
              <li>
                <span className="welcome-icon welcome-icon-text">ДА</span>
                Ставь монеты на ДА или НЕТ — от релизов игр до экзаменов
              </li>
              <li>
                <span className="welcome-icon">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M5 12.5l4.5 4.5L19 7.5" />
                  </svg>
                </span>
                Угадал — забираешь выплату, чем раньше, тем больше
              </li>
              <li>
                <span className="welcome-icon">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                    <path d="M5 20v-8M12 20V5M19 20v-12" />
                  </svg>
                </span>
                Соревнуйся с друзьями и своим чатом в рейтинге
              </li>
            </ul>
            {me.referred_by && (
              <div className="invite-note">
                <span className="invite-avatar">{me.referred_by.slice(0, 1).toUpperCase()}</span>
                <span>
                  Тебя позвал <b>{me.referred_by}</b>. Сделай 5 прогнозов — и вы оба получите по 300 монет.
                </span>
              </div>
            )}
          </main>
          <div className="onboarding-footer">
            <MainAction text="Сделать первый прогноз" variant="ink" onClick={() => setStep(1)} />
            <span className="sub footnote">Монеты виртуальные: их нельзя купить или вывести</span>
          </div>
        </>
      )}

      {step === 1 && (
        <>
          <main className="onboarding-body">
            <h1 className="onboarding-title">Как думаешь?</h1>
            {market === undefined && (
              <div className="card first-market" aria-hidden="true">
                <Skeleton width="60%" height={14} />
                <Skeleton height={22} />
                <Skeleton width={90} height={36} />
                <Skeleton height={48} radius={12} />
              </div>
            )}
            {market === null && (
              <div className="card first-market">
                <p className="sub">Сейчас нет рынков, открытых для прогноза. Загляни в ленту чуть позже.</p>
              </div>
            )}
            {market && (
              <article className="card first-market">
                <span className="sub small">
                  {[
                    categoryLabel,
                    formatEnds(market.closes_at),
                    pluralize(market.participants, 'участник', 'участника', 'участников'),
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </span>
                <h2 className="first-market-title">{market.title}</h2>
                <div className="first-market-prob">
                  <span className="first-market-pct">{pct.yes}%</span>
                  <span className="sub small">вероятность ДА</span>
                </div>
                <div className="hint-box">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--yes)" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                    <circle cx="12" cy="12" r="9" />
                    <path d="M12 11v5M12 7.5v.01" />
                  </svg>
                  <span>
                    {pct.yes}% — это цена ДА и оценка шанса. Все участники вместе считают, что ответ «да» — с
                    вероятностью {pct.yes}%. Не согласен — ставь против.
                  </span>
                </div>
                <div className="market-actions">
                  {(['yes', 'no'] as const).map((s) => (
                    <button
                      key={s}
                      type="button"
                      aria-pressed={side === s}
                      className={`side-button is-${s} is-large ${side === s ? 'is-picked' : ''}`}
                      onClick={() => pickSide(s)}
                    >
                      {sideLabel(s)} · {s === 'yes' ? pct.yes : pct.no}%
                    </button>
                  ))}
                </div>
              </article>
            )}
            {market && side && (
              <section className="card first-stake" aria-label="Сумма прогноза">
                <span className="first-stake-label">Сколько монет поставить?</span>
                <div className="quick-stakes is-three">
                  {QUICK_STAKES.map((v) => (
                    <button
                      key={v}
                      type="button"
                      aria-pressed={stake === v}
                      className={`chip-button ${stake === v ? 'is-active' : ''}`}
                      disabled={v > max}
                      onClick={() => setAmount(v)}
                    >
                      {v}
                    </button>
                  ))}
                </div>
                <span className="sub">
                  {payout === null ? (
                    <Skeleton width="80%" height={16} />
                  ) : (
                    `Если угадаешь, получишь ${formatMoney(payout, 0)} ${plural(payout, 'монету', 'монеты', 'монет')}. Если нет — ставка сгорит.`
                  )}
                </span>
              </section>
            )}
          </main>
          <div className="onboarding-footer">
            {market === null ? (
              <MainAction text="Дальше" variant="ink" onClick={() => setStep(2)} />
            ) : (
              <MainAction
                text={side ? `Прогноз ${sideLabel(side)} на ${stake}` : 'Выбери ДА или НЕТ'}
                color={side ? SIDE_FILL[side] : undefined}
                disabled={!side || !market}
                busy={buying}
                onClick={confirm}
              />
            )}
          </div>
        </>
      )}

      {step === 2 && (
        <>
          <main className="onboarding-body">
            {trade && (
              <div className="done-hero">
                <span className="done-check">
                  <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M5 12.5l4.5 4.5L19 7.5" />
                  </svg>
                </span>
                <h1 className="onboarding-title is-centered">Первый прогноз сделан</h1>
                <span className="sub">
                  {doneLine(trade, me.balance)}
                </span>
              </div>
            )}

            {tg.canRequestWriteAccess && (
              <section className="card setting-row" aria-label="Уведомления от бота">
                <span>Пришлём итог и выплату сообщением в Telegram</span>
                <button
                  type="button"
                  className={`small-button ${botAllowed ? 'is-done' : ''}`}
                  onClick={allowBot}
                  disabled={botAllowed}
                >
                  {botAllowed ? 'Готово' : 'Разрешить'}
                </button>
              </section>
            )}

            <section className="card name-choice" aria-label="Имя в рейтинге">
              <span className="name-choice-title">Как показывать тебя в рейтинге</span>
              {[
                { real: false, label: me.pseudonym, sub: 'Случайный псевдоним — можно поменять' },
                { real: true, label: me.name, sub: 'Имя из Telegram' },
              ].map((o) => (
                <button
                  key={String(o.real)}
                  type="button"
                  role="radio"
                  aria-checked={showRealName === o.real}
                  className={`radio-option ${showRealName === o.real ? 'is-active' : ''}`}
                  onClick={() => setShowRealName(o.real)}
                >
                  <span className="radio-dot" aria-hidden="true">
                    <span />
                  </span>
                  <span className="radio-text">
                    <span className="radio-label">{o.label}</span>
                    <span className="sub small">{o.sub}</span>
                  </span>
                </button>
              ))}
            </section>
          </main>
          <div className="onboarding-footer">
            <MainAction text="В ленту" variant="ink" busy={finishing} onClick={() => finish(false)} />
          </div>
        </>
      )}
    </div>
  );
}
