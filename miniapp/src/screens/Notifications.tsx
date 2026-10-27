// «Уведомления» from docs/mockups/Notifications.dc.html.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError, type Notice, type NoticeFilter } from '../api';
import { Skeleton } from '../components/Skeleton';
import { useToast } from '../components/Toast';
import { formatTime } from '../format';
import { botChatLink } from '../market';
import {
  countUnread,
  filterNotices,
  groupNoticesByDay,
  markNoticesRead,
  NOTICE_FILTERS,
  noticeAction,
  noticeIcon,
  type NoticeIcon,
} from '../notifications';
import { useSession } from '../session';
import { isTelegram, tg } from '../telegram';
import './notifications.css';

type Lists = Partial<Record<NoticeFilter, Notice[]>>;

export function NotificationsScreen() {
  const { me, setUnread } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  const [filter, setFilter] = useState<NoticeFilter>('all');
  const [lists, setLists] = useState<Lists>({});
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const list = lists[filter];

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .notifications(filter)
      .then((res) => {
        if (cancelled) return;
        setLists((prev) => ({ ...prev, [filter]: filterNotices(res.items, filter) }));
        setUnread(res.unread);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : 'Что-то пошло не так. Попробуйте ещё раз.');
      });
    return () => {
      cancelled = true;
    };
  }, [filter, attempt, setUnread]);

  const now = useMemo(() => new Date(), [list]);
  const days = useMemo(() => (list ? groupNoticesByDay(list, now) : null), [list, now]);

  const unread = me?.unread_notifications ?? countUnread(lists.all ?? list ?? []);
  const nothingToRead = unread === 0 && countUnread(list ?? []) === 0;

  const applyRead = (ids?: number[]) =>
    setLists((prev) => {
      const next: Lists = {};
      for (const [key, items] of Object.entries(prev) as [NoticeFilter, Notice[]][]) {
        next[key] = markNoticesRead(items, ids);
      }
      return next;
    });

  const readOne = useCallback(
    (n: Notice) => {
      if (n.read) return;
      applyRead([n.id]);
      setUnread((u) => u - 1);
      api
        .readNotifications([n.id])
        .then((res) => setUnread(res.unread))
        .catch(() => {});
    },
    [setUnread],
  );

  const readAll = () => {
    if (nothingToRead) return;
    tg.HapticFeedback.impact('light');
    const before = lists;
    const unreadBefore = unread;
    applyRead();
    setUnread(0);
    api
      .readNotifications()
      .then((res) => setUnread(res.unread))
      .catch((err: unknown) => {
        setLists(before);
        setUnread(unreadBefore);
        toast(err instanceof ApiError ? err.message : 'Не получилось. Попробуйте ещё раз.', 'error');
      });
  };

  const act = (n: Notice) => {
    readOne(n);
    if (!n.market) return;
    tg.HapticFeedback.impact('light');
    if (n.action === 'resolve') {
      const link = botChatLink(me?.referral_link, `resolve_${n.market.id}`);
      if (link) {
        tg.openTelegramLink(link);
        api.event('open_bot', { market: n.market.id, reason: 'resolve' }).catch(() => {});
        return;
      }
    }
    navigate(`/market/${n.market.id}`);
  };

  const pick = (key: NoticeFilter) => {
    if (key === filter) return;
    tg.HapticFeedback.selection();
    setFilter(key);
  };

  const back = () => {
    if (window.history.state && window.history.state.idx > 0) navigate(-1);
    else navigate('/', { replace: true });
  };

  return (
    <div className="screen nt">
      <header className="nt-header">
        {isTelegram() ? (
          <span className="nt-header-side" />
        ) : (
          <button type="button" className="nt-icon-button" aria-label="Назад" onClick={back}>
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M15 5l-7 7 7 7" />
            </svg>
          </button>
        )}
        <h1 className="nt-title">Уведомления</h1>
        <span className="nt-header-side is-end">
          <button type="button" className="nt-read-all" disabled={nothingToRead} onClick={readAll}>
            Прочитать все
          </button>
        </span>
      </header>

      <nav aria-label="Фильтр" className="chips-row nt-filters">
        {NOTICE_FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            className={`chip nt-chip ${f.key === filter ? 'is-active' : ''}`}
            aria-pressed={f.key === filter}
            onClick={() => pick(f.key)}
          >
            {f.label}
          </button>
        ))}
      </nav>

      <div className="nt-body">
        {error && !list ? (
          <div className="feed-message" role="alert">
            <p className="sub">Не удалось загрузить уведомления. {error}</p>
            <button type="button" className="button-secondary" onClick={() => setAttempt((n) => n + 1)}>
              Повторить
            </button>
          </div>
        ) : !days ? (
          <NoticesSkeleton />
        ) : days.length === 0 ? (
          <div className="nt-empty sub">Здесь пока пусто</div>
        ) : (
          days.map((day) => (
            <section key={day.title} aria-label={day.title} className="nt-day">
              <h2 className="nt-day-title sub">{day.title}</h2>
              <ul className="nt-list">
                {day.items.map((n) => (
                  <NoticeItem key={n.id} notice={n} onRead={readOne} onAction={act} />
                ))}
              </ul>
            </section>
          ))
        )}
        <p className="nt-note sub">
          Самое важное — выигрыши, итоги и напоминания — приходит ещё и в бота. Что присылать, настраивается в профиле.
        </p>
      </div>
    </div>
  );
}

function NoticeItem({
  notice: n,
  onRead,
  onAction,
}: {
  notice: Notice;
  onRead: (n: Notice) => void;
  onAction: (n: Notice) => void;
}) {
  const icon = noticeIcon(n);
  const action = noticeAction(n);
  return (
    <li className={`nt-item ${n.read ? '' : 'is-unread'}`} onClick={() => onRead(n)}>
      <span className={`nt-icon is-${icon}`}>
        <Icon kind={icon} />
      </span>
      <div className="nt-main">
        <div className="nt-line">
          <span className="nt-text">
            <b>{n.title}</b> {n.text}
          </span>
          {!n.read && <span className="nt-dot" aria-label="Не прочитано" />}
        </div>
        <span className="nt-time sub">{formatTime(n.at)}</span>
        {action && (
          <button
            type="button"
            className={`nt-action ${action.strong ? 'is-strong' : ''}`}
            onClick={(e) => {
              e.stopPropagation();
              onAction(n);
            }}
          >
            {action.label}
          </button>
        )}
      </div>
    </li>
  );
}

function Icon({ kind }: { kind: NoticeIcon }) {
  switch (kind) {
    case 'win':
      return (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M5 12.5l4.5 4.5L19 7.5" />
        </svg>
      );
    case 'move':
      return (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M4 17l5-5 4 3 7-8" />
          <path d="M15 7h5v5" />
        </svg>
      );
    case 'task':
      return (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <circle cx="12" cy="12" r="8.5" />
          <path d="M12 7.5V12l3 2" />
        </svg>
      );
    case 'people':
      return (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
          <circle cx="9" cy="8.5" r="3.5" />
          <path d="M3 19c1-3.2 3.3-5 6-5s5 1.8 6 5" />
          <path d="M16 5.5a3.5 3.5 0 0 1 0 6.5M18 14.5c1.5.7 2.5 2.2 3 4.5" />
        </svg>
      );
    case 'coin':
      return (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <circle cx="12" cy="12" r="8.5" />
          <circle cx="12" cy="12" r="4" />
        </svg>
      );
  }
}

function NoticesSkeleton() {
  return (
    <div className="nt-day" aria-hidden="true">
      <Skeleton width={70} height={13} />
      <div className="nt-list">
        {[0, 1, 2].map((i) => (
          <div key={i} className="nt-item">
            <Skeleton width={36} height={36} radius={999} />
            <div className="nt-main">
              <Skeleton height={14} />
              <Skeleton width="60%" height={14} />
              <Skeleton width={40} height={12} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
