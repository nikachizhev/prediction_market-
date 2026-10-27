// Profile screen from docs/mockups/Profile.dc.html.
import { useEffect, useMemo, useRef, useState, type PointerEvent } from 'react';
import { Link } from 'react-router-dom';
import {
  api,
  ApiError,
  type BestPrediction,
  type CalibrationBucket,
  type LedgerEntry,
  type ProfileResponse,
} from '../api';
import { AnimatedNumber } from '../components/AnimatedNumber';
import { Avatar } from '../components/Avatar';
import { CoinIcon } from '../components/CoinIcon';
import { Skeleton } from '../components/Skeleton';
import { useToast } from '../components/Toast';
import { formatMoney, formatSigned, formatSince, pluralize } from '../format';
import { shareInvite } from '../invite';
import {
  amountTone,
  balanceChangeLabel,
  balanceChart,
  balanceSeries,
  bestPredictionLabel,
  CALIBRATION_BOX,
  calibrationLabel,
  calibrationPoints,
  calibrationTip,
  daysAgo,
  formatLedgerDate,
  nearestPoint,
  pseudonymError,
  PSEUDONYM_MAX,
} from '../profile';
import { useSession } from '../session';
import { tg } from '../telegram';
import './profile.css';

type LoadState = 'loading' | 'ready' | 'error';

const CHART = { w: 358, h: 140, plot: 318, top: 6, bottom: 120 };
const DAYS = 30;
const LEDGER_PREVIEW = 10;

export function ProfileScreen() {
  const { me, balance, setMe } = useSession();
  const [data, setData] = useState<ProfileResponse | null>(null);
  const [state, setState] = useState<LoadState>('loading');
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState('loading');
    api
      .profile()
      .then((res) => {
        if (cancelled) return;
        setData(res);
        setMe(res.me);
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
  }, [attempt, setMe]);

  if (!me) return null;

  return (
    <div className="screen profile">
      <header className="profile-head">
        <Avatar name={me.name} photo={me.photo_url} size={64} />
        <div className="profile-who">
          <h1 className="profile-name">{me.name}</h1>
          <span className="sub small">
            В рейтинге: {me.display_name}
            {state === 'ready' && data?.joined_at ? ` · ${formatSince(data.joined_at)}` : ''}
          </span>
        </div>
      </header>

      {state === 'error' && (
        <div className="feed-message" role="alert">
          <p>Не удалось загрузить профиль. {error}</p>
          <button type="button" className="button-secondary" onClick={() => setAttempt((n) => n + 1)}>
            Повторить
          </button>
        </div>
      )}

      <BalanceCard history={state === 'ready' && data ? data.balance_history : null} balance={balance} />

      {state === 'ready' && data ? (
        <Stats stats={data.stats} />
      ) : (
        state === 'loading' && (
          <div className="profile-stats" aria-hidden="true">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="profile-stat">
                <Skeleton width={70} height={12} />
                <Skeleton width={56} height={22} />
              </div>
            ))}
          </div>
        )
      )}

      {state === 'ready' && data && data.calibration.length > 0 && <Calibration buckets={data.calibration} />}
      {state === 'ready' && data?.best_prediction && <BestPredictionCard best={data.best_prediction} />}

      <InviteCard link={me.referral_link} invited={state === 'ready' && data ? data.invited : null} />
      <NameCard />

      {state === 'ready' && data && <Ledger entries={data.ledger} />}
    </div>
  );
}

function BalanceCard({ history, balance }: { history: ProfileResponse['balance_history'] | null; balance: number }) {
  const [scrub, setScrub] = useState<number | null>(null);
  const now = useMemo(() => new Date(), []);
  const series = useMemo(() => (history ? balanceSeries(history, balance, now, DAYS) : null), [history, balance, now]);
  const chart = useMemo(
    () =>
      series
        ? balanceChart(series, {
            width: CHART.plot,
            top: CHART.top,
            bottom: CHART.bottom,
            start: now.getTime() - DAYS * 24 * 60 * 60 * 1000,
            end: now.getTime(),
          })
        : null,
    [series, now],
  );

  const point = chart && scrub !== null ? chart.points[scrub] : null;
  const last = chart ? chart.points[chart.points.length - 1] : null;

  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    if (!chart) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * CHART.w;
    const i = nearestPoint(chart.points, x);
    if (i !== scrub) {
      setScrub(i);
      tg.HapticFeedback.selection();
    }
  };

  return (
    <section aria-label="Баланс" className="card profile-balance">
      <div className="profile-balance-top">
        <div className="profile-balance-main">
          <span className="sub small">Баланс</span>
          <span className="profile-balance-value">
            <CoinIcon size={26} />
            {point ? formatMoney(point.v) : <AnimatedNumber value={balance} />}
          </span>
        </div>
        <span className="sub small profile-balance-when">
          {point ? daysAgo(point.t, now) : series ? balanceChangeLabel(series) : ''}
        </span>
      </div>
      {chart && series ? (
        <svg
          width={CHART.w}
          height={CHART.h}
          viewBox={`0 0 ${CHART.w} ${CHART.h}`}
          role="img"
          aria-label={`Баланс за ${DAYS} дней: с ${formatMoney(series[0].v)} до ${formatMoney(series[series.length - 1].v)}`}
          className="profile-chart"
          onPointerMove={onMove}
          onPointerDown={onMove}
          onPointerLeave={() => setScrub(null)}
        >
          {chart.ticks.map((t) => (
            <g key={t.value}>
              <line x1="0" y1={t.y + 0.5} x2={CHART.plot} y2={t.y + 0.5} stroke="var(--line)" strokeWidth="1" />
              <text x={CHART.w} y={t.y + 4} textAnchor="end" fontSize="11" fill="var(--sub)">
                {formatMoney(t.value)}
              </text>
            </g>
          ))}
          <path d={chart.path} fill="none" stroke="var(--yes)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
          {point ? (
            <g>
              <line x1={point.x} y1={CHART.top} x2={point.x} y2={CHART.bottom} stroke="var(--sub)" strokeWidth="1" strokeDasharray="3 3" />
              <circle cx={point.x} cy={point.y} r="4" fill="var(--yes)" stroke="var(--card)" strokeWidth="2" />
            </g>
          ) : (
            last && <circle cx={last.x} cy={last.y} r="4" fill="var(--yes)" stroke="var(--card)" strokeWidth="2" />
          )}
          <text x="0" y="136" fontSize="11" fill="var(--sub)">
            {DAYS} дней назад
          </text>
          <text x={CHART.plot} y="136" textAnchor="end" fontSize="11" fill="var(--sub)">
            сегодня
          </text>
        </svg>
      ) : (
        <Skeleton height={140} radius={10} />
      )}
    </section>
  );
}

function Stats({ stats }: { stats: ProfileResponse['stats'] }) {
  const tiles = [
    { label: 'Прибыль', value: formatSigned(stats.profit, 0), sub: 'без бонусов' },
    {
      label: 'Точность',
      value: stats.accuracy === null ? '—' : Math.round(stats.accuracy * 100) + '%',
      sub: stats.accuracy === null ? 'пока нет итогов' : `${stats.correct} из ${stats.evaluated} верно`,
    },
    { label: 'Место', value: stats.rank === null ? '—' : formatMoney(stats.rank, 0), sub: 'по прибыли' },
    {
      label: 'Создано рынков',
      value: formatMoney(stats.markets_created, 0),
      sub: pluralize(stats.disputes_lost, 'проигранный спор', 'проигранных спора', 'проигранных споров'),
    },
  ];
  return (
    <div className="profile-stats">
      {tiles.map((t) => (
        <div key={t.label} className="profile-stat">
          <span className="sub profile-stat-label">{t.label}</span>
          <span className="profile-stat-value">{t.value}</span>
          {t.sub && <span className="sub profile-stat-label">{t.sub}</span>}
        </div>
      ))}
    </div>
  );
}

const CAL = { w: 358, h: 232, tipW: 164, tipH: 40 };

function Calibration({ buckets }: { buckets: CalibrationBucket[] }) {
  const [active, setActive] = useState<number | null>(null);
  const points = useMemo(() => calibrationPoints(buckets), [buckets]);
  const { left, right, top, bottom } = CALIBRATION_BOX;
  const mid = (top + bottom) / 2;
  const line = 'M' + points.map((p) => `${p.x} ${p.y}`).join(' L');
  const point = active !== null ? points[active] : null;
  const tip = point && calibrationTip(point);
  const tipX = point ? Math.min(Math.max(point.x - CAL.tipW / 2, 0), CAL.w - CAL.tipW) : 0;
  const tipY = point ? (point.y - 52 < 0 ? point.y + 14 : point.y - 52) : 0;

  const show = (i: number) => {
    if (i === active) return;
    setActive(i);
    tg.HapticFeedback.selection();
  };

  return (
    <section aria-label="Калибровка" className="card profile-calibration">
      <h2 className="profile-section-title">Насколько точны твои вероятности</h2>
      <p className="sub small profile-text">
        Чем ближе точки к пунктиру, тем честнее твои оценки. Выше пунктира — ты недооцениваешь, ниже — переоцениваешь.
      </p>
      <svg
        width={CAL.w}
        height={CAL.h}
        viewBox={`0 0 ${CAL.w} ${CAL.h}`}
        role="img"
        aria-label={calibrationLabel(points)}
        className="profile-chart"
      >
        {[(mid + bottom) / 2, mid, (top + mid) / 2].map((y) => (
          <line key={y} x1={left} y1={y} x2={right} y2={y} stroke="var(--line)" strokeWidth="1" />
        ))}
        <line x1={left} y1={bottom} x2={right} y2={bottom} stroke="var(--sub)" strokeWidth="1" />
        <text x="28" y={bottom + 4} textAnchor="end" fontSize="11" fill="var(--sub)">0%</text>
        <text x="28" y={mid + 4} textAnchor="end" fontSize="11" fill="var(--sub)">50%</text>
        <text x="28" y={top + 4} textAnchor="end" fontSize="11" fill="var(--sub)">100%</text>
        <text x={left} y="206" textAnchor="middle" fontSize="11" fill="var(--sub)">0%</text>
        <text x={(left + right) / 2} y="206" textAnchor="middle" fontSize="11" fill="var(--sub)">50%</text>
        <text x={right} y="206" textAnchor="end" fontSize="11" fill="var(--sub)">100%</text>
        <text x={(left + right) / 2} y="226" textAnchor="middle" fontSize="11" fill="var(--sub)">
          цена, по которой покупал → как часто сбывалось
        </text>
        <line x1={left} y1={bottom} x2={right} y2={top} stroke="var(--sub)" strokeWidth="1" strokeDasharray="4 4" />
        {points.length > 1 && (
          <path d={line} fill="none" stroke="var(--yes)" strokeWidth="2" strokeLinejoin="round" strokeOpacity="0.5" />
        )}
        {points.map((p, i) => (
          <g key={p.price}>
            <circle cx={p.x} cy={p.y} r="5" fill="var(--yes)" stroke="var(--card)" strokeWidth="2" />
            <circle
              cx={p.x}
              cy={p.y}
              r="16"
              fill="transparent"
              className="profile-calibration-hit"
              onPointerEnter={() => show(i)}
              onPointerDown={() => show(i)}
              onPointerLeave={() => setActive(null)}
            />
          </g>
        ))}
        {point && tip && (
          <g pointerEvents="none">
            <rect x={tipX} y={tipY} width={CAL.tipW} height={CAL.tipH} rx="8" fill="var(--ink)" />
            <text x={tipX + 10} y={tipY + 16} fontSize="12" fontWeight="700" fill="var(--card)">
              {tip[0]}
            </text>
            <text x={tipX + 10} y={tipY + 31} fontSize="11" fill="var(--card)">
              {tip[1]}
            </text>
          </g>
        )}
      </svg>
    </section>
  );
}

function BestPredictionCard({ best }: { best: BestPrediction }) {
  return (
    <section aria-label="Лучший прогноз" className="card profile-best">
      <span className="sub profile-best-label">ЛУЧШИЙ ПРОГНОЗ</span>
      <Link to={`/market/${best.market.id}`} className="profile-best-title">
        {best.market.title}
      </Link>
      <div className="profile-best-row">
        <span className="sub">{bestPredictionLabel(best)}</span>
        <span className={`profile-best-gain is-${amountTone(best.gain)}`}>{formatSigned(best.gain, 0)}</span>
      </div>
    </section>
  );
}

function InviteCard({ link, invited }: { link: string; invited: number | null }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef(0);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      tg.HapticFeedback.notification('success');
      setCopied(true);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard may be blocked in the WebView; the share button still works.
    }
  };

  return (
    <section aria-label="Пригласить друга" className="card profile-invite">
      <h2 className="profile-section-title">Пригласи друга</h2>
      <p className="sub small profile-text">
        Вы оба получите монеты, когда друг начнёт делать прогнозы.
        {invited !== null && ` Приглашено ${formatMoney(invited, 0)}.`}
      </p>
      <div className="profile-link-row">
        <label htmlFor="ref-link" className="visually-hidden">
          Твоя ссылка
        </label>
        <input id="ref-link" readOnly value={link} className="profile-link" onFocus={(e) => e.currentTarget.select()} />
        <button type="button" className="small-button" onClick={copy}>
          {copied ? 'Скопировано' : 'Копировать'}
        </button>
      </div>
      <button type="button" className="button-primary is-ink button-block" onClick={() => shareInvite(link, 'profile')}>
        Пригласить друга
      </button>
    </section>
  );
}

function NameCard() {
  const { me, setMe } = useSession();
  const toast = useToast();
  const [pseudonym, setPseudonym] = useState(me?.pseudonym ?? '');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const saved = me?.pseudonym ?? '';
  useEffect(() => setPseudonym(saved), [saved]);

  if (!me) return null;
  const changed = pseudonym.trim() !== me.pseudonym;

  const choose = (real: boolean) => {
    if (real === me.show_real_name) return;
    tg.HapticFeedback.selection();
    const before = me;
    setMe({ ...me, show_real_name: real, display_name: real ? me.name : me.pseudonym });
    api
      .updateMe({ show_real_name: real })
      .then(setMe)
      .catch((err: unknown) => {
        setMe(before);
        toast(err instanceof ApiError ? err.message : 'Не получилось. Попробуйте ещё раз.', 'error');
      });
  };

  const save = () => {
    const problem = pseudonymError(pseudonym);
    setError(problem);
    if (problem || saving) return;
    setSaving(true);
    api
      .updateMe({ pseudonym: pseudonym.trim() })
      .then((res) => {
        setMe(res);
        tg.HapticFeedback.notification('success');
        toast('Сохранено');
      })
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : 'Не получилось. Попробуйте ещё раз.'))
      .finally(() => setSaving(false));
  };

  return (
    <section aria-label="Как меня видят в рейтинге" className="card profile-name-card">
      <h2 className="profile-section-title">Как меня видят в рейтинге</h2>
      <div role="radiogroup" aria-label="Как меня видят в рейтинге" className="profile-options">
        <button
          type="button"
          role="radio"
          aria-checked={!me.show_real_name}
          className={`radio-option ${!me.show_real_name ? 'is-active' : ''}`}
          onClick={() => choose(false)}
        >
          <span className="radio-dot" aria-hidden="true">
            <span />
          </span>
          <span className="radio-text">
            <span className="radio-label">Псевдоним</span>
            <span className="sub small">{me.pseudonym}</span>
          </span>
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={me.show_real_name}
          className={`radio-option ${me.show_real_name ? 'is-active' : ''}`}
          onClick={() => choose(true)}
        >
          <span className="radio-dot" aria-hidden="true">
            <span />
          </span>
          <span className="radio-text">
            <span className="radio-label">{me.name}</span>
            <span className="sub small">Имя из Telegram</span>
          </span>
        </button>
      </div>
      <div className="profile-pseudonym">
        <label htmlFor="pseudonym" className="profile-field-label">
          Псевдоним
        </label>
        <div className="profile-link-row">
          <input
            id="pseudonym"
            className="profile-link"
            value={pseudonym}
            maxLength={PSEUDONYM_MAX}
            onChange={(e) => {
              setPseudonym(e.target.value);
              setError(null);
            }}
            onKeyDown={(e) => e.key === 'Enter' && save()}
            aria-invalid={!!error || undefined}
            enterKeyHint="done"
            autoComplete="off"
          />
          <button type="button" className="small-button" disabled={!changed || saving} onClick={save}>
            Сохранить
          </button>
        </div>
        {error && <span className="profile-error">{error}</span>}
      </div>
    </section>
  );
}

function Ledger({ entries }: { entries: LedgerEntry[] }) {
  const [all, setAll] = useState(false);
  const now = useMemo(() => new Date(), []);
  const shown = all ? entries : entries.slice(0, LEDGER_PREVIEW);
  return (
    <section aria-label="История операций" className="card profile-ledger">
      <h2 className="profile-section-title">История операций</h2>
      {entries.length === 0 ? (
        <p className="sub small profile-text">Операций пока нет.</p>
      ) : (
        <ul className="ledger-list">
          {shown.map((e, i) => (
            <li key={`${e.at}-${i}`} className="ledger-row">
              <span className="ledger-main">
                <span className="ledger-label">{e.label}</span>
                {e.market && (
                  <Link to={`/market/${e.market.id}`} className="ledger-market">
                    {e.market.title}
                  </Link>
                )}
                <span className="sub ledger-date">{formatLedgerDate(e.at, now)}</span>
              </span>
              <span className={`ledger-amount is-${amountTone(e.amount)}`}>{formatSigned(e.amount)}</span>
            </li>
          ))}
        </ul>
      )}
      {!all && entries.length > LEDGER_PREVIEW && (
        <button type="button" className="button-secondary profile-more" onClick={() => setAll(true)}>
          Показать все
        </button>
      )}
    </section>
  );
}
