// «Пригласить друга»: Telegram's share sheet with the personal referral link.
import { api } from './api';
import { tg } from './telegram';

export const INVITE_TEXT = 'Делаю прогнозы на монеты в Telegram — присоединяйся и проверим, кто точнее!';

export function inviteShareUrl(referralLink: string, text: string = INVITE_TEXT): string {
  return 'https://t.me/share/url?url=' + encodeURIComponent(referralLink) + '&text=' + encodeURIComponent(text);
}

/** Opens the share sheet and records the `share` event (fire and forget). */
export function shareInvite(referralLink: string, source: string): void {
  tg.HapticFeedback.impact('light');
  tg.openTelegramLink(inviteShareUrl(referralLink));
  api.event('share', { what: 'invite', source }).catch(() => {});
}
