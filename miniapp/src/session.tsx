// User session: POST /api/session on start, `me` and balance shared via context.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, ApiError, type Grant, type Me, type Money, type Session } from './api';
import { tg } from './telegram';

interface SessionState {
  me: Me | null;
  granted: Grant[];
  newAccount: boolean;
  loading: boolean;
  error: ApiError | null;
  /** Current balance as a number (0 while loading). */
  balance: number;
  /** Updates the balance after a trade or market creation. */
  setBalance: (balance: Money) => void;
  /** Shifts the balance by `delta` coins (optimistic trades and their rollback). */
  adjustBalance: (delta: number) => void;
  /** Replaces `me` (e.g. after PATCH /api/me). */
  setMe: (me: Me) => void;
  /** Re-fetches `me` from the server. */
  refresh: () => Promise<void>;
  /** Retries the session start after an error. */
  retry: () => void;
}

const SessionContext = createContext<SessionState | null>(null);

// POST /api/session creates the account and grants bonuses, so it must run once
// per attempt even when React (StrictMode) runs the effect twice: the second
// call would report new_account = false and the onboarding would be lost.
let started: { attempt: number; promise: Promise<Session> } | null = null;

function startSession(attempt: number): Promise<Session> {
  if (!started || started.attempt !== attempt) {
    started = { attempt, promise: api.session(tg.startParam) };
  }
  return started.promise;
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [granted, setGranted] = useState<Grant[]>([]);
  const [newAccount, setNewAccount] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    startSession(attempt)
      .then((res) => {
        if (cancelled) return;
        setMe(res.me);
        setGranted(res.granted);
        setNewAccount(res.new_account);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err : new ApiError(0, 'Не удалось войти. Попробуйте ещё раз.'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const setBalance = useCallback((balance: Money) => {
    setMe((prev) => (prev ? { ...prev, balance } : prev));
  }, []);

  const adjustBalance = useCallback((delta: number) => {
    setMe((prev) => (prev ? { ...prev, balance: (Number(prev.balance) + delta).toFixed(2) } : prev));
  }, []);

  const refresh = useCallback(async () => {
    setMe(await api.me());
  }, []);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const value = useMemo<SessionState>(
    () => ({
      me,
      granted,
      newAccount,
      loading,
      error,
      balance: me ? Number(me.balance) : 0,
      setBalance,
      adjustBalance,
      setMe,
      refresh,
      retry,
    }),
    [me, granted, newAccount, loading, error, setBalance, adjustBalance, refresh, retry],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error('useSession must be used inside SessionProvider');
  return ctx;
}
