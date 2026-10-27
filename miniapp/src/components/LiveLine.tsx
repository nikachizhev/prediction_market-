import { useId, useLayoutEffect, useRef, useState } from 'react';
import { easeOutCubic, headAt, type Curve, type XY } from '../chartMotion';

interface LiveLineProps {
  points: XY[];
  /** SVG path of the line. */
  line: string;
  /** Closed path under the line, filled with a fading gradient. */
  area?: string;
  curve: Curve;
  color?: string;
  strokeWidth?: number;
  dotRadius?: number;
  /** The end dot; charts hide it while the finger scrubs. */
  showEnd?: boolean;
  /** The line is drawn again when this changes (period, metric). */
  replayKey?: string | number;
  /** Length of the drawing in ms. */
  duration?: number;
  /** Pause before the drawing in ms: the feed draws its cards one after another. */
  delay?: number;
  /** Gets the head's x on every frame of the drawing and null once the line is drawn. */
  onDraw?: (x: number | null) => void;
}

// The clip starts left of the plot so the round line cap is not cut.
const CLIP_X = -20;
const CLIP_FULL = 10_000;
/** How long the end dot glides to a new price when the data changes without a replay. */
const MOVE_MS = 600;

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
const translate = (p: XY) => `translate(${p.x} ${p.y})`;

/**
 * Chart line that draws itself left to right with a dot riding its tip; once
 * the dot reaches the end it bounces and starts pulsing. New data without a
 * replay moves the dot to the new end instead of redrawing. Renders inside an <svg>.
 */
export function LiveLine({
  points,
  line,
  area,
  curve,
  color = 'var(--yes)',
  strokeWidth = 2,
  dotRadius = 4,
  showEnd = true,
  replayKey,
  duration = 1100,
  delay = 0,
  onDraw,
}: LiveLineProps) {
  const id = useId().replace(/:/g, '');
  const clipRef = useRef<SVGRectElement>(null);
  const headRef = useRef<SVGGElement>(null);
  const pointsRef = useRef(points);
  pointsRef.current = points;
  const onDrawRef = useRef(onDraw);
  onDrawRef.current = onDraw;
  // Read once: a card that moves in the list must not redraw.
  const delayRef = useRef(delay);
  const drawingRef = useRef(false);
  const end = points.length > 0 ? points[points.length - 1] : null;
  const endRef = useRef(end);
  const [live, setLive] = useState(false);

  // Runs before paint, so a replay never flashes the finished chart.
  useLayoutEffect(() => {
    if (reducedMotion()) {
      setLive(true);
      return;
    }
    setLive(false);
    drawingRef.current = true;
    const start = performance.now() + delayRef.current;
    let frame = 0;
    const draw = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const head = headAt(pointsRef.current, easeOutCubic(t), curve);
      clipRef.current?.setAttribute('width', String(t < 1 ? head.x - CLIP_X : CLIP_FULL));
      headRef.current?.setAttribute('transform', translate(head));
      if (t < 1) {
        onDrawRef.current?.(head.x);
        frame = requestAnimationFrame(draw);
        return;
      }
      drawingRef.current = false;
      onDrawRef.current?.(null);
      setLive(true);
    };
    draw(performance.now());
    return () => {
      cancelAnimationFrame(frame);
      if (drawingRef.current) {
        drawingRef.current = false;
        onDrawRef.current?.(null);
      }
    };
  }, [replayKey, curve, duration]);

  // Fresh data in the same chart (a new trade): glide to the new end, then land again.
  useLayoutEffect(() => {
    const from = endRef.current;
    endRef.current = end;
    if (!end || !from || drawingRef.current || reducedMotion()) return;
    if (from.x === end.x && from.y === end.y) return;
    setLive(false);
    const start = performance.now();
    let frame = 0;
    const move = (now: number) => {
      if (drawingRef.current) return;
      const k = easeOutCubic((now - start) / MOVE_MS);
      headRef.current?.setAttribute('transform', translate({ x: from.x + (end.x - from.x) * k, y: from.y + (end.y - from.y) * k }));
      if (k < 1) frame = requestAnimationFrame(move);
      else setLive(true);
    };
    move(start);
    return () => cancelAnimationFrame(frame);
  }, [end?.x, end?.y]);

  if (!line || !end) return null;

  return (
    <>
      <defs>
        <linearGradient id={`${id}-fill`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" style={{ stopColor: color, stopOpacity: 0.24 }} />
          <stop offset="1" style={{ stopColor: color, stopOpacity: 0 }} />
        </linearGradient>
        <clipPath id={`${id}-clip`}>
          <rect ref={clipRef} x={CLIP_X} y={-CLIP_FULL / 2} width={CLIP_FULL} height={CLIP_FULL} />
        </clipPath>
      </defs>
      <g clipPath={`url(#${id}-clip)`}>
        {area && <path d={area} fill={`url(#${id}-fill)`} />}
        <path d={line} className="live-line" style={{ stroke: color, strokeWidth }} />
      </g>
      {showEnd && (
        <g
          ref={headRef}
          transform={translate(end)}
          className={`live-head ${live ? 'is-live' : ''}`}
          style={{ color }}
          aria-hidden="true"
        >
          {live ? <circle r={dotRadius} className="live-halo" /> : <circle r={dotRadius * 2.2} className="live-glow" />}
          <circle r={dotRadius} strokeWidth={dotRadius / 2} className="live-dot" />
        </g>
      )}
    </>
  );
}
