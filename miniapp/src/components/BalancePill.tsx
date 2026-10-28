import { useSession } from '../session';
import { AnimatedNumber } from './AnimatedNumber';
import { CoinIcon } from './CoinIcon';
import { Skeleton } from './Skeleton';

/** Coin balance chip from the feed header. */
export function BalancePill() {
  const { me, balance } = useSession();
  if (!me) return <Skeleton width={96} height={40} radius={999} />;
  return (
    <div className="balance-pill" aria-label="Баланс">
      <CoinIcon />
      <AnimatedNumber value={balance} />
    </div>
  );
}
