import { createContext, useContext, useRef } from 'react';

/**
 * Whether the kept page this component belongs to is the one on screen. Pages that are not kept (chart,
 * financials, dialogs outside pages) read the default `true`.
 */
export const PageActiveContext = createContext(true);

export const usePageActive = () => useContext(PageActiveContext);

/**
 * `value` while the page is on screen; the last on-screen value while it is hidden. Effects that depend on it do not
 * re-run (and issue no requests) while hidden, and run once on return only if it changed meanwhile.
 */
export function useWhileActive<T>(value: T): T {
  const active = usePageActive();
  const last = useRef(value);
  if (active) last.current = value;
  return last.current;
}
