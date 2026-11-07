import { defaultRoute, sameRoute, type Page, type Route } from './routes';
import { INITIAL_MARKET_SECTIONS, marketSectionOf, rememberMarketSection, type MarketSectionRoutes } from '../ui/MarketSectionNav';

/** Main pages that stay mounted (hidden) after their first visit, like the chart page. */
export const KEPT_PAGES = ['watchlist', 'markets', 'world', 'themes', 'screener', 'console'] as const;
export type KeptPage = (typeof KEPT_PAGES)[number];
export const isKeptPage = (page: Page): page is KeptPage => (KEPT_PAGES as readonly Page[]).includes(page);

/** Bottom-bar destinations. */
export type NavPage = 'watchlist' | 'markets' | 'screener' | 'chart' | 'console';
/** Existing detail pages light their parent primary section. */
export const navPageOf = (page: Page): NavPage =>
  page === 'financials' ? 'chart' : page === 'world' || page === 'themes' ? 'markets' : page;

export interface RouteMemory {
  /** The route each kept page showed last (what a nav tap reopens). */
  pages: Partial<Record<KeptPage, Route>>;
  /** 市場 sections as each was left; the section links use these. */
  sections: MarketSectionRoutes;
  /** The 市場 section the bottom 「市場」 reopens (a theme detail is not reopened). */
  lastMarket: Route;
}

export const INITIAL_ROUTE_MEMORY: RouteMemory = {
  pages: {},
  sections: INITIAL_MARKET_SECTIONS,
  lastMarket: INITIAL_MARKET_SECTIONS.markets,
};

/** Records the current route. Returns the same object when nothing changed (safe to call on every render). */
export function rememberRoute(memory: RouteMemory, route: Route): RouteMemory {
  if (!isKeptPage(route.page)) return memory;
  const page = route.page;
  const previous = memory.pages[page];
  const section = marketSectionOf(route);
  const sections = section ? rememberMarketSection(memory.sections, route) : memory.sections;
  const lastMarket = section === 'themes' ? sections.themes : section ? route : memory.lastMarket;
  if (previous && sameRoute(previous, route) && sameRoute(memory.lastMarket, lastMarket)
    && (sections === memory.sections || sameSections(sections, memory.sections))) return memory;
  return { pages: { ...memory.pages, [page]: route }, sections, lastMarket };
}

function sameSections(a: MarketSectionRoutes, b: MarketSectionRoutes) {
  return sameRoute(a.markets, b.markets) && sameRoute(a.world, b.world) && sameRoute(a.themes, b.themes);
}

/** The default route of a bottom-bar destination. */
export function navDefault(nav: Exclude<NavPage, 'chart'>): Route {
  return nav === 'markets' ? INITIAL_MARKET_SECTIONS.markets : defaultRoute(nav);
}

/** Where a nav tap from another page goes: the destination as it was left, or its default. */
export function navDestination(memory: RouteMemory, nav: Exclude<NavPage, 'chart'>): Route {
  if (nav === 'markets') return memory.lastMarket;
  return memory.pages[nav] ?? navDefault(nav);
}

/**
 * Whether the current route differs from its destination's default (filters, a group, an open list, another
 * 市場 section or country). Scroll position is judged separately by the page host.
 */
export function routeIsDefault(route: Route): boolean {
  const nav = navPageOf(route.page);
  return nav !== 'chart' && sameRoute(route, navDefault(nav));
}

/** Forgets the temporary view of one destination (all three 市場 sections for 市場); saved data is untouched. */
export function forgetDestination(memory: RouteMemory, nav: Exclude<NavPage, 'chart'>): RouteMemory {
  const forgotten = keptPagesOf(nav);
  const pages = Object.fromEntries(Object.entries(memory.pages).filter(([page]) => !forgotten.includes(page as KeptPage)));
  return nav === 'markets'
    ? { pages, sections: INITIAL_MARKET_SECTIONS, lastMarket: INITIAL_MARKET_SECTIONS.markets }
    : { ...memory, pages };
}

/**
 * The part of a route that changes what a page shows; a change of it inside the same page starts at the top
 * (another market, group, world view, themes market or chart symbol), as before.
 */
export function scrollSection(route: Route): string {
  switch (route.page) {
    case 'markets': return route.market;
    case 'screener': return route.group ?? '';
    case 'world': return route.view ?? '';
    case 'themes': return route.market ?? '';
    case 'chart': return route.symbol ?? '';
    default: return '';
  }
}

/** A saved page scroll is put back only onto the same view (also the same watchlist), never another one. */
export const scrollIdentity = (route: Route): string =>
  `${route.page}:${scrollSection(route)}${route.page === 'watchlist' ? `:${route.list ?? ''}` : ''}`;

/** The kept pages one destination covers. */
export const keptPagesOf = (nav: Exclude<NavPage, 'chart'>): readonly KeptPage[] =>
  nav === 'markets' ? ['markets', 'world', 'themes'] : [nav];
