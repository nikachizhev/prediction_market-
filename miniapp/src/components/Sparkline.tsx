import { useMemo } from 'react';
import type { XY } from '../chartMotion';
import { LiveLine } from './LiveLine';

interface SparklineProps {
  /** Values in 0..1 (probability of «Да»). */
  points: number[];
  width?: number;
  height?: number;
  color?: string;
  strokeWidth?: number;
  /** Pause before drawing, ms (the feed cascade). */
  delay?: number;
}

/** Point coordinates; the range is stretched to fit like in the mockup. */
export function sparkCoords(points: number[], width: number, height: number, pad = 2): XY[] {
  if (points.length === 0) return [];
  const values = points.length === 1 ? [points[0], points[0]] : points;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = Math.max(hi - lo, 0.01);
  const stepX = (width - pad * 2) / (values.length - 1);
  return values.map((v, i) => ({
    x: Number((pad + i * stepX).toFixed(1)),
    y: Number((height - pad - ((v - lo) / span) * (height - pad * 2)).toFixed(1)),
  }));
}

/** Builds the SVG polyline points. */
export function sparkPoints(points: number[], width: number, height: number, pad = 2): string {
  return sparkCoords(points, width, height, pad)
    .map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(' ');
}

/** Tiny trend chart that draws itself and keeps a pulsing dot at the end. */
export function Sparkline({ points, width = 96, height = 32, color = 'var(--yes)', strokeWidth = 2, delay = 0 }: SparklineProps) {
  const coords = useMemo(() => sparkCoords(points, width, height), [points, width, height]);
  const line = coords.length ? 'M' + coords.map((p) => `${p.x} ${p.y}`).join(' L') : '';
  const area = coords.length
    ? `${line} L${coords[coords.length - 1].x} ${height} L${coords[0].x} ${height} Z`
    : '';
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="live-chart" aria-hidden="true">
      <LiveLine
        points={coords}
        line={line}
        area={area}
        curve="linear"
        color={color}
        strokeWidth={strokeWidth}
        dotRadius={2.5}
        duration={900}
        delay={delay}
      />
    </svg>
  );
}
