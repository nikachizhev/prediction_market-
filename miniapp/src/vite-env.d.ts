/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Signed initData used when the app runs outside Telegram (local development). */
  readonly VITE_DEV_INIT_DATA?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
