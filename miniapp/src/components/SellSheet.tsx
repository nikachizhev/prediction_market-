// «Продать»: confirmation sheet and the optimistic sale, shared by the market card and «Мои».
import { useCallback, useRef, useState, type ReactNode } from 'react';
import { api, type Position, type Side, type TradeResult } from '../api';
import { formatMoney } from '../format';
import { estimateSellProceeds, sharesOf } from '../market';
import { useSession } from '../session';
import { tg } from '../telegram';
import { runOptimisticBuy, sideLabel } from '../trade';
import { BottomSheet } from './BottomSheet';
import { useToast } from './Toast';
import './sell.css';

export interface SellTarget {
  id: number;
  title: string;
  prob: number;
  position: Position;
  /** LMSR depth of this market; chat markets have their own (docs/api.md «Фаза 3b»). */
  b?: number;
}

export interface SellHandlers {
  /** Hide the sold side right away; `proceeds` is the estimate. */
  onOptimistic?: (marketId: number, side: Side, proceeds: number) => void;
  onCommit?: (result: TradeResult) => void;
  onRollback?: (marketId: number) => void;
}

interface Pending {
  target: SellTarget;
  side: Side;
  shares: string;
  estimate: number;
  feeRate: number;
}

/** Returns `request(target, side)` that asks for confirmation, and the sheet to render. */
export function useSell(handlers: SellHandlers = {}): {
  request: (target: SellTarget, side: Side) => void;
  sheet: ReactNode;
  sellingId: number | null;
} {
  const { me, adjustBalance, setBalance, refresh } = useSession();
  const toast = useToast();
  const [pending, setPending] = useState<Pending | null>(null);
  const [visible, setVisible] = useState(false);
  const [sellingId, setSellingId] = useState<number | null>(null);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const request = useCallback(
    (target: SellTarget, side: Side) => {
      const shares = sharesOf(target.position, side);
      if (!(Number(shares) > 0)) return;
      tg.HapticFeedback.impact('light');
      const b = target.b ?? me?.limits.b ?? 144;
      const fee = me?.limits.fee_rate ?? 0.02;
      setPending({ target, side, shares, estimate: estimateSellProceeds(Number(shares), side, target.prob, b, fee), feeRate: fee });
      setVisible(true);
    },
    [me],
  );

  const close = useCallback(() => setVisible(false), []);

  const confirm = useCallback(() => {
    if (!pending) return;
    const { target, side, shares, estimate } = pending;
    const h = handlersRef.current;
    setVisible(false);
    setSellingId(target.id);
    h.onOptimistic?.(target.id, side, estimate);
    void runOptimisticBuy({
      send: () => api.sell(target.id, side, shares),
      // A sale adds coins: a negative cost. No retries — a sale has no idempotency key.
      cost: -estimate,
      probAfter: null,
      retryDelays: [],
      adjustBalance,
      setBalance,
      onCommit: (result) => h.onCommit?.(result),
      onRollback: () => h.onRollback?.(target.id),
    }).then((outcome) => {
      setSellingId(null);
      if (outcome.ok) {
        tg.HapticFeedback.notification('success');
        toast(`Продано: ${sideLabel(side)}. На баланс зачислено ${formatMoney(outcome.result.trade.cost)}`);
      } else {
        tg.HapticFeedback.notification('error');
        toast(outcome.error.message, 'error');
        refresh().catch(() => {});
      }
    });
  }, [pending, adjustBalance, setBalance, refresh, toast]);

  const sheet = (
    <BottomSheet open={visible} onClose={close} label="Продажа позиции">
      {pending && (
        <div className="sell-panel">
          <div className="sell-title">{pending.target.title}</div>
          <p className="sell-text">
            Продать все акции {sideLabel(pending.side)} ({formatMoney(Math.floor(Number(pending.shares)), 0)} шт.)? Получишь примерно{' '}
            <strong>{formatMoney(pending.estimate)}</strong> — точная сумма зависит от цены в момент продажи, комиссия {Math.round(pending.feeRate * 100)}%.
          </p>
          <button type="button" className="button-primary button-block is-ink" onClick={confirm}>
            Продать {sideLabel(pending.side)}
          </button>
          <button type="button" className="button-secondary button-block" onClick={close}>
            Отмена
          </button>
        </div>
      )}
    </BottomSheet>
  );

  return { request, sheet, sellingId };
}
