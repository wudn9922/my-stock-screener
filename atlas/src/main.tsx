import { createRoot } from 'react-dom/client';
import { Site } from './app/Site';
import { applyColorConvention } from './app/sitePreferences';
import { SCREENER_HOSTING } from './app/HostingMode';
import { DEFAULT_LIFF_ID, isLineInAppBrowser, startLineIdentity } from './cloud/lineIdentity';
import './styles.css';
// Packaged native builds use their bundled assets, independent of PWA shell updates.
if (
  'serviceWorker' in navigator &&
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
  // Inside LINE, let LIFF consume its login parameters before the router rewrites the URL
  // (see cloud/lineIdentity.ts). Other browsers render immediately and never load LIFF.
  if (SCREENER_HOSTING && isLineInAppBrowser()) {
    const env = import.meta.env ?? {};
    await Promise.race([
      startLineIdentity(env.VITE_LIFF_ID || DEFAULT_LIFF_ID).catch(() => null),
      new Promise((resolve) => setTimeout(resolve, LIFF_START_TIMEOUT_MS)),
    ]);
  }
  // The chart workspace loads its axis font itself; the landing page renders immediately.
  createRoot(document.getElementById('root')!).render(<Site />);
}

void render();
