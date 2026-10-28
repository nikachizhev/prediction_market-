// Pure helpers for the drawing animation of the charts (components/LiveLine.tsx).

export interface XY {
  x: number;
  y: number;
}

/** How the line goes between points: a price holds until the next trade, a balance moves straight. */
export type Curve = 'step' | 'linear';

export const easeOutCubic = (t: number): number => 1 - (1 - Math.min(1, Math.max(0, t))) ** 3;

/** Y of the line at `x`: a step line keeps the previous value, a linear one interpolates. */
export function lineYAt(points: XY[], x: number, curve: Curve): number {
  if (points.length === 0) return 0;
  if (x <= points[0].x) return points[0].y;
  for (let i = 1; i < points.length; i += 1) {
    const b = points[i];
    if (x < b.x) {
      const a = points[i - 1];
      if (curve === 'step') return a.y;
      return a.y + ((x - a.x) / (b.x - a.x)) * (b.y - a.y);
    }
  }
  return points[points.length - 1].y;
}

/** Tip of the line at `progress` (0..1 of the time span, already eased). */
export function headAt(points: XY[], progress: number, curve: Curve): XY {
  if (points.length === 0) return { x: 0, y: 0 };
  const x0 = points[0].x;
  const x1 = points[points.length - 1].x;
  const x = x0 + (x1 - x0) * Math.min(1, Math.max(0, progress));
  return { x, y: lineYAt(points, x, curve) };
}

/** Step of the feed cascade: each next sparkline starts drawing this much later. */
export const CASCADE_STEP_MS = 120;
/** Cards past this index (below the first screen, or loaded later) draw without waiting. */
export const CASCADE_CARDS = 6;

/** Delay before a feed card draws its sparkline, so the list fills top to bottom. */
export const cascadeDelay = (index: number): number => (index < CASCADE_CARDS ? index * CASCADE_STEP_MS : 0);
