import { createRoot } from 'react-dom/client';
import { AccessBoundary } from './auth/AccessBoundary';
import { applyColorConvention } from './app/sitePreferences';
import { SCREENER_HOSTING } from './app/HostingMode';
import { accessState, initializeAccess } from './auth/access';
import { retireScreenerWorker } from './auth/retireWorker';
import { DEFAULT_LIFF_ID, needsLineIdentityBeforeRouting, startLineIdentity } from './cloud/lineIdentity';
import { PUBLIC_BASE } from './app/HostingMode';
import './styles.css';
import { prepareIncomingLineHandoff } from './cloud/lineLoginHandoff';
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
    // An explicit external LINE callback must finish before the router can
    // normalize its URL. A slow SDK gets a recovery screen with the URL intact.
    if (needsLineIdentityBeforeRouting()) {
      const root = document.getElementById('root')!;
      root.textContent = '正在完成 LINE 登入…';
      let timer: ReturnType<typeof setTimeout> | undefined;
      const finished = await Promise.race([
        startLineIdentity(import.meta.env.VITE_LIFF_ID || DEFAULT_LIFF_ID).then(identity => { prepareIncomingLineHandoff(identity); return true; }),
        new Promise<boolean>(resolve => { timer = setTimeout(() => resolve(false), 15000); }),
      ]);
      clearTimeout(timer);
      if (!finished) {
        createRoot(root).render(<main className="page-state" role="alert">
          <b>LINE 登入尚未完成</b>
          <p>連線恢復後可重新嘗試，或先以訪客開啟 Atlas。</p>
          <button type="button" className="primary-button" onClick={() => location.reload()}>重新嘗試登入</button>
          <a href={`${PUBLIC_BASE}?atlasGuest=1`}>先開啟 Atlas</a>
        </main>);
        return;
      }
    }
    // The configured LIFF endpoint is the parent root. Render this same entry
    // there; canonicalize only AFTER primary SDK handling and any handoff.
    if (location.pathname === new URL('../', new URL(PUBLIC_BASE, location.origin)).pathname)
      history.replaceState(history.state, '', `${PUBLIC_BASE}${location.search}${location.hash}`);
    // No guest app persistence, cached grants, or automatic legacy-data adoption.
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
