// «Зал оракула» (docs/mockups/oracle): one dark palette, exposed as CSS variables. Telegram's light theme is
// not followed on purpose (spec, «Стиль»).
import { useEffect } from 'react';
import { tg } from './telegram';

export interface ThemeTokens {
  bg: string;
  card: string;
  ink: string;
  sub: string;
  line: string;
  yes: string;
  yesTint: string;
  no: string;
  noTint: string;
  yesFill: string;
  noFill: string;
  gold: string;
  /** Gold text and icons on goldTint (Notifications.dc.html). */
  goldInk: string;
  goldTint: string;
  /** Background of an unread notification. */
  unreadBg: string;
  overlay: string;
  tab: string;
  /** Bot chat drawn inside the app (Bot.dc.html): background, bubbles, inline keys. */
  chatBg: string;
  bubbleIn: string;
  bubbleOut: string;
  botKey: string;
  botKeyOn: string;
}

/** Translucent panels let the living hall show through; text always lies on a dense panel. */
export const oracleTheme: ThemeTokens = {
  bg: '#07060D',
  card: 'rgba(13, 10, 24, 0.88)',
  ink: '#EDE4D3',
  sub: '#B5ACC8',
  line: 'rgba(190, 180, 230, 0.26)',
  yes: '#FFC766',
  yesTint: 'rgba(255, 176, 59, 0.18)',
  no: '#A9CAFF',
  noTint: 'rgba(141, 183, 255, 0.18)',
  yesFill: '#A8650F',
  noFill: '#3A66B8',
  gold: '#FFCD50',
  goldInk: '#FFCD50',
  goldTint: 'rgba(255, 205, 80, 0.16)',
  unreadBg: 'rgba(38, 28, 66, 0.92)',
  overlay: 'rgba(3, 2, 8, 0.72)',
  tab: 'rgba(7, 6, 13, 0.94)',
  chatBg: '#0A0814',
  bubbleIn: '#181426',
  bubbleOut: '#2A2050',
  botKey: '#1E1934',
  botKeyOn: '#322758',
};

const cssName = (key: string): string => '--' + key.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());

export function applyTheme(): void {
  const root = document.documentElement;
  for (const [key, value] of Object.entries(oracleTheme)) {
    root.style.setProperty(cssName(key), value);
  }
  root.dataset.theme = 'dark';
  root.style.colorScheme = 'dark';
}

/** Applies the theme once and paints Telegram's own header and background to match. */
export function useThemeSync(): void {
  useEffect(() => {
    applyTheme();
    tg.paintChrome(oracleTheme.bg);
  }, []);
}
