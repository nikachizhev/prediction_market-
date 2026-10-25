import type { CSSProperties } from 'react';

interface SkeletonProps {
  width?: number | string;
  height?: number | string;
  radius?: number | string;
  style?: CSSProperties;
}

/** Shimmering placeholder used instead of spinners while data loads. */
export function Skeleton({ width = '100%', height = 16, radius = 8, style }: SkeletonProps) {
  return <span className="skeleton" aria-hidden="true" style={{ width, height, borderRadius: radius, ...style }} />;
}

/** Placeholder shaped like a market card from the feed. */
export function CardSkeleton() {
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }} aria-hidden="true">
      <Skeleton width={90} height={14} />
      <Skeleton height={18} />
      <Skeleton width="70%" height={18} />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 4 }}>
        <Skeleton height={44} radius={10} />
        <Skeleton height={44} radius={10} />
      </div>
    </div>
  );
}
