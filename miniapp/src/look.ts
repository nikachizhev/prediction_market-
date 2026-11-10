// Which look the Mini App wears for this launch. The calm one by default; «Зал оракула» is kept for themed
// channels and can be seen with ?look=oracle in a browser or startapp=look_oracle in Telegram.
export type Look = 'calm' | 'oracle';

export function lookFrom(search: string, startParam: string | null | undefined): Look {
  if (startParam === 'look_oracle') return 'oracle';
  return new URLSearchParams(search).get('look') === 'oracle' ? 'oracle' : 'calm';
}

function startParamNow(): string | undefined {
  if (typeof window === 'undefined') return undefined;
  const app = (window as { Telegram?: { WebApp?: { initDataUnsafe?: { start_param?: string } } } }).Telegram?.WebApp;
  return app?.initDataUnsafe?.start_param;
}

/** Decided once at start: a look does not change while the app is open. */
export const look: Look = typeof window === 'undefined' ? 'calm' : lookFrom(window.location.search, startParamNow());
