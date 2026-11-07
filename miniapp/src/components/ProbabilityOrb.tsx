// The market's probability as a crystal orb: amber «да» and moon-blue «нет» in the real proportion.
import { useEffect, useRef } from 'react';
import { mountScene, type SceneHandle } from '../scene/scene';

const WIDTH = 195;
const HEIGHT = 128;

function prefersStill(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function ProbabilityOrb({ prob }: { prob: number }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const scene = useRef<SceneHandle | null>(null);

  useEffect(() => {
    if (!canvas.current) return;
    scene.current = mountScene(canvas.current, {
      width: WIDTH,
      height: HEIGHT,
      transparent: true,
      still: prefersStill(),
      props: [{ type: 'orb', x: WIDTH / 2, y: 54, r: 46, p: prob }],
    });
    return () => scene.current?.stop();
    // The orb stays; a new probability only changes its light (the effect below).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    scene.current?.update('orb', { p: prob });
  }, [prob]);

  return <canvas ref={canvas} className="orb-hero" aria-hidden="true" />;
}
