import { useEffect } from 'react';
/** Native inert backgrounds plus a small focus loop keep the sheet keyboard-accessible. */
export function useDialogFocus(open: boolean, identity?: string) {
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialog = identity
      ? [...document.querySelectorAll<HTMLElement>('[role="dialog"]')].find(
          (candidate) => candidate.dataset.dialogFocus === identity,
        )
      : document.querySelector<HTMLElement>('[role="dialog"]');
    if (!dialog) return;
    const focusable = () =>
      [
        ...dialog.querySelectorAll<HTMLElement>(
          'button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href],[tabindex="0"]',
        ),
      ].filter((e) => e.getClientRects().length);
    focusable()[0]?.focus({ preventScroll: true });
    const trap = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return;
      const nodes = focusable();
      if (!nodes.length) {
        e.preventDefault();
        return;
      }
      const first = nodes[0],
        last = nodes.at(-1)!;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus({ preventScroll: true });
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus({ preventScroll: true });
      }
    };
    document.addEventListener('keydown', trap);
    return () => {
      document.removeEventListener('keydown', trap);
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, [identity, open]);
}
