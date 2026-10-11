import { MARKET_KEYS, type MarketKey } from '../report/schema';
import { parseLaunchTimeframe } from './LaunchParams';
import type { Timeframe } from '../market-data/MarketDataProvider';

/**
 * Site routes live in the query string so the static GitHub Pages deployment needs no rewrites:
 * `?page=markets&market=tw`, `?page=world`, `?page=world&view=rates` (aliases `?page=rates`, `?page=bonds`),
 * `?page=themes&theme=ai-chips`, `?page=screener&group=tw_g1`, `?page=chart&symbol=2330.TW&tf=1D`,
 * `?page=financials&symbol=AAPL&p=q` (not in the bottom bar; opened from the chart header). Legacy report links (`?symbol=…&tf=…`) open the chart.
 * LINE LIFF wraps the original path and query in `liff.state`, which is unwrapped first.
 */
export const PAGES = ['markets', 'world', 'themes', 'screener', 'chart', 'financials'] as const;
export type Page = (typeof PAGES)[number];

export type Route =
  | { page: 'markets'; market: MarketKey }
  /** `view` is only present for the rates sub-view; the index view is just `{ page: 'world' }`. */
  | { page: 'world'; view?: 'rates' }
  | { page: 'themes'; theme?: string }
  | { page: 'screener'; group?: string; f?: string }
  | { page: 'chart'; symbol?: string; tf?: Timeframe }
  /** `p: 'q'` shows quarterly figures; omitted means annual. */
  | { page: 'financials'; symbol?: string; p?: 'q' };

export const DEFAULT_MARKET: MarketKey = 'tw';
const GROUP_KEY = /^[A-Za-z0-9_.:-]{1,80}$/;
const THEME_KEY = /^[a-z0-9-]{1,40}$/;
/** Screener filter conditions (pages/screenerFilters.ts encoding). */
const FILTER_PARAM = /^[A-Za-z0-9:.,;_~-]{1,300}$/;
const MAX_SYMBOL = 32;
const pageAliases: Record<string, Page> = {
  markets: 'markets',
  market: 'markets',
  index: 'markets',
  world: 'world',
  themes: 'themes',
  theme: 'themes',
  screener: 'screener',
  groups: 'screener',
  chart: 'chart',
  financials: 'financials',
  financial: 'financials',
};
/** Page aliases that also select a sub-view of their page. */
const viewAliases: Record<string, { page: Page; view: 'rates' }> = {
  rates: { page: 'world', view: 'rates' },
  bonds: { page: 'world', view: 'rates' },
};

function resolveAlias(name: string | undefined): { page: Page; view?: 'rates' } | undefined {
  if (!name) return undefined;
  const withView = viewAliases[name];
  if (withView) return withView;
  const page = pageAliases[name];
  return page ? { page } : undefined;
}

export function defaultRoute(page: Page): Route {
  switch (page) {
    case 'markets':
      return { page, market: DEFAULT_MARKET };
    case 'world':
      return { page };
    case 'themes':
      return { page };
    case 'screener':
      return { page };
    case 'chart':
      return { page };
    case 'financials':
      return { page };
  }
}

/** Unwraps LINE LIFF's `liff.state` (e.g. `/atlas/?page=world` or `?symbol=2330.TW`) into params. */
export function unwrapLiffState(params: URLSearchParams): { params: URLSearchParams; path: string } {
  const state = params.get('liff.state');
  if (!state) return { params, path: '' };
  let decoded = state;
  // LIFF can double-encode the state when the endpoint URL itself carries a query.
  for (let i = 0; i < 2 && /%[0-9A-Fa-f]{2}/.test(decoded) && !decoded.includes('?'); i++) {
    try {
      decoded = decodeURIComponent(decoded);
    } catch {
      break;
    }
  }
  const hashIndex = decoded.indexOf('#');
  const withoutHash = hashIndex >= 0 ? decoded.slice(0, hashIndex) : decoded;
  const queryIndex = withoutHash.indexOf('?');
  const path = queryIndex >= 0 ? withoutHash.slice(0, queryIndex) : withoutHash;
  const inner = new URLSearchParams(queryIndex >= 0 ? withoutHash.slice(queryIndex + 1) : '');
  const merged = new URLSearchParams();
  for (const [key, value] of params) if (key !== 'liff.state' && !key.startsWith('liff.')) merged.set(key, value);
  for (const [key, value] of inner) merged.set(key, value);
  return { params: merged, path };
}

function pageFromPath(path: string): { page: Page; view?: 'rates' } | undefined {
  const segments = path.split('/').filter(Boolean).map((segment) => segment.toLowerCase());
  for (let i = segments.length - 1; i >= 0; i--) {
    const found = resolveAlias(segments[i]!.replace(/\.html?$/, ''));
    if (found) return found;
  }
  return undefined;
}

/** Raw symbol text; the chart validates and resolves it (and explains invalid input). */
function cleanSymbol(value: string | null): string | undefined {
  const symbol = value?.trim().toUpperCase();
  return symbol ? symbol.slice(0, MAX_SYMBOL * 2) : undefined;
}

export interface ParsedRoute {
  route: Route;
  /** True when the URL should be rewritten to its canonical form (liff.state, aliases, junk). */
  rewrite: boolean;
}

export function parseRoute(search: string, fallback: Page = 'markets'): ParsedRoute {
  let raw: URLSearchParams;
  try {
    raw = new URLSearchParams(search);
  } catch {
    return { route: defaultRoute(fallback), rewrite: true };
  }
  const hadLiff = raw.has('liff.state');
  const { params, path } = unwrapLiffState(raw);
  const pageParam = params.get('page')?.trim().toLowerCase();
  const symbol = cleanSymbol(params.get('symbol'));
  const resolved = resolveAlias(pageParam) ?? pageFromPath(path);
  const page: Page = resolved?.page ?? (symbol ? 'chart' : fallback);
  let route: Route;
  if (page === 'markets') {
    const market = params.get('market')?.trim().toLowerCase();
    route = {
      page,
      market: (MARKET_KEYS as readonly string[]).includes(market ?? '') ? (market as MarketKey) : DEFAULT_MARKET,
    };
  } else if (page === 'themes') {
    const theme = params.get('theme')?.trim().toLowerCase();
    route = theme && THEME_KEY.test(theme) ? { page, theme } : { page };
  } else if (page === 'screener') {
    const group = params.get('group')?.trim();
    const f = params.get('f')?.trim();
    route = { page, ...(group && GROUP_KEY.test(group) ? { group } : {}), ...(f && FILTER_PARAM.test(f) ? { f } : {}) };
  } else if (page === 'chart') {
    const tf = parseLaunchTimeframe(params.get('tf') ?? params.get('timeframe'));
    route = { page, ...(symbol ? { symbol } : {}), ...(tf ? { tf } : {}) };
  } else if (page === 'world') {
    // `view` is only kept for 'rates'; plain `?page=world` stays `{ page: 'world' }`.
    const view = resolved?.page === 'world' && resolved.view ? resolved.view : params.get('view')?.trim().toLowerCase();
    route = view === 'rates' ? { page, view } : { page };
  } else if (page === 'financials') {
    const p = params.get('p')?.trim().toLowerCase();
    route = { page, ...(symbol ? { symbol } : {}), ...(p === 'q' ? { p } : {}) };
  } else route = { page };
  const canonical = serializeRoute(route);
  const current = normalizeSearch(search);
  return { route, rewrite: hadLiff || (current !== '' && current !== '?' && canonical !== current) };
}

function normalizeSearch(search: string) {
  return search.startsWith('?') ? search : search ? `?${search}` : '';
}

export function serializeRoute(route: Route): string {
  const params = new URLSearchParams();
  params.set('page', route.page);
  if (route.page === 'markets') params.set('market', route.market);
  if (route.page === 'world' && route.view) params.set('view', route.view);
  if (route.page === 'themes' && route.theme) params.set('theme', route.theme);
  if (route.page === 'screener' && route.group) params.set('group', route.group);
  if (route.page === 'screener' && route.f) params.set('f', route.f);
  if (route.page === 'chart') {
    if (route.symbol) params.set('symbol', route.symbol);
    if (route.tf) params.set('tf', route.tf);
  }
  if (route.page === 'financials') {
    if (route.symbol) params.set('symbol', route.symbol);
    if (route.p) params.set('p', route.p);
  }
  // `^TWII` reads better unescaped and is valid in a query string.
  return `?${params.toString().replace(/%5E/gi, '^')}`;
}

export function sameRoute(a: Route, b: Route): boolean {
  return serializeRoute(a) === serializeRoute(b);
}
