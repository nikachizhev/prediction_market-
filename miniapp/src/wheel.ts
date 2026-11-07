// «Колесо дня» without React: where the wheel stops and what the card says. The server picks the prize.
import type { WheelPrize, WheelState } from './api';
import type { WheelSlice } from './scene/scene';
import { formatMoney } from './format';

export function slicesOf(prizes: WheelPrize[]): WheelSlice[] {
  return prizes.map((p) => ({ label: formatMoney(p.prize), share: p.chance }));
}

/**
 * Degrees to turn the wheel so the prize's slice stops under the pointer at the top. Slices go clockwise
 * from the pointer; `turns` full turns come first, `offset` (from -0.5 to 0.5) moves within the slice.
 */
export function stopAngle(prizes: WheelPrize[], prize: string | number, turns = 5, offset = 0): number | null {
  const target = Number(prize);
  let from = 0;
  for (const p of prizes) {
    const width = p.chance * 360;
    if (Number(p.prize) === target) {
      const at = from + width / 2 + Math.max(-0.5, Math.min(0.5, offset)) * width * 0.7;
      return 360 * turns + ((360 - at) % 360);
    }
    from += width;
  }
  return null;
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

