import { useSession } from '../session';
import { AnimatedNumber } from './AnimatedNumber';
import { Skeleton } from './Skeleton';

/** Coin balance chip from the feed header. */
export function BalancePill() {
  const { me, balance } = useSession();
  if (!me) return <Skeleton width={96} height={40} radius={999} />;
  return (
    <div className="balance-pill" aria-label="Баланс">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="2" aria-hidden="true">
        <circle cx="12" cy="12" r="8.5" />
        <circle cx="12" cy="12" r="4" />
      </svg>
      <AnimatedNumber value={balance} />
    </div>
  );
}
