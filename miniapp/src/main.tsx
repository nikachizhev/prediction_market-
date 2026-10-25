import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';
import { ToastProvider } from './components/Toast';
import { router } from './router';
import { SessionProvider } from './session';
import { tg } from './telegram';
import { useThemeSync } from './theme';
import './styles.css';

function App() {
  useThemeSync();
  return (
    <SessionProvider>
      <ToastProvider>
        <RouterProvider router={router} />
      </ToastProvider>
    </SessionProvider>
  );
}

tg.ready();
tg.expand();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
