import { useEffect, useRef, useState } from 'react';
import { formatMoney } from '../format';

interface AnimatedNumberProps {
  value: number;
  /** Duration of the tween in ms. */
  duration?: number;
  format?: (n: number) => string;
  className?: string;
}

const easeOut = (t: number) => 1 - (1 - t) ** 3;

/** Number that smoothly tweens to its new value. */
export function AnimatedNumber({ value, duration = 500, format = (n) => formatMoney(n), className }: AnimatedNumberProps) {
  const [shown, setShown] = useState(value);
  const fromRef = useRef(value);

  useEffect(() => {
    const from = fromRef.current;
    if (from === value) return;
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (reduced) {
      fromRef.current = value;
      setShown(value);
      return;
    }
    const start = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const current = from + (value - from) * easeOut(t);
      fromRef.current = current;
      setShown(current);
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value, duration]);

  return (
    <span className={className} style={{ fontVariantNumeric: 'tabular-nums' }}>
      {format(shown)}
    </span>
  );
}
