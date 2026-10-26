// Rating screen from docs/mockups/Rating.dc.html.
import { useEffect, useRef, useState } from 'react';
import { api, ApiError, type RatingResponse } from '../api';
import { Avatar } from '../components/Avatar';
import { Skeleton } from '../components/Skeleton';
import { shareInvite } from '../invite';
import { formatRatingValue, hasOthers, medalOf, type RatingBy, type RatingPeriod, type RatingScope } from '../rating';
import { useSession } from '../session';
import { tg } from '../telegram';
import './rating.css';

const SCOPES: { key: RatingScope; label: string }[] = [
  { key: 'all', label: 'Все' },
  { key: 'friends', label: 'Друзья' },
];

const METRICS: { key: RatingBy; label: string }[] = [
  { key: 'profit', label: 'Прибыль' },
  { key: 'accuracy', label: 'Точность' },
];

const PERIODS: { key: RatingPeriod; label: string }[] = [
  { key: 'week', label: 'Неделя' },
  { key: 'month', label: 'Месяц' },
  { key: 'all', label: 'Всё время' },
];

// Filters survive switching tabs.
let lastFilters: { scope: RatingScope; by: RatingBy; period: RatingPeriod } = { scope: 'all', by: 'profit', period: 'all' };

type LoadState = 'loading' | 'ready' | 'error';

export function RatingScreen() {
  const { me } = useSession();
  const [scope, setScope] = useState<RatingScope>(lastFilters.scope);
  const [by, setBy] = useState<RatingBy>(lastFilters.by);
  const [period, setPeriod] = useState<RatingPeriod>(lastFilters.period);
  const [data, setData] = useState<RatingResponse | null>(null);
  const [state, setState] = useState<LoadState>('loading');
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const generation = useRef(0);

  useEffect(() => {
    lastFilters = { scope, by, period };
    const gen = ++generation.current;
    setState('loading');
    api
      .rating({ scope, by, period })
      .then((res) => {
        if (gen !== generation.current) return;
        setData(res);
        setState('ready');
      })
      .catch((err: unknown) => {
        if (gen !== generation.current) return;
        setError(err instanceof ApiError ? err.message : 'Что-то пошло не так. Попробуйте ещё раз.');
        setState('error');
      });
  }, [scope, by, period, attempt]);

  const pick = <T,>(value: T, current: T, set: (v: T) => void) => {
    if (value === current) return;
    tg.HapticFeedback.selection();
    set(value);
  };

  const friendsEmpty = state === 'ready' && scope === 'friends' && data !== null && !hasOthers(data);
  const invite = () => me && shareInvite(me.referral_link, 'rating');

  return (
    <div className="screen rating">
      <header className="rating-header">
        <h1 className="rating-title">Рейтинг</h1>
        <div role="tablist" aria-label="Кого показывать" className="segmented rating-scopes">
          {SCOPES.map((s) => (
            <button
              key={s.key}
              type="button"
              role="tab"
              aria-selected={s.key === scope}
              className={`segmented-option ${s.key === scope ? 'is-on' : ''}`}
              onClick={() => pick(s.key, scope, setScope)}
            >
              {s.label}
            </button>
          ))}
        </div>
        <div className="rating-filters">
          <div role="group" aria-label="Показатель" className="rating-metrics">
            {METRICS.map((m) => (
              <button
                key={m.key}
                type="button"
                aria-pressed={m.key === by}
                className={`chip rating-chip ${m.key === by ? 'is-on' : ''}`}
                onClick={() => pick(m.key, by, setBy)}
              >
                {m.label}
              </button>
            ))}
          </div>
          <div role="group" aria-label="Период" className="rating-periods">
            {PERIODS.map((p) => (
              <button
                key={p.key}
                type="button"
                aria-pressed={p.key === period}
                className={`sort-tab rating-period ${p.key === period ? 'is-active' : ''}`}
                onClick={() => pick(p.key, period, setPeriod)}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
      </header>

      <div className="rating-body">
        {state === 'loading' && (
          <div className="rating-list" aria-busy="true">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="rating-row">
                <Skeleton width={28} height={28} radius={999} />
                <Skeleton width={36} height={36} radius={999} />
                <Skeleton width="50%" height={16} radius={6} />
              </div>
            ))}
          </div>
        )}

        {state === 'error' && (
          <div className="feed-message" role="alert">
            <p>Не удалось загрузить рейтинг. {error}</p>
            <button type="button" className="button-secondary" onClick={() => setAttempt((n) => n + 1)}>
              Повторить
            </button>
          </div>
        )}

        {friendsEmpty && (
          <div className="card rating-empty">
            <p>Здесь будут друзья: те, кого ты пригласишь, и тот, кто пригласил тебя.</p>
            <button type="button" className="button-primary is-ink" onClick={invite}>
              Пригласить друга
            </button>
          </div>
        )}

        {state === 'ready' && data && !friendsEmpty && data.items.length === 0 && (
          <div className="feed-message sub">
            {by === 'accuracy' ? 'Пока нет завершённых прогнозов за этот период.' : 'Пока здесь никого нет.'}
          </div>
        )}

        {state === 'ready' && data && !friendsEmpty && data.items.length > 0 && (
          <ol aria-label="Таблица лидеров" className="rating-list">
            {data.items.map((item) => {
              const medal = medalOf(item.rank);
              return (
                <li key={`${item.rank}-${item.display_name}`} className={`rating-row ${item.is_me ? 'is-me' : ''}`}>
                  <span className={`rating-rank ${medal ? `is-${medal}` : ''}`}>{item.rank}</span>
                  <Avatar name={item.display_name} photo={item.photo_url} />
                  <span className="rating-name">{item.is_me ? 'Ты' : item.display_name}</span>
                  <span className="rating-value">{formatRatingValue(item.value, by)}</span>
                </li>
              );
            })}
          </ol>
        )}

        {state === 'ready' && !friendsEmpty && (
          <p className="sub rating-footnote">Прибыль считается без бонусов.</p>
        )}
      </div>

      {state === 'ready' && data && !friendsEmpty && (
        <section aria-label="Твоё место" className="rating-me">
          <span className="rating-rank is-me">{data.me ? data.me.rank : '—'}</span>
          <span className="rating-me-text">
            <span className="rating-name">Ты</span>
            {!data.me && <span className="sub rating-me-sub">Пока не в рейтинге</span>}
          </span>
          <span className="rating-value">{data.me ? formatRatingValue(data.me.value, by) : '—'}</span>
        </section>
      )}
    </div>
  );
}
