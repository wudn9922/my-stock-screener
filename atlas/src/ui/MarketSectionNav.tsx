import type { PageProps } from '../pages/pageProps';
import { serializeRoute, type Route } from '../app/routes';

export type MarketSection = 'markets' | 'world' | 'themes';
type MarketRoute = Extract<Route, { page: 'markets' }>;
export interface MarketSectionRoutes {
  /** Last selected country within 大盤. */
  markets: MarketRoute;
  world: Extract<Route, { page: 'world' }>;
  themes: Extract<Route, { page: 'themes' }>;
}

const SECTIONS: readonly (readonly [MarketSection, string])[] = [
  ['markets', '大盤'], ['world', '全球'], ['themes', '題材'],
];

/** The 市場 section a market-area route belongs to. */
export function marketSectionOf(route: Route): MarketSection | null {
  if (route.page === 'markets') return 'markets';
  return route.page === 'world' || route.page === 'themes' ? route.page : null;
}

/** Remembers each section's last route (market, view, theme market and map) so a section reopens as it was left. */
export function rememberMarketSection(memory: MarketSectionRoutes, route: Route): MarketSectionRoutes {
  if (route.page === 'markets') return { ...memory, [marketSectionOf(route)!]: route };
  if (route.page === 'world') return { ...memory, world: route };
  // A theme detail is not reopened; the section keeps its market and list/map choice.
  if (route.page === 'themes') return { ...memory, themes: { page: 'themes', ...(route.market ? { market: route.market } : {}), ...(route.v ? { v: route.v } : {}) } };
  return memory;
}

export const INITIAL_MARKET_SECTIONS: MarketSectionRoutes = {
  markets: { page: 'markets', market: 'tw' },
  world: { page: 'world' },
  themes: { page: 'themes' },
};

/** 市場 sections: one tap to each market area; every page keeps its own selectors unchanged. */
export function MarketSectionNav({ active, routes, navigate }: {
  active: MarketSection; routes: MarketSectionRoutes; navigate: PageProps['navigate'];
}) {
  return <nav className="market-section-nav" aria-label="市場分頁">
    {SECTIONS.map(([section, label]) => (
      <a key={section} href={serializeRoute(routes[section])} aria-current={active === section ? 'page' : undefined}
        className={active === section ? 'active' : ''} onClick={event => {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          navigate(routes[section]);
        }}>{label}</a>
    ))}
  </nav>;
}
