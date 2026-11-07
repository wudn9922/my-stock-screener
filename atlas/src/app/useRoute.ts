import { useCallback, useEffect, useState } from 'react';
import { parseRoute, sameRoute, serializeRoute, type Page, type Route } from './routes';

export interface NavigationState {
  route: Route;
  /** Increments on every user navigation (push/pop), not on URL syncs (`replace`). */
  id: number;
  /** `url`: page load or back/forward; `app`: an in-app navigation (search, lists, tabs). */
  source: 'url' | 'app';
  /** `history.state` of the entry (e.g. the list a chart was opened from); kept across URL syncs. */
  state: unknown;
}

function currentUrl(route: Route) {
  return `${window.location.pathname}${serializeRoute(route)}`;
}

/** History-API routing over query parameters; back/forward re-parse the URL. */
export function useRoute(fallback: Page) {
  const [state, setState] = useState<NavigationState>(() => {
    const parsed = parseRoute(window.location.search, fallback);
    if (parsed.rewrite) window.history.replaceState(window.history.state, '', currentUrl(parsed.route));
    return { route: parsed.route, id: 1, source: 'url', state: window.history.state };
  });
  useEffect(() => {
    const pop = () => {
      const parsed = parseRoute(window.location.search, fallback);
      if (parsed.rewrite) window.history.replaceState(window.history.state, '', currentUrl(parsed.route));
      setState((previous) => ({ route: parsed.route, id: previous.id + 1, source: 'url', state: window.history.state }));
    };
    window.addEventListener('popstate', pop);
    return () => window.removeEventListener('popstate', pop);
  }, [fallback]);
  /**
   * User navigation: pushes a history entry (or replaces it when `replace`). `state` is stored on the entry (only
   * this page load reads it); the same URL again replaces the entry so its state is current.
   */
  const navigate = useCallback((route: Route, options: { replace?: boolean; state?: unknown } = {}) => {
    const url = currentUrl(route);
    const state = options.state ?? null;
    if (options.replace || `${window.location.pathname}${window.location.search}` === url) window.history.replaceState(state, '', url);
    else window.history.pushState(state, '', url);
    setState((previous) => ({ route, id: previous.id + 1, source: 'app', state }));
  }, []);
  /** Keeps the URL in sync with in-page state without a new history entry or navigation id. */
  const syncRoute = useCallback((route: Route) => {
    setState((previous) => {
      if (sameRoute(previous.route, route)) return previous;
      window.history.replaceState(window.history.state, '', currentUrl(route));
      return { ...previous, route };
    });
  }, []);
  return { ...state, navigate, syncRoute };
}
