/** Build-time Web Push configuration and notification-tap routing (kept tiny: loaded at startup). */
const PUBLIC_KEY_PATTERN = /^B[A-Za-z0-9_-]{86}$/;

export function configuredPublicKey(value: unknown = import.meta.env?.VITE_WEB_PUSH_PUBLIC_KEY): string {
  return typeof value === 'string' && PUBLIC_KEY_PATTERN.test(value.trim()) ? value.trim() : '';
}
export const WEB_PUSH_PUBLIC_KEY = configuredPublicKey();
export const webPushEnabled = () => WEB_PUSH_PUBLIC_KEY !== '';

const base = () => new URL(import.meta.env?.BASE_URL || '/', location.origin);
export const pushScope = () => new URL('push/', base()).href;
export const pushScript = () => new URL('push-sw.js', base()).href;
/** At most one worker update check per foreground return within this window. */
const WORKER_REFRESH_MS = 10 * 60 * 1000;
/** When a foregrounded app asks the push worker for a pending tap. */
const TAP_PULL_DELAYS_MS = [0, 400, 1500, 3000, 6000, 10000];

// TEMPORARY tap diagnostics (iPhone background tap investigation). Armed for a few minutes by the test button,
// shows each step on screen, memory only. Remove once the background tap works.
const DIAGNOSTICS_MS = 3 * 60 * 1000;
let diagnosticsUntil = 0;
const diagnosticLines: string[] = [];
export function armPushDiagnostics() {
  diagnosticsUntil = Date.now() + DIAGNOSTICS_MS;
  diagnosticLines.length = 0;
  diagnose('診斷已開啟：把 Atlas 留在背景後點通知');
}
let diagnosticHide: ReturnType<typeof setTimeout> | undefined;
function diagnose(text: string) {
  if (Date.now() > diagnosticsUntil || typeof document === 'undefined' || !document.body) return;
  const time = new Date().toLocaleTimeString('zh-TW', { hour12: false });
  diagnosticLines.push(`${time} ${text}`);
  if (diagnosticLines.length > 24) diagnosticLines.splice(0, diagnosticLines.length - 24);
  let box = document.getElementById('atlas-push-diagnostics');
  if (!box) {
    box = document.createElement('pre');
    box.id = 'atlas-push-diagnostics';
    box.style.cssText = 'position:fixed;left:8px;right:8px;bottom:8px;z-index:2147483647;max-height:45vh;overflow:auto;margin:0;'
      + 'padding:8px;font:11px/1.4 ui-monospace,monospace;white-space:pre-wrap;color:#e6edf3;background:rgba(13,17,23,.94);'
      + 'border:1px solid #f0b72f;border-radius:8px;pointer-events:auto';
    box.addEventListener('click', () => box?.remove());
    document.body.appendChild(box);
  }
  box.textContent = `推播點擊診斷（點此關閉）\n${diagnosticLines.join('\n')}`;
  clearTimeout(diagnosticHide);
  diagnosticHide = setTimeout(() => document.getElementById('atlas-push-diagnostics')?.remove(), 60_000);
}

/** Capture taps at startup and defer URL changes until LINE has consumed any OAuth return. */
export function startPushNavigation() {
  if (!webPushEnabled() || !('serviceWorker' in navigator)) return () => {};
  const root = base();
  const script = pushScript();
  let ready = false;
  let pending: URL | null = null;
  // Same page and parameters in any order: a window opened at the tap URL does not get a duplicate entry.
  const target = (url: URL) => {
    const params = new URLSearchParams(url.search);
    params.sort();
    return `${url.origin}${url.pathname}?${params}`;
  };
  const open = (url: URL) => {
    if (target(new URL(location.href)) === target(url)) { diagnose('頁面：已經在這張圖，不切換'); return; }
    history.pushState(null, '', url.href);
    dispatchEvent(new PopStateEvent('popstate'));
    diagnose(`頁面：已切換到 ${url.search}`);
  };
  /** Accept an Atlas URL from the push worker; returns it when valid. */
  const accept = (value: unknown): URL | null => {
    if (typeof value !== 'string') return null;
    let url: URL;
    try { url = new URL(value, root); } catch { return null; }
    if (url.origin !== root.origin || !url.pathname.startsWith(root.pathname)) { diagnose('頁面：網址不在 Atlas，忽略'); return null; }
    if (ready) open(url);
    else { pending = url; diagnose('頁面：尚未就緒，稍後切換'); }
    return url;
  };
  navigator.serviceWorker.addEventListener('message', (event: MessageEvent) => {
    const source = event.source as ServiceWorker | null;
    const data = event.data as { type?: string; url?: string } | null;
    if (data?.type !== 'atlas-push-open') return;
    diagnose(`頁面：收到 worker 推送的訊息${source?.scriptURL === script ? '' : '（來源不符）'}`);
    if (source?.scriptURL !== script) return;
    const url = accept(data.url);
    if (url) event.ports[0]?.postMessage(url.href);
  });
  // Ask the push worker for a tap it is still holding (iOS does not deliver its message to a resumed app).
  const pullTap = (reason = '') => {
    navigator.serviceWorker?.getRegistration?.(pushScope()).then((registration) => {
      const worker = registration?.active;
      if (!worker || worker.scriptURL !== script || typeof MessageChannel === 'undefined') {
        diagnose(`頁面：取回${reason}失敗，${!registration ? '找不到推播註冊' : !worker ? '推播 worker 未啟用' : '推播 worker 網址不符'}`);
        return;
      }
      const channel = new MessageChannel();
      channel.port1.onmessage = (event: MessageEvent) => {
        channel.port1.close();
        const reply = event.data as { url?: unknown; trace?: unknown; worker?: unknown; pushes?: unknown } | string | null;
        const url = reply && typeof reply === 'object' ? reply.url : reply;
        const steps = reply && typeof reply === 'object' && Array.isArray(reply.trace) ? reply.trace.map(String) : [];
        const events = reply && typeof reply === 'object' && Array.isArray(reply.pushes) ? reply.pushes.map(String) : [];
        const instance = reply && typeof reply === 'object' && typeof reply.worker === 'string' ? reply.worker : '?';
        diagnose(`頁面：取回${reason} → ${typeof url === 'string' ? '有通知' : '沒有待處理的通知'}（worker ${instance}）`
          + `${events.length ? `\n  事件：${events.join('\n  事件：')}` : ''}${steps.length ? `\n  步驟：${steps.join('\n  步驟：')}` : ''}`);
        accept(url);
      };
      worker.postMessage({ type: 'atlas-push-pull' }, [channel.port2]);
    }).catch((error: unknown) => { diagnose(`頁面：取回${reason}出錯 ${error instanceof Error ? error.name : ''}`); });
  };
  navigator.serviceWorker.startMessages?.();
  // The push worker's scope (push/) is never navigated to, so the browser rarely checks it for updates on its own.
  // Ask at start and when the app returns to the foreground (iOS resumes a Home Screen app without restarting it),
  // so a deployed fix to notification taps reaches devices that enabled push earlier.
  const refreshWorker = () => {
    navigator.serviceWorker?.getRegistration?.(pushScope()).then((registration) => registration?.update()).catch(() => {});
  };
  refreshWorker();
  let refreshedAt = Date.now();
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', () => {
      diagnose(`頁面：visibilitychange → ${document.visibilityState}`);
      if (document.visibilityState !== 'visible') return;
      navigator.serviceWorker?.getRegistration?.(pushScope()).then((registration) => registration?.getNotifications?.())
        .then((shown) => { if (shown) diagnose(`頁面：通知中心還有 ${shown.length} 則 Atlas 通知`); }).catch(() => {});
      // The tap handler may still be running when the app comes forward: ask again shortly after.
      for (const delay of TAP_PULL_DELAYS_MS) setTimeout(() => pullTap(`（${delay}ms）`), delay);
      if (Date.now() - refreshedAt < WORKER_REFRESH_MS) return;
      refreshedAt = Date.now();
      refreshWorker();
    });
  }
  if (typeof addEventListener === 'function') {
    addEventListener('pageshow', (event) => diagnose(`頁面：pageshow${(event as PageTransitionEvent).persisted ? '（從快取恢復）' : ''}`));
    addEventListener('focus', () => diagnose('頁面：focus'));
  }
  return () => {
    ready = true;
    if (pending) open(pending);
    pending = null;
    pullTap();
  };
}
