interface SparklineProps {
  /** Values in 0..1 (probability of «Да»). */
  points: number[];
  width?: number;
  height?: number;
  color?: string;
  strokeWidth?: number;
}

/** Builds the SVG polyline points; the range is stretched to fit like in the mockup. */
export function sparkPoints(points: number[], width: number, height: number, pad = 2): string {
  if (points.length === 0) return '';
  const values = points.length === 1 ? [points[0], points[0]] : points;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = Math.max(hi - lo, 0.01);
  const stepX = (width - pad * 2) / (values.length - 1);
  return values
    .map((v, i) => {
      const x = pad + i * stepX;
      const y = height - pad - ((v - lo) / span) * (height - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
}

/** Tiny SVG trend chart. */
export function Sparkline({ points, width = 96, height = 32, color = 'var(--yes)', strokeWidth = 2 }: SparklineProps) {
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <polyline
        points={sparkPoints(points, width, height)}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
