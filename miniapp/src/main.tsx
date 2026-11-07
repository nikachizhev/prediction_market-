import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';
import { ToastProvider } from './components/Toast';
import { router } from './router';
import { SessionProvider } from './session';
import { tg } from './telegram';
import { useThemeSync } from './theme';
import '@fontsource/cormorant-garamond/cyrillic-600.css';
import '@fontsource/cormorant-garamond/cyrillic-700.css';
import '@fontsource/cormorant-garamond/latin-600.css';
import '@fontsource/cormorant-garamond/latin-700.css';
import '@fontsource/ibm-plex-mono/cyrillic-400.css';
import '@fontsource/ibm-plex-mono/cyrillic-500.css';
import '@fontsource/ibm-plex-mono/cyrillic-600.css';
import '@fontsource/ibm-plex-mono/latin-400.css';
import '@fontsource/ibm-plex-mono/latin-500.css';
import '@fontsource/ibm-plex-mono/latin-600.css';
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
