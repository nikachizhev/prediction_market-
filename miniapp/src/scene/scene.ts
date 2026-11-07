// «Зал оракула» (docs/mockups/oracle/scene.js): a living low-res scene. Canvas 2D only, no libraries.
// Drawn at half the screen's size, scaled up pixelated and dithered, so it reads as one painted world.
import { dotColor, drawBudgetExceeded, FAST_FRAME_MS, FRAME_MS, mix, sliceIndexAt, spinCurve, type Rgb } from './quality';

const STEP = 20;
const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5];
export const AMBER: Rgb = [255, 176, 59];
export const MOON: Rgb = [110, 170, 255];
export const EMBER: Rgb = [255, 62, 47];

type Ctx = CanvasRenderingContext2D;

export interface Cone {
  x: number;
  color: Rgb;
  a: number;
  w: number;
  len: number;
}

export interface OrbProp {
  type: 'orb';
  x: number;
  y: number;
  r: number;
  p: number;
}

export interface CandlesProp {
  type: 'candles';
  y: number;
  lit: number;
  count?: number;
}

export interface WheelSlice {
  label: string;
  /** Share of the circle, from 0 to 1. */
  share: number;
}

export interface WheelProp {
  type: 'wheel';
  x: number;
  y: number;
  r: number;
  angle: number;
  /** Clockwise from the pointer; the server's table. */
  slices?: WheelSlice[];
  /** The pointer's flick when a slice edge passes it, 0..1. */
  kick?: number;
  /** How hot the wheel glows while it spins, 0..1. */
  heat?: number;
}

export interface PodiumPlace {
  x: number;
  y: number;
  color: Rgb;
  crown?: boolean;
}

export interface PodiumProp {
  type: 'podium';
  places: PodiumPlace[];
}

export interface EyeProp {
  type: 'eye';
  x: number;
  y: number;
  r: number;
}

export type SceneProp = OrbProp | CandlesProp | WheelProp | PodiumProp | EyeProp;

export interface SceneOptions {
  width: number;
  height: number;
  /** A prop without the hall: it sits on the page's own backdrop. */
  transparent?: boolean;
  cones?: Cone[];
  props?: SceneProp[];
  fogY?: number;
  fog?: Rgb;
  fog2?: Rgb;
  /** Share of particles that rise as embers instead of falling as ash. */
  embers?: number;
  /** Draw one frame and stop: slow device or «reduce motion». */
  still?: boolean;
  /** Called once when the device turned out too slow to animate. */
  onTooSlow?: () => void;
  /** A slice edge passed the pointer while the wheel spins: a light haptic tick. */
  onTick?: () => void;
}

export interface SceneHandle {
  stop(): void;
  /** Change a prop in place, for example the orb's probability. */
  update(type: SceneProp['type'], patch: Partial<SceneProp>): void;
  spinWheel(degrees: number, ms: number, done?: () => void): void;
  /** A burst of coins out of the wheel; `level` from 0 (the commonest prize) up. */
  celebrate(level: number): void;
}

const rgb = (c: Rgb, a?: number): string =>
  a === undefined ? `rgb(${c[0]},${c[1]},${c[2]})` : `rgba(${c[0]},${c[1]},${c[2]},${a})`;

function seeded(seed: number): () => number {
  let s = seed;
  return () => {
    s = (s * 16807) % 2147483647;
    return (s - 1) / 2147483646;
  };
}

function layer(w: number, h: number): HTMLCanvasElement {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  return c;
}

const fogCache = new Map<string, HTMLCanvasElement>();

/** Tileable value noise for the fog: drifting it costs one drawImage. Computed once per colour. */
function fogTile(color: Rgb, seed: number): HTMLCanvasElement {
  const key = `${color.join()}:${seed}`;
  const cached = fogCache.get(key);
  if (cached) return cached;
  const size = 256;
  const c = layer(size, size);
  const g = c.getContext('2d')!;
  const rnd = seeded(seed);
  const octave = (n: number) => {
    const grid = Array.from({ length: n * n }, () => rnd());
    const at = (i: number, j: number) => grid[((j + n) % n) * n + ((i + n) % n)];
    return (x: number, y: number) => {
      const fx = (x / size) * n;
      const fy = (y / size) * n;
      const x0 = Math.floor(fx);
      const y0 = Math.floor(fy);
      const sx = fx - x0;
      const sy = fy - y0;
      const ux = sx * sx * (3 - 2 * sx);
      const uy = sy * sy * (3 - 2 * sy);
      const top = at(x0, y0) + (at(x0 + 1, y0) - at(x0, y0)) * ux;
      const bottom = at(x0, y0 + 1) + (at(x0 + 1, y0 + 1) - at(x0, y0 + 1)) * ux;
      return top + (bottom - top) * uy;
    };
  };
  const a = octave(8);
  const b = octave(16);
  const d = octave(32);
  const img = g.createImageData(size, size);
  for (let y = 0; y < size; y += 1) {
    const edge = Math.sin((y / size) * Math.PI);
    for (let x = 0; x < size; x += 1) {
      const v = a(x, y) * 0.6 + b(x, y) * 0.28 + d(x, y) * 0.12;
      const i = (y * size + x) * 4;
      img.data[i] = color[0];
      img.data[i + 1] = color[1];
      img.data[i + 2] = color[2];
      img.data[i + 3] = Math.max(0, Math.min(255, (v - 0.34) * 560)) * edge * edge;
    }
  }
  g.putImageData(img, 0, 0);
  fogCache.set(key, c);
  return c;
}

function hall(w: number, h: number, cones: Cone[]): HTMLCanvasElement {
  const c = layer(w, h);
  const g = c.getContext('2d')!;
  const sky = g.createLinearGradient(0, 0, 0, h);
  sky.addColorStop(0, '#07060d');
  sky.addColorStop(0.55, '#100b1f');
  sky.addColorStop(1, '#0a0d1c');
  g.fillStyle = sky;
  g.fillRect(0, 0, w, h);
  const rnd = seeded(77);
  for (let y = 0; y < h * 0.72; y += 8) {
    for (let x = (y / 8) % 2 ? -8 : 0; x < w; x += 16) {
      g.fillStyle = `rgba(120,110,170,${0.02 + rnd() * 0.05})`;
      g.fillRect(x, y, 15, 7);
    }
  }
  [w * 0.14, w * 0.86].forEach((x) => {
    const top = h * 0.31;
    const bottom = h * 0.78;
    const hole = g.createLinearGradient(0, top - 30, 0, bottom);
    hole.addColorStop(0, '#04030a');
    hole.addColorStop(0.75, '#04030a');
    hole.addColorStop(1, 'rgba(4,3,10,0)');
    g.fillStyle = hole;
    g.beginPath();
    g.moveTo(x - 30, bottom);
    g.lineTo(x - 30, top);
    g.arc(x, top, 30, Math.PI, 0);
    g.lineTo(x + 30, bottom);
    g.closePath();
    g.fill();
    const rim = g.createLinearGradient(0, top - 34, 0, top + 110);
    rim.addColorStop(0, 'rgba(116,106,210,.75)');
    rim.addColorStop(1, 'rgba(116,106,210,0)');
    g.strokeStyle = rim;
    g.lineWidth = 3;
    g.beginPath();
    g.moveTo(x - 31, top + 110);
    g.lineTo(x - 31, top);
    g.arc(x, top, 31, Math.PI, 0);
    g.lineTo(x + 31, top + 110);
    g.stroke();
  });
  cones.forEach((cone) => {
    const end = h * cone.len;
    [1, 0.82, 0.62, 0.42].forEach((k) => {
      const beam = g.createLinearGradient(0, 0, 0, end);
      beam.addColorStop(0, rgb(cone.color, cone.a * 0.34));
      beam.addColorStop(0.7, rgb(cone.color, cone.a * 0.1));
      beam.addColorStop(1, rgb(cone.color, 0));
      g.fillStyle = beam;
      g.beginPath();
      g.moveTo(cone.x - 13 * k, 0);
      g.lineTo(cone.x + 13 * k, 0);
      g.lineTo(cone.x + cone.w * k, end);
      g.lineTo(cone.x - cone.w * k, end);
      g.closePath();
      g.fill();
    });
  });
  return c;
}

function vignette(w: number, h: number): HTMLCanvasElement {
  const c = layer(w, h);
  const g = c.getContext('2d')!;
  const v = g.createRadialGradient(w / 2, h * 0.42, h * 0.18, w / 2, h * 0.46, h * 0.72);
  v.addColorStop(0, 'rgba(0,0,0,0)');
  v.addColorStop(1, 'rgba(0,0,0,.82)');
  g.fillStyle = v;
  g.fillRect(0, 0, w, h);
  return c;
}

function glow(g: Ctx, x: number, y: number, r: number, color: Rgb, a: number): void {
  const light = g.createRadialGradient(x, y, 0, x, y, r);
  light.addColorStop(0, rgb(color, a));
  light.addColorStop(1, rgb(color, 0));
  g.save();
  g.globalCompositeOperation = 'lighter';
  g.fillStyle = light;
  g.fillRect(x - r, y - r, r * 2, r * 2);
  g.restore();
}

function plinth(g: Ctx, x: number, y: number, r: number): void {
  const steps: [number, number, string][] = [
    [1.28, 9, '#3a0d12'],
    [1.08, 8, '#5a1418'],
    [0.9, 7, '#7c1d1f'],
  ];
  steps.forEach(([k, h, color], i) => {
    const py = y + (2 - i) * 7;
    g.fillStyle = '#1a0507';
    g.beginPath();
    g.ellipse(x, py + 3, r * k, h, 0, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = color;
    g.beginPath();
    g.ellipse(x, py, r * k, h, 0, 0, Math.PI * 2);
    g.fill();
  });
}

function drawOrb(g: Ctx, s: OrbProp, t: number): void {
  const { x, y, r, p } = s;
  plinth(g, x, y + r + 8, r * 0.82);
  glow(g, x, y, r * 2.5, dotColor(p, MOON, AMBER), 0.5 + Math.sin(t * 1.3) * 0.06);
  g.save();
  g.beginPath();
  g.arc(x, y, r, 0, Math.PI * 2);
  g.clip();
  g.fillStyle = '#0c0a18';
  g.fillRect(x - r, y - r, r * 2, r * 2);
  const rnd = seeded(11);
  for (let i = 0; i < 46; i += 1) {
    const a = rnd() * 6.28 + t * (0.18 + rnd() * 0.4) * (i % 2 ? 1 : -1);
    const d = rnd() * r * 0.92;
    const size = 6 + rnd() * 13;
    glow(g, x + Math.cos(a) * d, y + Math.sin(a) * d * 0.8, size, i / 46 < p ? AMBER : MOON, 0.2);
  }
  g.restore();
  g.strokeStyle = 'rgba(232,226,255,.55)';
  g.lineWidth = 1.5;
  g.beginPath();
  g.arc(x, y, r, 0, Math.PI * 2);
  g.stroke();
  g.fillStyle = 'rgba(255,255,255,.72)';
  g.beginPath();
  g.ellipse(x - r * 0.38, y - r * 0.55, r * 0.2, r * 0.09, -0.55, 0, Math.PI * 2);
  g.fill();
}

function drawCandles(g: Ctx, s: CandlesProp, t: number, width: number): void {
  const { y, lit, count = 7 } = s;
  const gap = Math.min(25, (width - 24) / count);
  const x0 = width / 2 - ((count - 1) * gap) / 2;
  g.fillStyle = '#2a0a10';
  g.fillRect(0, y + 3, width, 6);
  g.fillStyle = '#7c1d1f';
  g.fillRect(0, y, width, 3);
  for (let i = 0; i < count; i += 1) {
    const x = x0 + i * gap;
    const h = 24 + ((i * 7) % 5) * 3;
    const on = i < lit;
    if (on) glow(g, x, y - h - 8, 40, AMBER, 0.5 + Math.sin(t * 5 + i * 2) * 0.06);
    g.fillStyle = on ? '#f5e8c9' : '#3e3450';
    g.fillRect(x - 5, y - h, 10, h);
    g.fillStyle = on ? '#cdb186' : '#2a2338';
    g.fillRect(x + 2, y - h, 3, h);
    g.fillStyle = on ? '#fff6dd' : '#54486a';
    g.fillRect(x - 5, y - h, 10, 2);
    g.fillStyle = '#17121b';
    g.fillRect(x, y - h - 3, 1, 3);
    if (on) {
      const f = Math.sin(t * 9 + i * 3) > 0 ? 1 : 0;
      g.fillStyle = '#ff4a1c';
      g.fillRect(x - 3, y - h - 11 - f, 7, 9 + f);
      g.fillRect(x - 1, y - h - 14 - f, 3, 3);
      g.fillStyle = '#ffc23b';
      g.fillRect(x - 2, y - h - 9 - f, 5, 6);
      g.fillStyle = '#fff6cf';
      g.fillRect(x, y - h - 7, 1, 4);
    }
  }
}

const SLICE_STYLE = [
  { c: '#2a2360', ink: '#d8d2ff' },
  { c: '#5a2a8c', ink: '#ffffff' },
  { c: '#c8341f', ink: '#fff2d6' },
  { c: '#f29a24', ink: '#22120a' },
  { c: '#fff1c2', ink: '#22120a' },
];

const DEFAULT_SLICES: WheelSlice[] = [
  { label: '5', share: 0.6 },
  { label: '10', share: 0.25 },
  { label: '25', share: 0.1 },
  { label: '50', share: 0.04 },
  { label: '100', share: 0.01 },
];

/** Smaller slices get smaller numbers further out, where the slice is widest. */
function labelSize(share: number): { size: number; at: number } {
  if (share >= 0.5) return { size: 16, at: 0.58 };
  if (share >= 0.2) return { size: 14, at: 0.62 };
  if (share >= 0.08) return { size: 11, at: 0.68 };
  if (share >= 0.03) return { size: 8, at: 0.76 };
  return { size: 5, at: 0.84 };
}

function drawWheel(g: Ctx, s: WheelProp, t: number): void {
  const { x, y, r } = s;
  g.fillStyle = '#1b0b12';
  g.beginPath();
  g.moveTo(x - 8, y);
  g.lineTo(x + 8, y);
  g.lineTo(x + 30, y + r + 40);
  g.lineTo(x - 30, y + r + 40);
  g.closePath();
  g.fill();
  plinth(g, x, y + r + 40, 40);
  const heat = s.heat ?? 0;
  glow(g, x, y, r * (1.9 + heat * 0.5), EMBER, 0.34 + Math.sin(t) * 0.04 + heat * 0.3);
  g.save();
  g.translate(x, y);
  g.rotate((s.angle * Math.PI) / 180);
  let from = -Math.PI / 2;
  (s.slices ?? DEFAULT_SLICES).forEach((slice, i) => {
    const style = SLICE_STYLE[i % SLICE_STYLE.length];
    const { size, at } = labelSize(slice.share);
    const to = from + slice.share * Math.PI * 2;
    g.fillStyle = style.c;
    g.beginPath();
    g.moveTo(0, 0);
    g.arc(0, 0, r, from, to);
    g.closePath();
    g.fill();
    g.strokeStyle = '#100815';
    g.lineWidth = 1;
    g.stroke();
    const mid = (from + to) / 2;
    g.save();
    g.rotate(mid);
    g.translate(r * at, 0);
    g.rotate(Math.PI / 2);
    g.fillStyle = style.ink;
    g.font = `700 ${size}px Georgia, serif`;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText(slice.label, 0, 0);
    g.restore();
    from = to;
  });
  g.lineWidth = 5;
  g.strokeStyle = '#c98a2c';
  g.beginPath();
  g.arc(0, 0, r, 0, Math.PI * 2);
  g.stroke();
  g.lineWidth = 1;
  g.strokeStyle = '#ffe2a0';
  g.beginPath();
  g.arc(0, 0, r + 2, 0, Math.PI * 2);
  g.stroke();
  for (let i = 0; i < 24; i += 1) {
    const a = (i / 24) * Math.PI * 2;
    g.fillStyle = i % 2 ? '#fff0b8' : '#5a2c0c';
    g.fillRect(Math.cos(a) * r - 1, Math.sin(a) * r - 1, 2, 2);
  }
  g.restore();
  g.fillStyle = '#c98a2c';
  g.beginPath();
  g.arc(x, y, 9, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = '#ff3e2f';
  g.beginPath();
  g.arc(x, y, 5, 0, Math.PI * 2);
  g.fill();
  glow(g, x, y, 13, EMBER, 0.7);
  // The pointer flicks back as each slice edge knocks it.
  g.save();
  g.translate(x, y - r - 9);
  g.rotate((-(s.kick ?? 0) * 18 * Math.PI) / 180);
  g.fillStyle = '#ffe2a0';
  g.beginPath();
  g.moveTo(0, 18);
  g.lineTo(-7, 0);
  g.lineTo(7, 0);
  g.closePath();
  g.fill();
  g.restore();
  glow(g, x, y - r - 4, 16 + heat * 8, AMBER, 0.55 + heat * 0.3);
}

function drawPodium(g: Ctx, s: PodiumProp, t: number): void {
  s.places.forEach(({ x, y, color, crown }) => {
    const dark = mix(color, [10, 6, 18], 0.8);
    const front = g.createLinearGradient(0, y + 30, 0, y + 190);
    front.addColorStop(0, rgb(mix(color, [10, 6, 18], 0.62)));
    front.addColorStop(1, rgb([10, 6, 18], 0));
    g.fillStyle = front;
    g.fillRect(x - 25, y + 30, 50, 170);
    g.fillStyle = rgb(color);
    g.fillRect(x - 27, y + 28, 54, 3);
    glow(g, x, y + 8, 36, color, 0.38 + Math.sin(t * 1.7 + x) * 0.05);
    g.fillStyle = rgb(dark);
    g.beginPath();
    g.arc(x, y + 30, 20, Math.PI, 0);
    g.closePath();
    g.fill();
    g.beginPath();
    g.arc(x, y + 1, 12, 0, Math.PI * 2);
    g.fill();
    g.strokeStyle = rgb(color);
    g.lineWidth = 2;
    g.beginPath();
    g.arc(x, y + 1, 12, Math.PI * 1.05, Math.PI * 1.95);
    g.stroke();
    g.beginPath();
    g.arc(x, y + 30, 20, Math.PI * 1.1, Math.PI * 1.9);
    g.stroke();
    if (crown) {
      g.fillStyle = rgb(color);
      g.beginPath();
      g.moveTo(x - 9, y - 11);
      g.lineTo(x - 12, y - 23);
      g.lineTo(x - 4, y - 16);
      g.lineTo(x, y - 26);
      g.lineTo(x + 4, y - 16);
      g.lineTo(x + 12, y - 23);
      g.lineTo(x + 9, y - 11);
      g.closePath();
      g.fill();
      glow(g, x, y - 26, 9, [255, 255, 220], 0.9);
    }
  });
}

function drawEye(g: Ctx, s: EyeProp, t: number): void {
  const { x, y, r } = s;
  g.save();
  g.globalCompositeOperation = 'lighter';
  for (let i = 0; i < 28; i += 1) {
    const a = (i / 28) * Math.PI * 2 + t * 0.05;
    const inner = r * (i % 2 ? 1.2 : 1.32);
    const outer = r * (i % 2 ? 1.5 : 2.05);
    g.strokeStyle = rgb(i % 2 ? AMBER : EMBER, 0.55);
    g.lineWidth = 1;
    g.beginPath();
    g.moveTo(x + Math.cos(a) * inner, y + Math.sin(a) * inner * 0.72);
    g.lineTo(x + Math.cos(a) * outer, y + Math.sin(a) * outer * 0.72);
    g.stroke();
  }
  g.restore();
  glow(g, x, y, r * 1.9, EMBER, 0.45);
  const open = 0.56 + Math.sin(t * 0.7) * 0.03;
  const lens = () => {
    g.beginPath();
    g.moveTo(x - r, y);
    g.quadraticCurveTo(x, y - r * open * 2, x + r, y);
    g.quadraticCurveTo(x, y + r * open * 2, x - r, y);
  };
  g.save();
  lens();
  g.closePath();
  g.fillStyle = '#f5e6c8';
  g.fill();
  g.clip();
  const iris = g.createRadialGradient(x, y, 2, x, y, r * 0.56);
  iris.addColorStop(0, '#ffe08a');
  iris.addColorStop(0.45, '#ff5a22');
  iris.addColorStop(1, '#5a0d12');
  g.fillStyle = iris;
  g.beginPath();
  g.arc(x, y, r * 0.56, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = '#0a0612';
  g.beginPath();
  g.arc(x, y, r * 0.2, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = '#fff';
  g.fillRect(x + r * 0.16, y - r * 0.26, 3, 3);
  g.restore();
  g.strokeStyle = '#1b0b12';
  g.lineWidth = 2;
  lens();
  g.stroke();
}

function drawProp(g: Ctx, prop: SceneProp, t: number, width: number): void {
  if (prop.type === 'orb') drawOrb(g, prop, t);
  else if (prop.type === 'candles') drawCandles(g, prop, t, width);
  else if (prop.type === 'wheel') drawWheel(g, prop, t);
  else if (prop.type === 'podium') drawPodium(g, prop, t);
  else drawEye(g, prop, t);
}

function dither(g: Ctx, w: number, h: number): void {
  const img = g.getImageData(0, 0, w, h);
  const d = img.data;
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const i = (y * w + x) * 4;
      const n = (BAYER[(y & 3) * 4 + (x & 3)] / 16 - 0.5) * STEP;
      d[i] = Math.round((d[i] + n) / STEP) * STEP;
      d[i + 1] = Math.round((d[i + 1] + n) / STEP) * STEP;
      d[i + 2] = Math.round((d[i + 2] + n) / STEP) * STEP;
    }
  }
  g.putImageData(img, 0, 0);
}

export function mountScene(canvas: HTMLCanvasElement, o: SceneOptions): SceneHandle {
  const w = o.width;
  const h = o.height;
  canvas.width = w;
  canvas.height = h;
  const g = canvas.getContext('2d', { willReadFrequently: true });
  if (!g) return { stop() {}, update() {}, spinWheel(_d, _m, done) { done?.(); }, celebrate() {} };
  const cones = o.cones ?? [{ x: w / 2, color: AMBER, a: 0.55, w: w * 0.47, len: 0.72 }];
  const back = o.transparent ? null : hall(w, h, cones);
  const shade = o.transparent ? null : vignette(w, h);
  const fogA = o.transparent ? null : fogTile(o.fog ?? [58, 72, 150], 31);
  const fogB = o.transparent ? null : fogTile(o.fog2 ?? [110, 44, 130], 97);
  const rnd = seeded(5);
  const ash = o.transparent
    ? []
    : Array.from({ length: 34 }, () => ({
        x: rnd() * w,
        y: rnd() * h,
        v: 3 + rnd() * 7,
        s: rnd() < 0.3 ? 2 : 1,
        ember: rnd() < (o.embers ?? 0.25),
        p: rnd() * 6,
      }));
  const props: SceneProp[] = (o.props ?? []).map((p) => ({ ...p }));
  const fogY = o.fogY ?? h * 0.59;
  const start = performance.now();
  let frame = 0;
  let last = 0;
  let visible = true;
  let stopped = false;
  let slowFrames = 0;
  let spin: { prop: WheelProp; base: number; to: number; ms: number; from: number; slice: number; done?: () => void } | null = null;
  // Sparks off the rim and coins of a win: plain physics in scene pixels per second.
  type Particle = { x: number; y: number; vx: number; vy: number; life: number; max: number; size: number; coin: boolean };
  let particles: Particle[] = [];
  let flash = 0;
  let lastDraw = start;
  const wheelOf = () => props.find((p): p is WheelProp => p.type === 'wheel');
  const sharesOf = (p: WheelProp) => (p.slices ?? [{ share: 0.6 }, { share: 0.25 }, { share: 0.1 }, { share: 0.04 }, { share: 0.01 }]).map((s) => s.share);
  const busy = () => spin !== null || particles.length > 0 || flash > 0.01 || (wheelOf()?.kick ?? 0) > 0.01;

  const draw = (now: number) => {
    const t = (now - start) / 1000;
    if (o.transparent) g.clearRect(0, 0, w, h);
    if (back) g.drawImage(back, 0, 0);
    const ax = (t * 3) % 256;
    const bx = (t * -5) % 256;
    if (fogB) {
      g.globalAlpha = 0.5;
      [-512, 0, 512].forEach((dx) => g.drawImage(fogB, dx + bx * 2, fogY - 70, 512, 130));
      g.globalAlpha = 1;
    }
    const dt = Math.min(0.1, Math.max(0, (now - lastDraw) / 1000));
    lastDraw = now;
    const wheel = wheelOf();
    if (wheel) {
      wheel.kick = (wheel.kick ?? 0) * Math.pow(0.02, dt);
      wheel.heat = (wheel.heat ?? 0) * Math.pow(0.15, dt);
    }
    if (spin) {
      const k = Math.min(1, (now - spin.from) / spin.ms);
      const before = spin.prop.angle;
      // From wherever the wheel stands now: a second spin does not jump back to zero first.
      spin.prop.angle = spin.base + spinCurve(k, spin.to - spin.base);
      const speed = dt > 0 ? Math.abs(spin.prop.angle - before) / dt : 0;
      spin.prop.heat = Math.max(spin.prop.heat ?? 0, Math.min(1, speed / 1400));
      const slice = sliceIndexAt(sharesOf(spin.prop), spin.prop.angle);
      if (slice !== spin.slice) {
        spin.slice = slice;
        spin.prop.kick = 1;
        o.onTick?.();
      }
      // Sparks fly off the rim, more the faster it turns.
      const rim = spin.prop;
      for (let i = 0; i < Math.round((rim.heat ?? 0) * 3); i += 1) {
        const a = Math.random() * Math.PI * 2;
        const out = 40 + Math.random() * 60;
        particles.push({ x: rim.x + Math.cos(a) * rim.r, y: rim.y + Math.sin(a) * rim.r, vx: -Math.sin(a) * out,
          vy: Math.cos(a) * out - 20, life: 0, max: 0.35 + Math.random() * 0.3, size: 1, coin: false });
      }
      if (k >= 1) {
        const done = spin.done;
        spin = null;
        done?.();
      }
    }
    props.forEach((p) => drawProp(g, p, t, w));
    if (flash > 0.01 && wheel) {
      glow(g, wheel.x, wheel.y, wheel.r * 2.6, AMBER, flash * 0.8);
      flash *= Math.pow(0.12, dt);
    }
    particles = particles.filter((p) => (p.life += dt) < p.max);
    particles.forEach((p) => {
      p.vy += (p.coin ? 260 : 120) * dt;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      const fade = 1 - p.life / p.max;
      if (p.coin) {
        // A spinning coin: its width breathes, the rim catches the light.
        const spinW = Math.max(1, Math.round(Math.abs(Math.cos(p.life * 14 + p.x)) * p.size * 2));
        g.fillStyle = `rgba(255, 196, 64, ${fade})`;
        g.fillRect(p.x - spinW / 2, p.y - p.size, spinW, p.size * 2);
        g.fillStyle = `rgba(255, 248, 210, ${fade})`;
        g.fillRect(p.x - spinW / 2, p.y - p.size, Math.max(1, spinW / 3), 1);
      } else {
        g.fillStyle = `rgba(255, ${140 + Math.round(fade * 100)}, 60, ${fade})`;
        g.fillRect(p.x, p.y, p.size, p.size);
      }
    });
    if (fogA && fogB) {
      g.globalAlpha = 0.62;
      [-512, 0, 512].forEach((dx) => g.drawImage(fogA, dx + ax * 2, fogY + 10, 512, 160));
      [-512, 0, 512].forEach((dx) => g.drawImage(fogB, dx - ax * 3, fogY + 90, 512, 120));
      g.globalAlpha = 1;
    }
    ash.forEach((a) => {
      const dir = a.ember ? -1 : 1;
      const y = (((a.y + dir * a.v * t) % h) + h) % h;
      const x = a.x + Math.sin(t * 0.6 + a.p) * 5;
      g.fillStyle = a.ember ? '#ff7a2a' : '#d9d2c4';
      g.fillRect(x, y, a.s, a.s);
      if (a.s === 2 && !a.ember) {
        g.fillRect(x - 1, y + 0.5, 4, 1);
        g.fillRect(x + 0.5, y - 1, 1, 4);
      }
    });
    if (shade) g.drawImage(shade, 0, 0);
    dither(g, w, h);
  };

  const loop = (now: number) => {
    if (stopped) return;
    frame = requestAnimationFrame(loop);
    if (!visible || document.hidden || now - last < (busy() ? FAST_FRAME_MS : FRAME_MS)) return;
    last = now;
    const before = performance.now();
    draw(now);
    slowFrames = performance.now() - before > FRAME_MS ? slowFrames + 1 : Math.max(0, slowFrames - 1);
    // A spinning wheel must finish; otherwise a device that can't keep up gets a still picture.
    if (drawBudgetExceeded(slowFrames) && !busy()) {
      stopped = true;
      cancelAnimationFrame(frame);
      o.onTooSlow?.();
    }
  };

  let observer: IntersectionObserver | null = null;
  if (typeof IntersectionObserver !== 'undefined') {
    observer = new IntersectionObserver((entries) => {
      visible = entries[0].isIntersecting;
    });
    observer.observe(canvas);
  }
  draw(start);
  if (!o.still) frame = requestAnimationFrame(loop);

  return {
    stop() {
      stopped = true;
      cancelAnimationFrame(frame);
      observer?.disconnect();
    },
    update(type, patch) {
      props.filter((p) => p.type === type).forEach((p) => Object.assign(p, patch));
      if (o.still || stopped) draw(performance.now());
    },
    spinWheel(degrees, ms, done) {
      const prop = props.find((p): p is WheelProp => p.type === 'wheel');
      if (!prop) return;
      if (o.still || stopped) {
        prop.angle = degrees;
        draw(performance.now());
        done?.();
        return;
      }
      // The same position, within one turn, so `degrees` (several turns ahead) stays ahead of it.
      const base = ((prop.angle % 360) + 360) % 360;
      prop.angle = base;
      spin = { prop, base, to: degrees, ms, from: performance.now(), done, slice: sliceIndexAt(sharesOf(prop), base) };
    },
    celebrate(level) {
      const wheel = wheelOf();
      if (!wheel || o.still || stopped) return;
      flash = 0.6 + level * 0.15;
      const count = 14 + level * 16;
      for (let i = 0; i < count; i += 1) {
        const a = -Math.PI / 2 + (Math.random() - 0.5) * (1.2 + level * 0.35);
        const power = 90 + Math.random() * (110 + level * 40);
        particles.push({ x: wheel.x + (Math.random() - 0.5) * 10, y: wheel.y, vx: Math.cos(a) * power,
          vy: Math.sin(a) * power, life: 0, max: 1.1 + Math.random() * 0.9, size: 2 + (Math.random() < 0.3 ? 1 : 0), coin: true });
      }
    },
  };
}
