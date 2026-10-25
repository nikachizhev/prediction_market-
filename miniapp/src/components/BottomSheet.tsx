import { useEffect, useRef, useState, type ReactNode, type PointerEvent } from 'react';

interface BottomSheetProps {
  open: boolean;
  onClose: () => void;
  /** Accessible name of the sheet, e.g. «Панель прогноза». */
  label: string;
  children: ReactNode;
}

const CLOSE_DISTANCE = 80; // px of downward drag that closes the sheet
const ANIMATION_MS = 220;

/** Panel sliding up from the bottom over a dimmed overlay. Closes on overlay tap or swipe down. */
export function BottomSheet({ open, onClose, label, children }: BottomSheetProps) {
  const [mounted, setMounted] = useState(open);
  const [visible, setVisible] = useState(false);
  const [drag, setDrag] = useState(0);
  const startY = useRef<number | null>(null);

  useEffect(() => {
    if (open) {
      setMounted(true);
      // Next frame so the transition runs from the closed position.
      const id = requestAnimationFrame(() => setVisible(true));
      return () => cancelAnimationFrame(id);
    }
    setVisible(false);
    const id = window.setTimeout(() => setMounted(false), ANIMATION_MS);
    return () => window.clearTimeout(id);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!mounted) return null;

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    startY.current = e.clientY;
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (startY.current === null) return;
    setDrag(Math.max(0, e.clientY - startY.current));
  };
  const onPointerUp = () => {
    if (startY.current === null) return;
    startY.current = null;
    if (drag > CLOSE_DISTANCE) onClose();
    setDrag(0);
  };

  return (
    <div className="sheet-root">
      <button
        type="button"
        className="sheet-overlay"
        aria-label="Закрыть панель"
        onClick={onClose}
        style={{ opacity: visible ? 1 : 0 }}
      />
      <section
        role="dialog"
        aria-modal="true"
        aria-label={label}
        className="sheet"
        style={{
          transform: visible ? `translateY(${drag}px)` : 'translateY(100%)',
          transition: startY.current === null ? `transform ${ANIMATION_MS}ms ease` : 'none',
        }}
      >
        <div
          className="sheet-grip"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          <span />
        </div>
        {children}
      </section>
    </div>
  );
}
