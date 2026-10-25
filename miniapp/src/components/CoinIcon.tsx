/** Coin glyph used next to balances and stakes. */
export function CoinIcon({ size = 22, strokeWidth = 2 }: { size?: number; strokeWidth?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth={strokeWidth} aria-hidden="true">
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4" />
    </svg>
  );
}
