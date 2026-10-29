// Links to a market: «Поделиться» and the start_param deep link (docs/api.md, «Фаза 2»).
import { api } from './api';
import { shareLink } from './market';
import { tg } from './telegram';

/**
 * Link that opens the market in the Mini App and keeps the referral:
 * `https://t.me/<bot>/<app>?startapp=ref_<code>` → `…?startapp=ref_<code>-m<id>`.
 * Without `startapp` in the referral link the market goes alone: `?startapp=m<id>`.
 */
export function marketShareLink(referralLink: string, marketId: number): string {
  const m = /([?&]startapp=)([^&#]*)/.exec(referralLink);
  if (m) {
    const value = m[2] ? `${m[2]}-m${marketId}` : `m${marketId}`;
    return referralLink.slice(0, m.index) + m[1] + value + referralLink.slice(m.index + m[0].length);
  }
  const sep = referralLink.includes('?') ? '&' : '?';
  return `${referralLink}${sep}startapp=m${marketId}`;
}

/** Market id from a start_param `m<id>` or `ref_<code>-m<id>`; null for anything else. */
export function marketFromStartParam(startParam: string | null | undefined): number | null {
  const m = /^(?:ref_.+-)?m(\d+)$/.exec(startParam ?? '');
  if (!m) return null;
  const id = Number(m[1]);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

/** Chat code from a start_param `chat_<code>` (the chat's pinned link); null for anything else. */
export function chatFromStartParam(startParam: string | null | undefined): string | null {
  const m = /^chat_([A-Za-z0-9_-]{1,64})$/.exec(startParam ?? '');
  return m ? m[1] : null;
}

/**
 * «Поделиться»: a prepared message via Telegram.WebApp.shareMessage when the
 * client supports it; otherwise, or on any error, the regular t.me/share dialog.
 */
export async function shareMarket(market: { id: number; title: string }, referralLink: string): Promise<void> {
  tg.HapticFeedback.impact('light');
  api.event('share', { market: market.id }).catch(() => {});
  if (tg.canShareMessage) {
    try {
      const { prepared_message_id } = await api.shareMarket(market.id);
      await tg.shareMessage(prepared_message_id);
      return;
    } catch {
      // Fall through to the plain link.
    }
  }
  tg.openTelegramLink(shareLink(marketShareLink(referralLink, market.id), market.title));
}
