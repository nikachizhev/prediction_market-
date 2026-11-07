// What decides how the scene behaves, without touching a canvas: this part is tested.
export type Rgb = [number, number, number];

/** 20 frames per second are enough for fog and ash, and spare the battery. */
export const FRAME_MS = 50;
/** This many slow frames on balance, and the device gets a still picture instead. */
export const SLOW_FRAMES_LIMIT = 30;

export function drawBudgetExceeded(slowFrames: number): boolean {
  return slowFrames >= SLOW_FRAMES_LIMIT;
}

export function mix(a: Rgb, b: Rgb, k: number): Rgb {
  return [
    Math.round(a[0] + (b[0] - a[0]) * k),
    Math.round(a[1] + (b[1] - a[1]) * k),
    Math.round(a[2] + (b[2] - a[2]) * k),
  ];
}

/** The orb's light: moon blue at 0%, amber at 100%. A broken probability stays in the middle. */
export function dotColor(p: number, no: Rgb, yes: Rgb): Rgb {
  const k = Number.isFinite(p) ? Math.min(1, Math.max(0, p)) : 0.5;
  return mix(no, yes, k);
}

/** The scene's size: half the screen, so that pixels stay visible, within sane limits. */
export function sceneSize(screenWidth: number, screenHeight: number): { width: number; height: number } {
  return {
    width: Math.max(120, Math.min(320, Math.ceil(screenWidth / 2))),
    height: Math.max(200, Math.min(560, Math.ceil(screenHeight / 2))),
  };
}

export type BackdropMood = 'amber' | 'ember' | 'violet' | 'moon';

/** Each part of the app has its own light; everything else — the same hall. */
export function moodOf(pathname: string): BackdropMood {
  if (pathname.startsWith('/challenge') || pathname.startsWith('/wheel')) return 'ember';
  if (pathname.startsWith('/rating')) return 'moon';
  if (pathname.startsWith('/profile') || pathname.startsWith('/create')) return 'violet';
  return 'amber';
}

export const MOOD_LIGHT: Record<BackdropMood, Rgb> = {
  amber: [255, 176, 59],
  ember: [255, 80, 50],
  violet: [190, 90, 230],
  moon: [120, 170, 255],
};
