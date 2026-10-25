// Text of the toast about coins granted on app open.
import type { Grant } from './api';
import { formatMoney, plural } from './format';

const LABELS: Partial<Record<Grant['kind'], string>> = {
  daily: 'Бонус дня',
  insurance: 'Страховка',
};

/**
 * "Бонус дня: +20 монет" (docs/mockups/Main.dc.html). The starter pack is
 * left out: onboarding shows it. Returns null when there is nothing to say.
 */
export function grantMessage(granted: Grant[]): string | null {
  const parts = granted
    .filter((g) => LABELS[g.kind])
    .map((g) => {
      const amount = Number(g.amount);
      return `${LABELS[g.kind]}: +${formatMoney(amount)} ${plural(Math.floor(amount), 'монета', 'монеты', 'монет')}`;
    });
  return parts.length ? parts.join(' · ') : null;
}
