export type LineAppLaunchPlatform = 'ios' | 'android';

/** iOS/iPadOS (including desktop-class iPad UA) and Android only. */
export function lineAppLaunchPlatform(): LineAppLaunchPlatform | null {
  const nav = globalThis.navigator;
  if (
    /iPhone|iPad|iPod/i.test(nav?.userAgent ?? '') ||
    (nav?.platform === 'MacIntel' && (nav.maxTouchPoints ?? 0) > 1)
  )
    return 'ios';
  return /Android/i.test(nav?.userAgent ?? '') ? 'android' : null;
}

/** Mobile app dispatch only; the receiver document must never navigate away. */
export function canLaunchLineAppDirectly() {
  return lineAppLaunchPlatform() !== null;
}

/** Legacy LIFF app scheme: only the LIFF ID plus the intent/write query, never read proof. */
export function lineAppSchemeUrl(liffUrl: URL) {
  return `line://app/${liffUrl.pathname.split('/')[1]}${liffUrl.search}`;
}

export function lineLaunchFallbackUrl(liffUrl: URL) {
  const fallback = new URL(
    'line-login-launch.html',
    new URL(
      import.meta.env.BASE_URL || './',
      globalThis.location?.href || 'https://atlas.invalid/',
    ),
  );
  fallback.search = liffUrl.search;
  return fallback.href;
}

/** If the page is still visible and focused this long after dispatch, LINE did not open. */
export const LINE_LAUNCH_CHECK_MS = 1500;

/**
 * iOS WebKit ignores custom-scheme navigation from subframes, so iOS uses a
 * top-level navigation started inside the same user gesture. When an app
 * handles a custom scheme, WebKit does not unload the current document, so the
 * RAM-only read secret survives. Only `line:` is ever navigated this way;
 * the receiver window never navigates to an http(s) URL or reloads.
 */
function navigateTopLevel(url: string) {
  if (!url.startsWith('line://app/')) throw new Error('unexpected scheme');
  const link = document.createElement('a');
  link.href = url;
  link.rel = 'noreferrer';
  link.referrerPolicy = 'no-referrer';
  link.hidden = true;
  link.dataset.atlasLineLaunch = 'ios';
  document.body.append(link);
  try {
    link.click();
  } finally {
    link.remove();
  }
  return () => {};
}

/**
 * Android keeps PR #185's subframe dispatch: LINE does not document an
 * `intent://` form for LIFF, and a top-level navigation to an unhandled scheme
 * (or an intent's browser_fallback_url) can replace this document in Chrome,
 * losing the read secret. A failed subframe dispatch stays inside the frame.
 */
function dispatchFrame(url: string) {
  const frame = document.createElement('iframe');
  frame.hidden = true;
  frame.title = '開啟 LINE';
  frame.referrerPolicy = 'no-referrer';
  frame.src = url;
  document.body.append(frame);
  return () => frame.remove();
}

/** Calls onMissed once if neither hidden/blur/pagehide is seen within the window. */
function watchLaunch(onMissed: () => void) {
  const stop = () => {
    clearTimeout(timer);
    window.removeEventListener('blur', stop);
    window.removeEventListener('pagehide', stop);
    document.removeEventListener('visibilitychange', hidden);
  };
  const hidden = () => {
    if (document.visibilityState === 'hidden') stop();
  };
  window.addEventListener('blur', stop);
  window.addEventListener('pagehide', stop);
  document.addEventListener('visibilitychange', hidden);
  const timer = setTimeout(() => {
    stop();
    // Never auto-open a window here: this is outside the gesture (popup blockers).
    if (
      document.visibilityState !== 'hidden' &&
      document.hasFocus?.() !== false
    )
      onMissed();
  }, LINE_LAUNCH_CHECK_MS);
  return stop;
}

/** Called synchronously by the login gesture, before awaiting begin's POST. */
export function launchLineHandoff(
  liffUrl: URL,
  onLaunchMissed: () => void = () => {},
): () => void {
  const platform = lineAppLaunchPlatform();
  if (platform) {
    // LINE documents this legacy app scheme as deprecated, with no removal date.
    // Never put read proof, reverse code or identity here. HTTPS remains the fallback.
    try {
      const url = lineAppSchemeUrl(liffUrl);
      const release =
        platform === 'ios' ? navigateTopLevel(url) : dispatchFrame(url);
      const stopWatching = watchLaunch(onLaunchMissed);
      return () => {
        stopWatching();
        release();
      };
    } catch {
      /* Use the non-blank fallback if this host cannot dispatch the scheme. */
    }
  }
  // Always load our real explanatory document, even when noopener returns null.
  try {
    window.open(
      lineLaunchFallbackUrl(liffUrl),
      '_blank',
      'noopener,noreferrer',
    );
  } catch {
    /* The manual fallback link is already visible on the original page. */
  }
  return () => {};
}
