import { DEFAULT_LIFF_ID } from './lineLoginConfig';
// This document carries only the same intent/write proof as the LIFF URL.
// It never polls, accepts an identity or accesses browser storage.
const params = new URLSearchParams(location.search);
const intent = params.get('atlasLineHandoff');
const write = params.get('atlasLineWrite');
const liff = import.meta.env.VITE_LIFF_ID || DEFAULT_LIFF_ID;
const link = document.getElementById('continue-line') as HTMLAnchorElement;
history.replaceState(null, '', location.pathname);
if (
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(
    intent || '',
  ) &&
  /^[A-Za-z0-9_-]{43}$/.test(write || '') &&
  /^\d+-[A-Za-z0-9]+$/.test(liff || '')
) {
  const target = new URL(`https://liff.line.me/${liff}/atlas/`);
  target.searchParams.set('atlasLineHandoff', intent!);
  target.searchParams.set('atlasLineWrite', write!);
  link.href = target.href;
} else document.getElementById('launch-error')!.hidden = false;

let launched = false,
  left = false;
const closeWindow = () => {
  try {
    window.close();
  } catch {
    /* The visible X instructions remain. */
  }
};
link.addEventListener('click', () => {
  launched = true;
});
document.getElementById('close-window')!.addEventListener('click', closeWindow);
document.addEventListener('visibilitychange', () => {
  if (!launched) return;
  if (document.visibilityState === 'hidden') left = true;
  else if (left) closeWindow();
});
window.addEventListener('blur', () => {
  if (launched) left = true;
});
window.addEventListener('focus', () => {
  if (launched && left) closeWindow();
});
