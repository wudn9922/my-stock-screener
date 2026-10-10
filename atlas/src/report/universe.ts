import { reportUrl } from './loadReport';
import { METRIC_MAS, parseMetrics, toFinite, type GroupItem, type ReportGroup } from './schema';

/**
 * Full-market screener universe (`report/universe.json`, written by main.py next to latest.json).
 * Rows are column arrays named by `fields`; each becomes a report `GroupItem` (MA20/50/200 values
 * derived back from the distances) so rows, sorting and filters share one shape. Loaded only when
 * a 全市場篩選 group is opened. Like the report schema it is tolerant: unknown fields are ignored and
 * a bad row is dropped; a document without any usable market is "invalid".
 */
export type UniverseMarketKey = 'TW' | 'US';
export const UNIVERSE_MARKETS: readonly UniverseMarketKey[] = ['TW', 'US'];

/** Column order of the contract; used when a file omits `fields`. */
export const UNIVERSE_FIELDS = [
  'symbol',
  'name',
  'close',
  'changePct',
  'volume',
  'r5',
  'r21',
  'r63',
  'ma20',
  'ma50',
  'ma200',
  'ma20Slope5',
  'trend',
  'hi52',
  'fromHi52',
  'hiBars',
  'rs21',
  'rs63',
  'rsRank',
  'volRatio',
  'turnover20',
] as const;

/** Route group keys of the two full-market lists (report group keys use underscores, never this form). */
export const UNIVERSE_GROUP_KEYS: Record<UniverseMarketKey, string> = { TW: 'universe-tw', US: 'universe-us' };
export const UNIVERSE_LABELS: Record<UniverseMarketKey, string> = { TW: '台股全市場篩選', US: '美股全市場篩選' };

export function universeMarketOf(groupKey: string | undefined): UniverseMarketKey | null {
  return groupKey === UNIVERSE_GROUP_KEYS.TW ? 'TW' : groupKey === UNIVERSE_GROUP_KEYS.US ? 'US' : null;
}

export interface UniverseMarket {
  market: UniverseMarketKey;
  benchmark: string;
  asOf: string | null;
  /** Human description of the list, e.g. 「S&P 500」. */
  universe: string;
  items: GroupItem[];
}

export interface Universe {
  generatedAt: string | null;
  reportDate: string | null;
  markets: Partial<Record<UniverseMarketKey, UniverseMarket>>;
}

export type UniverseLoadResult =
  | { status: 'ok'; universe: Universe; url: string }
  | { status: 'missing' | 'invalid' | 'error'; message: string; url: string };

const MISSING = '全市場篩選資料尚未產生（每日報告更新後提供）。';
const INVALID = '全市場篩選資料格式無法辨識。';

const textOrNull = (value: unknown) => (typeof value === 'string' && value.trim() ? value.trim() : null);
const SYMBOL = /^[A-Z0-9^][A-Z0-9.^=-]{0,31}$/;

/** `report/universe.json`, a sibling of latest.json. */
export function universeUrl(report = reportUrl()): string {
  return new URL('universe.json', report).href;
}

/** One column-array row → a GroupItem; null when the symbol is unusable. */
export function universeRowToItem(
  row: unknown,
  index: Readonly<Record<string, number>>,
  context: { benchmark: string; asOf: string | null },
): GroupItem | null {
  if (!Array.isArray(row)) return null;
  const get = (field: string) => (field in index ? row[index[field]!] : undefined);
  const symbolText = typeof get('symbol') === 'string' ? (get('symbol') as string).trim().toUpperCase() : '';
  if (!SYMBOL.test(symbolText)) return null;
  const metrics = parseMetrics({
    r5: get('r5'),
    r21: get('r21'),
    r63: get('r63'),
    ma: { 20: get('ma20'), 50: get('ma50'), 200: get('ma200') },
    ma20Slope5: get('ma20Slope5'),
    trend: get('trend'),
    hi52: get('hi52'),
    fromHi52: get('fromHi52'),
    hiBars: get('hiBars'),
    // The universe has no `bars` column; the window length is the best lower bound.
    bars: get('hiBars'),
    rs21: get('rs21'),
    rs63: get('rs63'),
    rsBench: context.benchmark,
    rsRank: get('rsRank'),
    volRatio: get('volRatio'),
    turnover20: get('turnover20'),
  })!;
  const numeric = (field: string) => toFinite(get(field));
  const close = numeric('close');
  const maValues: Record<string, number> = {};
  for (const period of METRIC_MAS) {
    const distance = metrics.ma[`${period}`];
    if (close !== null && close > 0 && distance !== null && distance > -100)
      maValues[String(period)] = close / (1 + distance / 100);
  }
  const name = typeof get('name') === 'string' ? (get('name') as string).trim() : '';
  return {
    symbol: symbolText,
    name: name || symbolText,
    maList: [...METRIC_MAS],
    close,
    changePct: numeric('changePct'),
    maValues,
    note: null,
    volume: numeric('volume'),
    asOf: context.asOf,
    metrics,
  };
}

/** Parses an already-fetched universe body. Never throws. */
export function parseUniverse(data: unknown, url = ''): UniverseLoadResult {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return { status: 'invalid', message: INVALID, url };
  const document = data as Record<string, unknown>;
  const fields =
    Array.isArray(document.fields) && document.fields.every((field) => typeof field === 'string')
      ? (document.fields as string[])
      : [...UNIVERSE_FIELDS];
  const index: Record<string, number> = {};
  fields.forEach((field, position) => {
    if (!(field in index)) index[field] = position;
  });
  if (!('symbol' in index)) return { status: 'invalid', message: INVALID, url };
  const marketsRaw =
    document.markets && typeof document.markets === 'object' && !Array.isArray(document.markets)
      ? (document.markets as Record<string, unknown>)
      : null;
  if (!marketsRaw) return { status: 'invalid', message: INVALID, url };
  const markets: Universe['markets'] = {};
  for (const market of UNIVERSE_MARKETS) {
    const raw = marketsRaw[market];
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) continue;
    const entry = raw as Record<string, unknown>;
    const benchmark = textOrNull(entry.benchmark) ?? (market === 'US' ? 'SPY' : '^TWII');
    const asOf = textOrNull(entry.asOf);
    const items: GroupItem[] = [];
    const seen = new Set<string>();
    for (const row of Array.isArray(entry.rows) ? entry.rows : []) {
      const item = universeRowToItem(row, index, { benchmark, asOf });
      if (item && !seen.has(item.symbol)) {
        seen.add(item.symbol);
        items.push(item);
      }
    }
    if (!items.length) continue;
    markets[market] = {
      market,
      benchmark,
      asOf,
      universe: textOrNull(entry.universe) ?? (market === 'US' ? 'S&P 500' : '台股'),
      items,
    };
  }
  if (!Object.keys(markets).length) return { status: 'missing', message: MISSING, url };
  return {
    status: 'ok',
    universe: { generatedAt: textOrNull(document.generatedAt), reportDate: textOrNull(document.reportDate), markets },
    url,
  };
}

/** Fetches and validates the universe. Network, HTTP and JSON failures resolve to a friendly state. */
export async function fetchUniverse(
  url = universeUrl(),
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<UniverseLoadResult> {
  let response: Response;
  try {
    response = await fetcher(url, { cache: 'no-cache', signal });
  } catch {
    return { status: 'error', message: '無法連線取得全市場資料，請稍後再試。', url };
  }
  if (response.status === 404) return { status: 'missing', message: MISSING, url };
  if (!response.ok) return { status: 'error', message: `全市場資料暫時無法取得（HTTP ${response.status}）。`, url };
  let data: unknown;
  try {
    // An SPA fallback can answer with index.html; treat non-JSON as "not built yet".
    data = JSON.parse(await response.text());
  } catch {
    return { status: 'invalid', message: INVALID, url };
  }
  return parseUniverse(data, url);
}

let shared: Promise<UniverseLoadResult> | null = null;

/** One shared load per page session; a failed load is retried on the next call. */
export function loadUniverse(refresh = false): Promise<UniverseLoadResult> {
  if (!shared || refresh) {
    const promise = fetchUniverse();
    shared = promise;
    void promise.then((result) => {
      if (result.status !== 'ok' && shared === promise) shared = null;
    });
  }
  return shared;
}

/** A universe market as a screener group (same shape as the report's groups). */
export function universeGroup(market: UniverseMarket): ReportGroup {
  return {
    key: UNIVERSE_GROUP_KEYS[market.market],
    name: UNIVERSE_LABELS[market.market],
    market: market.market,
    kind: 'scan',
    maList: [...METRIC_MAS],
    items: market.items,
  };
}
