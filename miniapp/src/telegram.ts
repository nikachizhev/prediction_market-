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

export interface MainButtonParams {
  text?: string;
  color?: string;
  text_color?: string;
  is_active?: boolean;
  is_visible?: boolean;
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
  setParams(params: MainButtonParams): void;
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
  isVersionAtLeast(version: string): boolean;
  requestWriteAccess?(cb?: (allowed: boolean) => void): void;
  /** Bot API 8.0+: shares a message prepared by the bot (savePreparedInlineMessage). */
  shareMessage?(msgId: string, cb?: (sent: boolean) => void): void;
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
    setParams(params: MainButtonParams): void {
      webApp()?.MainButton.setParams(params);
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

  /** True when the client can ask for permission to receive bot messages. */
  get canRequestWriteAccess(): boolean {
    const app = webApp();
    return !!app && typeof app.requestWriteAccess === 'function' && app.isVersionAtLeast('6.9');
  },

  /** Asks the user to let the bot write to them; resolves with the answer. */
  requestWriteAccess(): Promise<boolean> {
    const app = webApp();
    if (!app || typeof app.requestWriteAccess !== 'function') return Promise.resolve(false);
    return new Promise((resolve) => app.requestWriteAccess!((allowed) => resolve(allowed)));
  },

  /** True when the client supports Telegram.WebApp.shareMessage (8.0+). */
  get canShareMessage(): boolean {
    const app = webApp();
    return !!app && typeof app.shareMessage === 'function' && app.isVersionAtLeast('8.0');
  },

  /**
   * Opens Telegram's dialog for a prepared message; resolves with whether it was
   * sent. Rejects when the client cannot do it, so the caller can fall back.
   */
  shareMessage(msgId: string): Promise<boolean> {
    const app = webApp();
    if (!app || typeof app.shareMessage !== 'function') return Promise.reject(new Error('shareMessage is unavailable'));
    return new Promise((resolve, reject) => {
      try {
        app.shareMessage!(msgId, (sent) => resolve(!!sent));
      } catch (err) {
        reject(err);
      }
    });
  },

  /** Subscribes to a WebApp event; returns an unsubscribe function. */
  onEvent(event: TelegramEvent, cb: () => void): () => void {
    const app = webApp();
    if (!app) return () => {};
    app.onEvent(event, cb);
    return () => app.offEvent(event, cb);
  },
};
