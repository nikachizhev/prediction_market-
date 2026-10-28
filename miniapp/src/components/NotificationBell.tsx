import { Link } from 'react-router-dom';
import { unreadBadge } from '../notifications';
import { useSession } from '../session';
import { tg } from '../telegram';

/** Bell from the feed header (docs/mockups/Main.dc.html) with the unread count. */
export function NotificationBell() {
  const { me } = useSession();
  const badge = unreadBadge(me?.unread_notifications);
  return (
    <Link
      to="/notifications"
      className="bell"
      aria-label={badge ? `Уведомления, непрочитанных: ${badge}` : 'Уведомления'}
      onClick={() => tg.HapticFeedback.selection()}
    >
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z" />
        <path d="M10 20.5a2 2 0 0 0 4 0" />
      </svg>
      {badge && (
        <span className="bell-badge" aria-hidden="true">
          {badge}
        </span>
      )}
    </Link>
  );
}
