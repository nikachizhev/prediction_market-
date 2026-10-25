import { useEffect, useRef, type ReactNode } from 'react';
import { createBrowserRouter, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { BalancePill } from './components/BalancePill';
import { TabBar } from './components/TabBar';
import { useToast } from './components/Toast';
import { grantMessage } from './grants';
import { Onboarding } from './screens/Onboarding';
import { Placeholder } from './screens/Placeholder';
import { useSession } from './session';
import { tg } from './telegram';
import {
  CreateScreen,
  FeedScreen,
  MarketScreen,
  MineScreen,
  NotFoundScreen,
  ProfileScreen,
  RatingScreen,
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
        { path: '*', element: <NotFoundScreen /> },
      ],
    },
  ],
  { basename: '/app' },
);
