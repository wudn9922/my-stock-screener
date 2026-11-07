import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { PageActiveContext } from './pageActivity';

/**
 * Keeps a main page mounted after its first visit (like the chart page) while another page is on screen.
 *
 * The page renders into its own container element, which is attached under `<main>` only while the page is active.
 * A hidden page is therefore not in the document at all: no duplicate ids, labels or test selectors, nothing exposed
 * to assistive technology. Its React state (filters, open list, expanded sections, "load more" counts) survives, and
 * while hidden it is not re-rendered by the shell, so prop-driven effects do not fire; live polling and other
 * timers check `usePageActive()`. Scroll offsets of inner scrollers are recorded as they change and put back when the
 * container is attached again (a detached element loses its scroll offset).
 */
export function KeptPage({ page, active, children }: { page: string; active: boolean; children: ReactNode }) {
  const [container] = useState(() => {
    const element = document.createElement('div');
    element.className = 'kept-page';
    element.dataset.keptPage = page;
    return element;
  });
  // The shell re-renders on every navigation; a hidden page keeps the element it last showed.
  const shown = useRef(children);
  if (active) shown.current = children;
  const scrolls = useRef(new Map<Element, readonly [number, number]>());
  useEffect(() => {
    const record = (event: Event) => {
      const target = event.target;
      // A scroller that loses its box on detach reports 0; only on-screen scrolling is recorded.
      if (container.isConnected && target instanceof Element && container.contains(target))
        scrolls.current.set(target, [target.scrollTop, target.scrollLeft]);
    };
    container.addEventListener('scroll', record, { capture: true, passive: true });
    return () => container.removeEventListener('scroll', record, { capture: true });
  }, [container]);
  // Attach/detach in the commit itself (ref callbacks run before the page's own layout effects), so the page always
  // measures itself attached.
  const host = useCallback((element: HTMLDivElement | null) => {
    if (!element || !active) return;
    element.appendChild(container);
    for (const [target, [top, left]] of scrolls.current) {
      if (!container.contains(target)) {
        scrolls.current.delete(target);
        continue;
      }
      if (target.scrollTop !== top) target.scrollTop = top;
      if (target.scrollLeft !== left) target.scrollLeft = left;
    }
    return () => container.remove();
  }, [active, container]);
  return (
    <>
      <div ref={host} className="kept-page-host" />
      {createPortal(<PageActiveContext.Provider value={active}>{shown.current}</PageActiveContext.Provider>, container)}
    </>
  );
}

/** Scrolls the active kept page's inner scrollers (e.g. the screener list) back to the top. */
export function scrollKeptPageToTop(page: string) {
  const container = document.querySelector<HTMLElement>(`.kept-page[data-kept-page="${page}"]`);
  if (!container) return false;
  let scrolled = false;
  for (const element of container.querySelectorAll<HTMLElement>('*')) {
    if (element.scrollTop > 0) {
      element.scrollTop = 0;
      scrolled = true;
    }
  }
  return scrolled;
}
