import { createRoot } from 'react-dom/client';
import { AccessBoundary } from './auth/AccessBoundary';
import { applyColorConvention } from './app/sitePreferences';
import { SCREENER_HOSTING } from './app/HostingMode';
import { accessState, initializeAccess } from './auth/access';
import { retireScreenerWorker } from './auth/retireWorker';
import './styles.css';
// Packaged native builds use their bundled assets, independent of PWA shell updates.
if (
  'serviceWorker' in navigator &&
  !SCREENER_HOSTING &&
  import.meta.env.PROD &&
  import.meta.env.VITE_NATIVE_WRAPPER !== '1'
)
  window.addEventListener('load', () => {
    void navigator.serviceWorker
      .register(`${import.meta.env.BASE_URL}sw.js`, { scope: import.meta.env.BASE_URL })
      .catch(console.error);
  });

applyColorConvention();

/** Upper bound on how long LINE login may delay the first render. */
const LIFF_START_TIMEOUT_MS = 4000;

async function render() {
  if (SCREENER_HOSTING) {
    // No guest storage, SDK outside LINE, cached grant, or automatic legacy-data adoption.
    // Delayed login may resolve later; the boundary then replaces the entire temporary workspace.
    await Promise.race([
      initializeAccess(),
      new Promise((resolve) => setTimeout(resolve, LIFF_START_TIMEOUT_MS)),
    ]);
    accessState.allowGuestWhileLoading();
    await retireScreenerWorker(new URL(import.meta.env.BASE_URL, location.origin).href);
  }
  // The chart workspace loads its axis font itself; the landing page renders immediately.
  createRoot(document.getElementById('root')!).render(<AccessBoundary />);
}

void render();
