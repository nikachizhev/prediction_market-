// Two looks, exposed as CSS variables. «Спокойный» (docs/mockups/Main.dc.html) is the default and follows
// Telegram's light or dark theme. «Зал оракула» (docs/mockups/oracle) is one dark palette with a living scene,
// kept for themed channels (spec, «Стиль»).
import { useEffect, useState } from 'react';
import { look, type Look } from './look';
import { isTelegram, tg, type ColorScheme } from './telegram';

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

export const themes: Record<ColorScheme, ThemeTokens> = {
  light: {
    bg: '#F2F3F5',
    card: '#FFFFFF',
    ink: '#111418',
    sub: '#5F6672',
    line: '#E3E5E8',
    yes: '#1A5FB8',
    yesTint: '#E7EFFA',
    no: '#B23A0B',
    noTint: '#FBEDE6',
    yesFill: '#1F6FD6',
    noFill: '#C2410C',
    gold: '#D99A00',
    goldInk: '#8A6100',
    goldTint: '#FBF1D9',
    unreadBg: '#F6F9FE',
    overlay: 'rgba(17, 20, 24, 0.45)',
    tab: '#FFFFFF',
    chatBg: '#E6EBF0',
    bubbleIn: '#FFFFFF',
    bubbleOut: '#D7E8FF',
    botKey: '#F3F5F8',
    botKeyOn: '#E1EBF8',
  },
  dark: {
    bg: '#0E1014',
    card: '#181B21',
    ink: '#F1F3F5',
    sub: '#9AA3AE',
    line: '#2A2E36',
    yes: '#7DB0FF',
    yesTint: '#1E2A3C',
    no: '#FF9E6B',
    noTint: '#33241D',
    yesFill: '#1F6FD6',
    noFill: '#C2410C',
    gold: '#F2B632',
    goldInk: '#F2B632',
    goldTint: '#3A3020',
    unreadBg: '#1B212B',
    overlay: 'rgba(0, 0, 0, 0.6)',
    tab: '#14171C',
    chatBg: '#0E1014',
    bubbleIn: '#1E2229',
    bubbleOut: '#1F3A5C',
    botKey: '#242931',
    botKeyOn: '#2F3A4B',
  },
};

/** «Зал оракула». Translucent panels let the living hall show through; text always lies on a dense panel. */
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

/** The palette for a look: the oracle hall is always dark, the calm look follows the scheme. */
export function tokensFor(which: Look, scheme: ColorScheme): ThemeTokens {
  return which === 'oracle' ? oracleTheme : themes[scheme];
}

/** Fill colours of the «да» and «нет» confirm buttons; the same in light and dark. */
export function sideFills(which: Look = look): { yes: string; no: string } {
  const t = which === 'oracle' ? oracleTheme : themes.light;
  return { yes: t.yesFill, no: t.noFill };
}

export function applyTheme(scheme: ColorScheme, which: Look = look): void {
  const root = document.documentElement;
  for (const [key, value] of Object.entries(tokensFor(which, scheme))) {
    root.style.setProperty(cssName(key), value);
  }
  const shown = which === 'oracle' ? 'dark' : scheme;
  root.dataset.look = which;
  root.dataset.theme = shown;
  root.style.colorScheme = shown;
}

/** Applies the theme and keeps the calm look in sync with Telegram or the OS setting. */
export function useThemeSync(): ColorScheme {
  const [scheme, setScheme] = useState<ColorScheme>(() => tg.colorScheme);

  useEffect(() => {
    applyTheme(scheme);
    // The hall paints Telegram's own header and background to match; the calm look leaves Telegram's.
    if (look === 'oracle') tg.paintChrome(oracleTheme.bg);
  }, [scheme]);

  useEffect(() => {
    if (look === 'oracle') return;
    if (isTelegram()) {
      return tg.onEvent('themeChanged', () => setScheme(tg.colorScheme));
    }
    const media = window.matchMedia?.('(prefers-color-scheme: dark)');
    if (!media) return;
    const onChange = (e: MediaQueryListEvent) => setScheme(e.matches ? 'dark' : 'light');
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, []);

  return scheme;
}
