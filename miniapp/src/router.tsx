import { useEffect, useRef, type ReactNode } from 'react';
import { createBrowserRouter, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { BalancePill } from './components/BalancePill';
import { TabBar } from './components/TabBar';
import { useToast } from './components/Toast';
import { grantMessage } from './grants';
import { chatFromStartParam, marketFromStartParam } from './links';
import { Onboarding } from './screens/Onboarding';
import { Placeholder } from './screens/Placeholder';
import { useSession } from './session';
import { tg } from './telegram';
import {
  CreateScreen,
  CreatedScreen,
  FeedScreen,
  MarketScreen,
  MineScreen,
  NotFoundScreen,
  NotificationsScreen,
  ProfileScreen,
  RatingScreen,
  ChatScreen,
} from './screens/screens';

const TAB_PATHS = new Set(['/', '/mine', '/create', '/rating', '/profile']);

/** Shows Telegram's system back button on nested screens; the tab bar on top-level ones. */
function Layout() {
  const location = useLocation();
  const navigate = useNavigate();
  const nested = !TAB_PATHS.has(location.pathname);
  const { me, newAccount } = useSession();
  const onboarding = !!me && newAccount && !me.onboarded;
  useGrantToast();
  useMarketDeepLink(!!me && !onboarding);

  useEffect(() => {
    if (!nested) {
      tg.BackButton.hide();
      return;
    }
    tg.BackButton.show();
    const off = tg.BackButton.onClick(() => {
      // Opened directly by a deep link: there is no history to go back to.
      if (window.history.state && window.history.state.idx > 0) navigate(-1);
      else navigate('/', { replace: true });
    });
    return off;
  }, [nested, navigate]);

  if (onboarding) {
    return (
      <div className="app">
        <Onboarding />
      </div>
    );
  }

  return (
    <div className="app">
      <SessionGate>
        <main className="app-main">
          <Outlet />
        </main>
      </SessionGate>
      {!nested && <TabBar />}
    </div>
  );
}

// The start_param is the same for the whole launch: open its market only once.
let deepLinkHandled = false;

/**
 * `startapp=ref_<code>-m<id>` or `m<id>` opens the market, `chat_<code>` the closed chat,
 * once the session is ready.
 */
function useMarketDeepLink(ready: boolean) {
  const navigate = useNavigate();
  useEffect(() => {
    if (!ready || deepLinkHandled) return;
    deepLinkHandled = true;
    const id = marketFromStartParam(tg.startParam);
    if (id !== null) {
      navigate(`/market/${id}`);
      return;
    }
    const chat = chatFromStartParam(tg.startParam);
    if (chat !== null) navigate(`/chat/${encodeURIComponent(chat)}`);
  }, [ready, navigate]);
}

/** Tells once per launch about the daily bonus or insurance granted by POST /api/session. */
function useGrantToast() {
  const { granted } = useSession();
  const toast = useToast();
  const shown = useRef(false);
  useEffect(() => {
    if (shown.current) return;
    const text = grantMessage(granted);
    if (!text) return;
    shown.current = true;
    tg.HapticFeedback.notification('success');
    toast(text);
  }, [granted, toast]);
}

/** Shows an error with a retry button if the session could not start. */
function SessionGate({ children }: { children: ReactNode }) {
  const { me, error, retry } = useSession();
  if (error) {
    return (
      <div className="screen session-error" role="alert">
        <h1 className="screen-title">Не удалось войти</h1>
        <p className="sub">{error.message}</p>
        <button type="button" className="button-primary" onClick={retry}>
          Повторить
        </button>
      </div>
    );
  }
  // Screens call the API only after POST /api/session: it is the request that
  // creates the account and decides about the starter pack and the daily bonus.
  if (!me) return <Placeholder title="" header={<BalancePill />} cards={3} />;
  return <>{children}</>;
}

export const router = createBrowserRouter(
  [
    {
      element: <Layout />,
      children: [
        { path: '/', element: <FeedScreen /> },
        { path: '/mine', element: <MineScreen /> },
        { path: '/create', element: <CreateScreen /> },
        { path: '/rating', element: <RatingScreen /> },
        { path: '/profile', element: <ProfileScreen /> },
        { path: '/market/:id', element: <MarketScreen /> },
        { path: '/market/:id/created', element: <CreatedScreen /> },
        { path: '/notifications', element: <NotificationsScreen /> },
        { path: '/chat/:code', element: <ChatScreen /> },
        { path: '*', element: <NotFoundScreen /> },
      ],
    },
  ],
  { basename: '/app' },
);
