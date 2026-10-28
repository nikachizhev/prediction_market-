import { useEffect, useRef } from 'react';
import { isTelegram, tg } from '../telegram';

interface MainActionProps {
  text: string;
  onClick: () => void;
  disabled?: boolean;
  /** Hex colour (Telegram's MainButton does not understand CSS variables). */
  color?: string;
  /** Shows progress and ignores taps. */
  busy?: boolean;
  /** Look of the browser fallback when no colour is given: blue or the ink colour. */
  variant?: 'primary' | 'ink';
}

/**
 * The screen's main action: Telegram's native MainButton inside Telegram,
 * a regular full-width button in a plain browser.
 */
export function MainAction({ text, onClick, disabled = false, color, busy = false, variant = 'primary' }: MainActionProps) {
  const inTelegram = isTelegram();
  const handler = useRef(onClick);
  handler.current = onClick;
  const blocked = useRef(disabled || busy);
  blocked.current = disabled || busy;

  useEffect(() => {
    if (!inTelegram) return;
    tg.MainButton.show();
    const off = tg.MainButton.onClick(() => {
      if (!blocked.current) handler.current();
    });
    return () => {
      off();
      tg.MainButton.hideProgress();
      tg.MainButton.hide();
    };
  }, [inTelegram]);

  useEffect(() => {
    if (!inTelegram) return;
    const theme = tg.themeParams;
    const fill = color ?? theme.button_color;
    const ink = color ? '#FFFFFF' : theme.button_text_color;
    // Telegram hideProgress may re-enable the button; apply validation afterwards.
    if (!busy) tg.MainButton.hideProgress();
    tg.MainButton.setParams({
      text,
      is_active: !disabled && !busy,
      ...(fill ? { color: fill } : {}),
      ...(ink ? { text_color: ink } : {}),
    });
    if (busy) tg.MainButton.showProgress();
  }, [inTelegram, text, disabled, color, busy]);

  if (inTelegram) return null;
  return (
    <button
      type="button"
      className={`button-primary button-block ${variant === 'ink' ? 'is-ink' : ''}`}
      style={color ? { background: color } : undefined}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      onClick={onClick}
    >
      {text}
    </button>
  );
}
