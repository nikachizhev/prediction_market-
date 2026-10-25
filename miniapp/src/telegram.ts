// Thin typed wrapper over window.Telegram.WebApp.
// Outside Telegram (regular browser) every call is a safe no-op and initData
// comes from VITE_DEV_INIT_DATA.

export type ColorScheme = 'light' | 'dark';

export interface ThemeParams {
  bg_color?: string;
  text_color?: string;
  hint_color?: string;
  link_color?: string;
  button_color?: string;
  button_text_color?: string;
  secondary_bg_color?: string;
  header_bg_color?: string;
  accent_text_color?: string;
  section_bg_color?: string;
  section_header_text_color?: string;
  subtitle_text_color?: string;
  destructive_text_color?: string;
}

type ImpactStyle = 'light' | 'medium' | 'heavy' | 'rigid' | 'soft';
type NotificationType = 'error' | 'success' | 'warning';
type TelegramEvent = 'themeChanged' | 'viewportChanged' | 'mainButtonClicked' | 'backButtonClicked';

interface TgBackButton {
  isVisible: boolean;
  show(): void;
  hide(): void;
  onClick(cb: () => void): void;
  offClick(cb: () => void): void;
}

interface TgMainButton {
  text: string;
  isVisible: boolean;
  setText(text: string): void;
  show(): void;
  hide(): void;
  enable(): void;
  disable(): void;
  showProgress(leaveActive?: boolean): void;
  hideProgress(): void;
  onClick(cb: () => void): void;
  offClick(cb: () => void): void;
}

interface TgHapticFeedback {
  impactOccurred(style: ImpactStyle): void;
  notificationOccurred(type: NotificationType): void;
  selectionChanged(): void;
}

interface TgWebApp {
  initData: string;
  initDataUnsafe: { start_param?: string; [key: string]: unknown };
  version: string;
  platform: string;
  colorScheme: ColorScheme;
  themeParams: ThemeParams;
  BackButton: TgBackButton;
  MainButton: TgMainButton;
  HapticFeedback: TgHapticFeedback;
  ready(): void;
  expand(): void;
  openTelegramLink(url: string): void;
  openLink(url: string): void;
  onEvent(event: TelegramEvent, cb: () => void): void;
  offEvent(event: TelegramEvent, cb: () => void): void;
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TgWebApp };
  }
}

function webApp(): TgWebApp | null {
  if (typeof window === 'undefined') return null;
  const app = window.Telegram?.WebApp;
  // telegram-web-app.js defines WebApp even in a plain browser; real launches
  // always carry a non-empty initData.
  return app && app.initData ? app : null;
}

/** True when running inside a Telegram client. */
export const isTelegram = (): boolean => webApp() !== null;

function systemScheme(): ColorScheme {
  if (typeof window === 'undefined' || !window.matchMedia) return 'light';
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export const tg = {
  ready(): void {
    webApp()?.ready();
  },

  expand(): void {
    webApp()?.expand();
  },

  get initData(): string {
    return webApp()?.initData ?? import.meta.env.VITE_DEV_INIT_DATA ?? '';
  },

  get startParam(): string | undefined {
    const app = webApp();
    if (app) return app.initDataUnsafe.start_param;
    // Allow ?startapp=... in a regular browser for local testing.
    if (typeof window === 'undefined') return undefined;
    return new URLSearchParams(window.location.search).get('startapp') ?? undefined;
  },

  get colorScheme(): ColorScheme {
    return webApp()?.colorScheme ?? systemScheme();
  },

  get themeParams(): ThemeParams {
    return webApp()?.themeParams ?? {};
  },

  BackButton: {
    show(): void {
      webApp()?.BackButton.show();
    },
    hide(): void {
      webApp()?.BackButton.hide();
    },
    /** Subscribes to taps; returns an unsubscribe function. */
    onClick(cb: () => void): () => void {
      const app = webApp();
      if (!app) return () => {};
      app.BackButton.onClick(cb);
      return () => app.BackButton.offClick(cb);
    },
  },

  MainButton: {
    setText(text: string): void {
      webApp()?.MainButton.setText(text);
    },
    show(): void {
      webApp()?.MainButton.show();
    },
    hide(): void {
      webApp()?.MainButton.hide();
    },
    showProgress(): void {
      webApp()?.MainButton.showProgress(false);
    },
    hideProgress(): void {
      webApp()?.MainButton.hideProgress();
    },
    /** Subscribes to taps; returns an unsubscribe function. */
    onClick(cb: () => void): () => void {
      const app = webApp();
      if (!app) return () => {};
      app.MainButton.onClick(cb);
      return () => app.MainButton.offClick(cb);
    },
  },

  HapticFeedback: {
    impact(style: ImpactStyle = 'light'): void {
      webApp()?.HapticFeedback.impactOccurred(style);
    },
    notification(type: NotificationType): void {
      webApp()?.HapticFeedback.notificationOccurred(type);
    },
    selection(): void {
      webApp()?.HapticFeedback.selectionChanged();
    },
  },

  openTelegramLink(url: string): void {
    const app = webApp();
    if (app) app.openTelegramLink(url);
    else window.open(url, '_blank', 'noopener');
  },

  /** Subscribes to a WebApp event; returns an unsubscribe function. */
  onEvent(event: TelegramEvent, cb: () => void): () => void {
    const app = webApp();
    if (!app) return () => {};
    app.onEvent(event, cb);
    return () => app.offEvent(event, cb);
  },
};
