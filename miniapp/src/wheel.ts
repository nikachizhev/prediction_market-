// «Колесо дня» without React: where the wheel stops and what the card says. The server picks the prize.
import type { WheelPrize, WheelState } from './api';
import type { WheelSlice } from './scene/scene';
import { formatMoney } from './format';

export interface WheelSegment {
  prize: string;
  /** Share of the circle, from 0 to 1. */
  share: number;
  /** 0 for the commonest prize, up to the rarest: picks the colour. */
  rank: number;
}

/**
 * The wheel as drawn: the commonest prize cut into pieces with the others between them, so no single
 * slice gives the answer away. Each prize keeps its whole chance as the sum of its pieces: the wheel stays
 * honest. A rarer prize gets fewer pieces (at most 4); the commonest gets one piece per gap.
 */
export function wheelLayout(prizes: WheelPrize[]): WheelSegment[] {
  const order = [...prizes].sort((a, b) => b.chance - a.chance);
  const rankOf = (p: WheelPrize) => order.indexOf(p);
  if (order.length < 2) return order.map((p) => ({ prize: p.prize, share: p.chance, rank: 0 }));
  const [common, ...others] = order;
  const pieces = others.map((p) => Math.max(1, Math.min(4, Math.round(p.chance * 16))));
  const gaps = pieces.reduce((a, b) => a + b, 0);
  // Spread each prize's pieces evenly around the wheel, the most pieces first.
  const slots: (WheelPrize | null)[] = Array(gaps).fill(null);
  others.forEach((p, i) => {
    const n = pieces[i];
    for (let k = 0; k < n; k += 1) {
      let at = Math.round((k * gaps) / n);
      while (slots[at % gaps] !== null) at += 1;
      slots[at % gaps] = p;
    }
  });
  const out: WheelSegment[] = [];
  for (const p of slots as WheelPrize[]) {
    out.push({ prize: common.prize, share: common.chance / gaps, rank: 0 });
    out.push({ prize: p.prize, share: p.chance / pieces[others.indexOf(p)], rank: rankOf(p) });
  }
  return out;
}

export function slicesOf(prizes: WheelPrize[]): WheelSlice[] {
  return wheelLayout(prizes).map((s) => ({ label: formatMoney(s.prize), share: s.share, tone: s.rank }));
}

/**
 * Degrees to turn the wheel so one of the prize's pieces stops under the pointer at the top. Pieces go
 * clockwise from the pointer; `pick` (0..1) chooses which piece, `turns` full turns come first and
 * `offset` (from -0.5 to 0.5) moves within the piece.
 */
export function stopAngle(
  prizes: WheelPrize[],
  prize: string | number,
  turns = 5,
  offset = 0,
  pick: number = Math.random(),
): number | null {
  const target = Number(prize);
  const spots: { from: number; width: number }[] = [];
  let from = 0;
  for (const s of wheelLayout(prizes)) {
    const width = s.share * 360;
    if (Number(s.prize) === target) spots.push({ from, width });
    from += width;
  }
  if (spots.length === 0) return null;
  const spot = spots[Math.min(spots.length - 1, Math.floor(Math.max(0, pick) * spots.length))];
  const at = spot.from + spot.width / 2 + Math.max(-0.5, Math.min(0.5, offset)) * spot.width * 0.7;
  return 360 * turns + ((360 - at) % 360);
}

export type WheelCardView = 'locked' | 'open' | 'spun';

export function cardView(state: Pick<WheelState, 'unlocked' | 'spun'>): WheelCardView {
  if (state.spun !== null) return 'spun';
  return state.unlocked ? 'open' : 'locked';
}

/** «5 — 60%» for the odds under the wheel. */
export function chanceLabel(chance: number): string {
  const percent = chance * 100;
  return `${percent < 1 ? percent.toFixed(1).replace('.', ',') : Math.round(percent)}%`;
}

/** 0 for the commonest prize, up to the rarest: how loud the win is. */
export function celebrationLevel(prizes: WheelPrize[], prize: string | number): number {
  const order = [...prizes].sort((a, b) => b.chance - a.chance).map((p) => Number(p.prize));
  const at = order.indexOf(Number(prize));
  return at < 0 ? 0 : at;
}

const PHRASES = ['Капля удачи', 'Хороший знак', 'Оракул улыбнулся', 'Звёзды сошлись', 'Оракул сегодня щедр!'];

export function winPhrase(level: number): string {
  return PHRASES[Math.min(Math.max(0, level), PHRASES.length - 1)];
}

/** The demo's prize: the one asked for in the link if it is on the wheel, otherwise by the same odds. */
export function pickDemoPrize(prizes: WheelPrize[], wanted: string | null, rnd: () => number = Math.random): string {
  const asked = prizes.find((p) => wanted !== null && Number(p.prize) === Number(wanted));
  if (asked) return asked.prize;
  let roll = rnd() * prizes.reduce((sum, p) => sum + p.chance, 0);
  for (const p of prizes) {
    roll -= p.chance;
    if (roll < 0) return p.prize;
  }
  return prizes[prizes.length - 1].prize;
}

