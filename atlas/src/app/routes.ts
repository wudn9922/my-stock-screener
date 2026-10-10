import { MARKET_KEYS, type MarketKey } from '../report/schema';
import { parseLaunchTimeframe } from './LaunchParams';
import type { Timeframe } from '../market-data/MarketDataProvider';

/**
 * Site routes live in the query string so the static GitHub Pages deployment needs no rewrites:
 * `?page=markets&market=tw`, `?page=world`, `?page=themes&theme=ai-chips`, `?page=screener&group=tw_g1`,
 * `?page=chart&symbol=2330.TW&tf=1D`. Legacy report links (`?symbol=…&tf=…`) open the chart.
 * LINE LIFF wraps the original path and query in `liff.state`, which is unwrapped first.
 */
export const PAGES = ['markets', 'world', 'themes', 'screener', 'chart'] as const;
export type Page = (typeof PAGES)[number];

export type Route =
  | { page: 'markets'; market: MarketKey }
  | { page: 'world' }
  | { page: 'themes'; theme?: string }
  | { page: 'screener'; group?: string }
  | { page: 'chart'; symbol?: string; tf?: Timeframe };

export const DEFAULT_MARKET: MarketKey = 'tw';
const GROUP_KEY = /^[A-Za-z0-9_.:-]{1,80}$/;
const THEME_KEY = /^[a-z0-9-]{1,40}$/;
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
};

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

function pageFromPath(path: string): Page | undefined {
  const segments = path.split('/').filter(Boolean).map((segment) => segment.toLowerCase());
  for (let i = segments.length - 1; i >= 0; i--) {
    const page = pageAliases[segments[i]!.replace(/\.html?$/, '')];
    if (page) return page;
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
  const page: Page =
    (pageParam ? pageAliases[pageParam] : undefined) ??
    pageFromPath(path) ??
    (symbol ? 'chart' : fallback);
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
    route = group && GROUP_KEY.test(group) ? { page, group } : { page };
  } else if (page === 'chart') {
    const tf = parseLaunchTimeframe(params.get('tf') ?? params.get('timeframe'));
    route = { page, ...(symbol ? { symbol } : {}), ...(tf ? { tf } : {}) };
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
  if (route.page === 'themes' && route.theme) params.set('theme', route.theme);
  if (route.page === 'screener' && route.group) params.set('group', route.group);
  if (route.page === 'chart') {
    if (route.symbol) params.set('symbol', route.symbol);
    if (route.tf) params.set('tf', route.tf);
  }
  // `^TWII` reads better unescaped and is valid in a query string.
  return `?${params.toString().replace(/%5E/gi, '^')}`;
}

export function sameRoute(a: Route, b: Route): boolean {
  return serializeRoute(a) === serializeRoute(b);
}
