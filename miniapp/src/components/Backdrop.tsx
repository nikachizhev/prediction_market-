// The living hall behind every screen (docs/mockups/oracle). Purely decorative: the app works without it.
import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { MOOD_LIGHT, moodOf, sceneSize } from '../scene/quality';
import { mountScene } from '../scene/scene';

function prefersStill(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** A device that was too slow once stays still for the rest of the launch. */
let tooSlow = false;

export function Backdrop() {
  const canvas = useRef<HTMLCanvasElement>(null);
  const mood = moodOf(useLocation().pathname);

  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const { width, height } = sceneSize(window.innerWidth, window.innerHeight);
    const scene = mountScene(el, {
      width,
      height,
      cones: [{ x: width / 2, color: MOOD_LIGHT[mood], a: 0.6, w: width * 0.5, len: 0.7 }],
      embers: mood === 'ember' ? 0.8 : 0.25,
      still: tooSlow || prefersStill(),
      onTooSlow: () => {
        tooSlow = true;
      },
    });
    return () => scene.stop();
  }, [mood]);

  return <canvas ref={canvas} className="backdrop" aria-hidden="true" />;
}
