import { useCallback, useEffect, useState } from 'react';
import { parseRoute, sameRoute, serializeRoute, type Page, type Route } from './routes';

export interface NavigationState {
  route: Route;
  /** Increments on every user navigation (push/pop), not on URL syncs (`replace`). */
  id: number;
}

function currentUrl(route: Route) {
  return `${window.location.pathname}${serializeRoute(route)}`;
}

/** History-API routing over query parameters; back/forward re-parse the URL. */
export function useRoute(fallback: Page) {
  const [state, setState] = useState<NavigationState>(() => {
    const parsed = parseRoute(window.location.search, fallback);
    if (parsed.rewrite) window.history.replaceState(window.history.state, '', currentUrl(parsed.route));
    return { route: parsed.route, id: 1 };
  });
  useEffect(() => {
    const pop = () =>
      setState((previous) => ({ route: parseRoute(window.location.search, fallback).route, id: previous.id + 1 }));
    window.addEventListener('popstate', pop);
    return () => window.removeEventListener('popstate', pop);
  }, [fallback]);
  /** User navigation: pushes a history entry (or replaces it when `replace`). */
  const navigate = useCallback((route: Route, options: { replace?: boolean } = {}) => {
    const url = currentUrl(route);
    if (options.replace) window.history.replaceState(null, '', url);
    else if (`${window.location.pathname}${window.location.search}` !== url) window.history.pushState(null, '', url);
    setState((previous) => ({ route, id: previous.id + 1 }));
    window.scrollTo?.(0, 0);
  }, []);
  /** Keeps the URL in sync with in-page state without a new history entry or navigation id. */
  const syncRoute = useCallback((route: Route) => {
    setState((previous) => {
      if (sameRoute(previous.route, route)) return previous;
      window.history.replaceState(window.history.state, '', currentUrl(route));
      return { route, id: previous.id };
    });
  }, []);
  return { ...state, navigate, syncRoute };
}
