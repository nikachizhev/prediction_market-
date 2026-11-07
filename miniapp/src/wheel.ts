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
