// Profile screen from docs/mockups/Profile.dc.html.
import { useEffect, useMemo, useRef, useState, type PointerEvent } from 'react';
import { Link } from 'react-router-dom';
import {
  api,
  ApiError,
  type BestPrediction,
  type CalibrationBucket,
  type CategoryStat,
  type HistoryPeriod,
  type LedgerEntry,
  type NotifyKind,
  type ProfileHistoryPoint,
  type ProfileResponse,
  type ReferralSummary,
} from '../api';
import { BottomSheet } from '../components/BottomSheet';
import { NotificationBell } from '../components/NotificationBell';
import { AnimatedNumber } from '../components/AnimatedNumber';
import { Avatar } from '../components/Avatar';
import { lineYAt } from '../chartMotion';
import { CoinIcon } from '../components/CoinIcon';
import { LiveLine } from '../components/LiveLine';
import { Skeleton } from '../components/Skeleton';
import { useToast } from '../components/Toast';
import { formatDayMonth, formatMoney, formatSigned, formatSince, pluralize } from '../format';
import { shareInvite } from '../invite';
import { periodWord, PERIODS } from '../market';
import { NOTIFY_NOTE, NOTIFY_OPTIONS, notifyCountLabel, toggleNotify } from '../notifications';
import {
  amountTone,
  balanceChart,
  bestPredictionLabel,
  bestPredictionsOf,
  CALIBRATION_BOX,
  calibrationLabel,
  calibrationPoints,
  calibrationTip,
  categoryRows,
  formatLedgerDate,
  historySeries,
  inviteText,
  legacyHistory,
  nearestPoint,
  PERIOD_START_LABEL,
  periodWindow,
  pseudonymError,
  PSEUDONYM_MAX,
  scrubLabel,
  seriesChangeLabel,
  type ChartMetric,
} from '../profile';
import { useSession } from '../session';
import { tg } from '../telegram';
import './profile.css';

type LoadState = 'loading' | 'ready' | 'error';

const CHART = { w: 358, h: 140, plot: 318, top: 6, bottom: 120 };
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
        <NotificationBell />
      </header>

      {state === 'error' && (
        <div className="feed-message" role="alert">
          <p>Не удалось загрузить профиль. {error}</p>
          <button type="button" className="button-secondary" onClick={() => setAttempt((n) => n + 1)}>
            Повторить
          </button>
        </div>
      )}

      <BalanceCard fallback={state === 'ready' && data ? data.balance_history : null} balance={balance} />

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
      {state === 'ready' && data && <Categories stats={data.categories} />}
      {state === 'ready' && data && <BestPredictions items={bestPredictionsOf(data)} />}

      <InviteCard
        link={me.referral_link}
        invited={state === 'ready' && data ? data.invited : null}
        referral={state === 'ready' && data ? data.referral : undefined}
      />
      {me.notify && <SettingsNav />}
      <NameCard />

      {state === 'ready' && data && <Ledger entries={data.ledger} />}
    </div>
  );
}

const METRICS: { key: ChartMetric; label: string }[] = [
  { key: 'balance', label: 'Баланс' },
  { key: 'profit', label: 'Прибыль' },
];

type HistoryCache = Partial<Record<HistoryPeriod, ProfileHistoryPoint[]>>;

/**
 * Balance and profit by period (GET /api/profile/history). An older server
 * without the endpoint gets the balance from `balance_history` and no profit.
 */
function BalanceCard({ fallback, balance }: { fallback: ProfileResponse['balance_history'] | null; balance: number }) {
  const [period, setPeriod] = useState<HistoryPeriod>('m');
  const [metric, setMetric] = useState<ChartMetric>('balance');
  const [cache, setCache] = useState<HistoryCache>({});
  const [legacy, setLegacy] = useState(false);
  const [scrub, setScrub] = useState<number | null>(null);

  useEffect(() => {
    if (legacy || cache[period]) return;
    let cancelled = false;
    api
      .profileHistory(period)
      .then((res) => {
        if (!cancelled) setCache((prev) => ({ ...prev, [period]: res.points }));
      })
      .catch(() => {
        if (cancelled) return;
        setLegacy(true);
        setMetric('balance');
        setPeriod('m');
      });
    return () => {
      cancelled = true;
    };
  }, [period, legacy, cache]);

  const points = legacy ? (fallback ? legacyHistory(fallback) : null) : (cache[period] ?? null);
  const now = useMemo(() => new Date(), [points]);
  const series = useMemo(
    () => (points ? historySeries(points, metric, period, metric === 'balance' ? balance : null, now) : null),
    [points, metric, period, balance, now],
  );
  const frame = useMemo(() => (points ? periodWindow(period, points, now) : null), [points, period, now]);
  const chart = useMemo(
    () =>
      series && frame
        ? balanceChart(series, { width: CHART.plot, top: CHART.top, bottom: CHART.bottom, ...frame })
        : null,
    [series, frame],
  );
  useEffect(() => setScrub(null), [chart]);
  const values = useMemo(() => (chart ? chart.points.map((p) => ({ x: p.x, y: p.v })) : []), [chart]);
  // While the line draws itself the big number runs along with its tip.
  const [headX, setHeadX] = useState<number | null>(null);

  const point = chart && scrub !== null ? (chart.points[scrub] ?? null) : null;
  const last = chart ? chart.points[chart.points.length - 1] : null;
  const profit = metric === 'profit';
  const format = (v: number) => (profit ? formatSigned(v, 0) : formatMoney(v));
  const label = profit ? 'Прибыль' : 'Баланс';
  const startLabel =
    period === 'all' && frame ? formatDayMonth(new Date(frame.start).toISOString(), now) : PERIOD_START_LABEL[period];

  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    if (!chart || headX !== null) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * CHART.w;
    const i = nearestPoint(chart.points, x);
    if (i !== scrub) {
      setScrub(i);
      tg.HapticFeedback.selection();
    }
  };

  const pickPeriod = (p: HistoryPeriod) => {
    if (p === period) return;
    tg.HapticFeedback.selection();
    setPeriod(p);
  };

  const pickMetric = (m: ChartMetric) => {
    if (m === metric) return;
    tg.HapticFeedback.selection();
    setMetric(m);
  };

  return (
    <section aria-label={label} className="card profile-balance">
      {!legacy && (
        <div role="group" aria-label="Что показать" className="profile-metrics">
          {METRICS.map((m) => (
            <button
              key={m.key}
              type="button"
              className={`chip-button ${m.key === metric ? 'is-active' : ''}`}
              aria-pressed={m.key === metric}
              onClick={() => pickMetric(m.key)}
            >
              {m.label}
            </button>
          ))}
        </div>
      )}
      <div className="profile-balance-top">
        <div className="profile-balance-main">
          <span className="sub small">{label}</span>
          <span className="profile-balance-value">
            {!profit && <CoinIcon size={26} />}
            {point
              ? format(point.v)
              : headX !== null && values.length > 0
                ? format(Math.round(lineYAt(values, headX, 'linear')))
                : profit
                  ? last
                    ? format(last.v)
                    : '—'
                  : <AnimatedNumber value={balance} />}
          </span>
          {profit && <span className="sub small">без бонусов</span>}
        </div>
        <span className="sub small profile-balance-when">
          {point ? scrubLabel(point.t, period, now) : series ? seriesChangeLabel(series, period) : ''}
        </span>
      </div>
      {chart && series ? (
        <svg
          width={CHART.w}
          height={CHART.h}
          viewBox={`0 0 ${CHART.w} ${CHART.h}`}
          role="img"
          aria-label={`${label} ${periodWord(period)}: с ${format(series[0].v)} до ${format(series[series.length - 1].v)}`}
          className="profile-chart live-chart"
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
          <LiveLine
            points={chart.points}
            line={chart.path}
            area={chart.area}
            curve="linear"
            showEnd={!point}
            replayKey={`${metric}-${period}`}
            onDraw={setHeadX}
          />
          {point && (
            <g>
              <line x1={point.x} y1={CHART.top} x2={point.x} y2={CHART.bottom} stroke="var(--sub)" strokeWidth="1" strokeDasharray="3 3" />
              <circle cx={point.x} cy={point.y} r="4" fill="var(--yes)" stroke="var(--card)" strokeWidth="2" />
            </g>
          )}
          <text x="0" y="136" fontSize="11" fill="var(--sub)">
            {startLabel}
          </text>
          <text x={CHART.plot} y="136" textAnchor="end" fontSize="11" fill="var(--sub)">
            сегодня
          </text>
        </svg>
      ) : (
        <Skeleton height={140} radius={10} />
      )}
      {!legacy && (
        <div role="group" aria-label="Период" className="profile-periods">
          {PERIODS.map((p) => (
            <button
              key={p.key}
              type="button"
              className={`chip-button ${p.key === period ? 'is-active' : ''}`}
              aria-pressed={p.key === period}
              onClick={() => pickPeriod(p.key)}
            >
              {p.label}
            </button>
          ))}
        </div>
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

function Categories({ stats }: { stats: CategoryStat[] | undefined }) {
  const rows = useMemo(() => categoryRows(stats), [stats]);
  if (rows.length === 0) return null;
  return (
    <section aria-label="По категориям" className="card profile-categories">
      <h2 className="profile-section-title">По категориям</h2>
      <ul className="profile-cat-list">
        {rows.map((r) => (
          <li key={r.slug} className="profile-cat">
            <div className="profile-cat-row">
              <span className="profile-cat-label">{r.label}</span>
              <span className={`profile-cat-profit is-${r.tone}`}>{r.profit}</span>
            </div>
            <div className="profile-cat-bar" aria-hidden="true">
              <span style={{ width: `${Math.round(r.bar * 100)}%` }} />
            </div>
            <span className="sub profile-cat-sub">
              Точность {r.accuracy} · {r.predictions}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function BestPredictions({ items }: { items: BestPrediction[] }) {
  if (items.length === 0) return null;
  const many = items.length > 1;
  return (
    <section aria-label={many ? 'Лучшие прогнозы' : 'Лучший прогноз'} className="card profile-best">
      <span className="sub profile-best-label">{many ? 'ЛУЧШИЕ ПРОГНОЗЫ' : 'ЛУЧШИЙ ПРОГНОЗ'}</span>
      <ul className="profile-best-list">
        {items.map((best) => (
          <li key={best.market.id} className="profile-best-item">
            <Link to={`/market/${best.market.id}`} className="profile-best-title">
              {best.market.title}
            </Link>
            <div className="profile-best-row">
              <span className="sub">{bestPredictionLabel(best)}</span>
              <span className={`profile-best-gain is-${amountTone(best.gain)}`}>{formatSigned(best.gain, 0)}</span>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function InviteCard({
  link,
  invited,
  referral,
}: {
  link: string;
  invited: number | null;
  referral: ReferralSummary | undefined;
}) {
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
      <p className="sub small profile-text">{inviteText(referral, invited)}</p>
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

/** «Настройки» list from the mockup; for now only «Уведомления». */
function SettingsNav() {
  const { me } = useSession();
  const [open, setOpen] = useState(false);
  if (!me?.notify) return null;
  return (
    <>
      <nav aria-label="Настройки" className="card profile-settings">
        <button
          type="button"
          className="profile-setting"
          onClick={() => {
            tg.HapticFeedback.selection();
            setOpen(true);
          }}
        >
          <span className="profile-setting-label">Уведомления</span>
          <span className="sub profile-setting-value">{notifyCountLabel(me.notify)}</span>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--sub)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M9 5l7 7-7 7" />
          </svg>
        </button>
      </nav>
      <BottomSheet open={open} onClose={() => setOpen(false)} label="Уведомления в боте">
        <NotifySettings />
      </BottomSheet>
    </>
  );
}

/** Five switches; optimistic PATCH /api/me, the one switch rolls back on error. */
function NotifySettings() {
  const { me, setMe, patchMe } = useSession();
  const toast = useToast();
  const latest = useRef(0);
  if (!me?.notify) return null;
  const notify = me.notify;

  const flip = (key: NotifyKind) => {
    const value = toggleNotify(notify, key)[key];
    tg.HapticFeedback.impact('light');
    patchMe((prev) => (prev.notify ? { ...prev, notify: { ...prev.notify, [key]: value } } : prev));
    latest.current += 1;
    const request = latest.current;
    api
      .updateMe({ notify: { [key]: value } })
      .then((res) => {
        // A newer toggle is in flight: its answer brings the final state.
        if (request === latest.current) setMe(res);
      })
      .catch((err: unknown) => {
        patchMe((prev) => (prev.notify ? { ...prev, notify: { ...prev.notify, [key]: !value } } : prev));
        tg.HapticFeedback.notification('error');
        toast(err instanceof ApiError ? err.message : 'Не получилось. Попробуйте ещё раз.', 'error');
      });
  };

  return (
    <div className="profile-notify">
      <h2 className="profile-section-title">Уведомления в боте</h2>
      <ul className="profile-notify-list">
        {NOTIFY_OPTIONS.map((o) => (
          <li key={o.key} className="profile-notify-row">
            <span className="profile-notify-text" id={`notify-${o.key}`}>
              <span className="profile-notify-label">{o.label}</span>
              <span className="sub small">{o.hint}</span>
            </span>
            <button
              type="button"
              role="switch"
              className="switch"
              aria-checked={notify[o.key]}
              aria-labelledby={`notify-${o.key}`}
              onClick={() => flip(o.key)}
            />
          </li>
        ))}
      </ul>
      <p className="sub small profile-text">{NOTIFY_NOTE}</p>
    </div>
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
