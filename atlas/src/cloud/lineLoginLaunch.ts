/** Mobile app dispatch only; the receiver document must never navigate away. */
export function canLaunchLineAppDirectly() {
  const nav = globalThis.navigator;
  return (
    /iPhone|iPad|iPod|Android/i.test(nav?.userAgent ?? '') ||
    (nav?.platform === 'MacIntel' && nav.maxTouchPoints > 1)
  );
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

/** Called synchronously by the login gesture, before awaiting begin's POST. */
export function launchLineHandoff(liffUrl: URL): () => void {
  if (canLaunchLineAppDirectly()) {
    // LINE documents this legacy app scheme as deprecated, with no removal date.
    // Never put read proof, reverse code or identity here. HTTPS remains the fallback.
    try {
      const frame = document.createElement('iframe');
      frame.hidden = true;
      frame.title = '開啟 LINE';
      frame.referrerPolicy = 'no-referrer';
      frame.src = `line://app/${liffUrl.pathname.split('/')[1]}${liffUrl.search}`;
      document.body.append(frame);
      return () => frame.remove();
    } catch {
      /* Use the non-blank fallback if this host cannot create the frame. */
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
