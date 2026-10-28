// Opens the trade panel for a market and runs the optimistic purchase.
import { useCallback, useRef, useState, type ReactNode } from 'react';
import { api, ApiError, newRequestKey, type MarketCard, type MarketDetail, type Quote, type Side, type TradeResult } from '../api';
import { useSession } from '../session';
import { tg } from '../telegram';
import {
  acceptedMessage,
  cardBlockReason,
  optimisticCost,
  runOptimisticBuy,
  toMoney,
  tradeBlockReason,
} from '../trade';
import { BottomSheet } from './BottomSheet';
import { useToast } from './Toast';
import { TradePanel } from './TradePanel';

export interface TradeHandlers {
  /** Show the new probability right away. */
  onOptimistic?: (marketId: number, probAfter: number) => void;
  /** The server accepted the trade; `result.market` is the fresh market. */
  onCommit?: (result: TradeResult) => void;
  /** The server refused: restore what onOptimistic changed. */
  onRollback?: (marketId: number) => void;
}

interface Opened {
  market: MarketDetail;
  side: Side;
  /** Remounts the panel for every opening. */
  key: number;
}

const isDetail = (m: MarketCard | MarketDetail): m is MarketDetail => 'can_trade' in m;

/**
 * Returns `open(market, side)` and the sheet element to render. A feed card is
 * re-fetched first to learn `can_trade` and `max_amount`; when trading is not
 * possible the sheet stays closed and a toast explains why.
 */
export function useTradeSheet(handlers: TradeHandlers = {}): {
  open: (market: MarketCard | MarketDetail, side: Side) => Promise<void>;
  sheet: ReactNode;
  pendingId: number | null;
} {
  const { me, adjustBalance, setBalance, refresh } = useSession();
  const toast = useToast();
  const [opened, setOpened] = useState<Opened | null>(null);
  const [visible, setVisible] = useState(false);
  const [pendingId, setPendingId] = useState<number | null>(null);
  const openSeq = useRef(0);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const open = useCallback(
    async (market: MarketCard | MarketDetail, side: Side) => {
      tg.HapticFeedback.impact('light');
      const closed = cardBlockReason(market);
      if (closed) {
        toast(closed, 'error');
        return;
      }
      const seq = ++openSeq.current;
      let detail: MarketDetail;
      if (isDetail(market)) {
        detail = market;
      } else {
        setPendingId(market.id);
        try {
          detail = await api.market(market.id);
        } catch (err) {
          if (seq === openSeq.current) {
            setPendingId(null);
            toast(err instanceof ApiError ? err.message : 'Не удалось открыть рынок.', 'error');
          }
          return;
        }
        if (seq !== openSeq.current) return;
        setPendingId(null);
      }
      const reason = tradeBlockReason(detail, me);
      if (reason) {
        toast(reason, 'error');
        return;
      }
      setOpened({ market: detail, side, key: seq });
      setVisible(true);
    },
    [me, toast],
  );

  const close = useCallback(() => setVisible(false), []);

  const confirm = useCallback(
    (side: Side, amount: number, quote: Quote | null) => {
      if (!opened) return;
      const marketId = opened.market.id;
      const requestKey = newRequestKey();
      const h = handlersRef.current;
      setVisible(false);
      void runOptimisticBuy({
        send: () => api.buy(marketId, side, toMoney(amount), requestKey),
        cost: optimisticCost(amount, side, quote),
        probAfter: quote ? quote.prob_after : null,
        adjustBalance,
        setBalance,
        onOptimistic: (p) => h.onOptimistic?.(marketId, p),
        onCommit: (result) => h.onCommit?.(result),
        onRollback: () => h.onRollback?.(marketId),
      }).then((outcome) => {
        if (outcome.ok) {
          tg.HapticFeedback.notification('success');
          toast(acceptedMessage(side, amount, outcome.result.trade.shares));
        } else {
          tg.HapticFeedback.notification('error');
          toast(outcome.error.message, 'error');
          // The optimistic rollback is local; make sure the balance matches the server.
          refresh().catch(() => {});
        }
      });
    },
    [opened, adjustBalance, setBalance, refresh, toast],
  );

  const sheet = (
    <BottomSheet open={visible} onClose={close} label="Панель прогноза">
      {opened && <TradePanel key={opened.key} market={opened.market} initialSide={opened.side} onConfirm={confirm} />}
    </BottomSheet>
  );

  return { open, sheet, pendingId };
}
