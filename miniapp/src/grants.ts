// Text of the toast about coins granted on app open.
import type { Grant } from './api';
import { formatMoney, plural } from './format';

const LABELS: Partial<Record<Grant['kind'], string>> = {
  daily: 'Бонус дня',
  insurance: 'Страховка',
};

const coins = (amount: number) => `+${formatMoney(amount)} ${plural(Math.floor(amount), 'монета', 'монеты', 'монет')}`;

/**
 * "Бонус дня: +20 монет" (docs/mockups/Main.dc.html). With a run of days (phase 3f, spec E12.1):
 * "Бонус дня: +25 монет · 2 дня подряд, завтра +30"; on the first day only "завтра +25".
 * The starter pack is left out: onboarding shows it. Returns null when there is nothing to say.
 */
export function grantMessage(granted: Grant[]): string | null {
  const parts = granted
    .filter((g) => LABELS[g.kind])
    .map((g) => {
      const text = `${LABELS[g.kind]}: ${coins(Number(g.amount))}`;
      return g.kind === 'daily' ? `${text}${streakSuffix(g)}` : text;
    });
  return parts.length ? parts.join(' · ') : null;
}

function streakSuffix(grant: Grant): string {
  if (grant.tomorrow === undefined || grant.streak === undefined) return '';
  const tomorrow = `завтра +${formatMoney(grant.tomorrow)}`;
  if (grant.streak < 2) return ` · ${tomorrow}`;
  return ` · ${grant.streak} ${plural(grant.streak, 'день', 'дня', 'дней')} подряд, ${tomorrow}`;
}
