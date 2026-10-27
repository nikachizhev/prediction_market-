// Palette from theme() in docs/mockups/Main.dc.html, exposed as CSS variables.
import { useEffect, useState } from 'react';
import { tg, isTelegram, type ColorScheme } from './telegram';

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
  },
};

const cssName = (key: string): string => '--' + key.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());

export function applyTheme(scheme: ColorScheme): void {
  const root = document.documentElement;
  for (const [key, value] of Object.entries(themes[scheme])) {
    root.style.setProperty(cssName(key), value);
  }
  root.dataset.theme = scheme;
  root.style.colorScheme = scheme;
}

/** Applies the theme and keeps it in sync with Telegram or the OS setting. */
export function useThemeSync(): ColorScheme {
  const [scheme, setScheme] = useState<ColorScheme>(() => tg.colorScheme);

  useEffect(() => {
    applyTheme(scheme);
  }, [scheme]);

  useEffect(() => {
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
