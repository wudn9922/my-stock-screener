/**
 * The stock list a chart was opened from (screener results, a watchlist, a theme…), so the chart can step to the
 * previous/next stock in the order the list showed. It lives only in memory for this page load: the history entry
 * carries an opaque key, the URL stays `page=chart&symbol=…`, and a reload or a shared link has no context.
 */
export interface ChartListItem {
  symbol: string;
  name?: string;
  /** The same per-item periods the list passes to openChart today (metadata only). */
  maList?: readonly number[];
}

export interface ChartListContext {
  /** Display name of the list, e.g. the screener group or watchlist name. */
  name: string;
  /** Full ordered list as displayed (after filtering and sorting), not only the rendered rows. */
  items: readonly ChartListItem[];
}

export interface ListPosition {
  /** Zero-based index of the symbol in the list. */
  index: number;
  total: number;
  previous: ChartListItem | null;
  next: ChartListItem | null;
}

/** One list context from displayed rows: blank symbols dropped, the first occurrence of a symbol kept. */
export function chartList(
  name: string,
  rows: readonly { symbol?: string | null; name?: string; maList?: readonly number[] }[],
): ChartListContext {
  const seen = new Set<string>();
  const items: ChartListItem[] = [];
  for (const row of rows) {
    const symbol = row.symbol?.trim();
    if (!symbol || seen.has(symbol)) continue;
    seen.add(symbol);
    items.push({ symbol, ...(row.name && row.name !== symbol ? { name: row.name } : {}), ...(row.maList ? { maList: row.maList } : {}) });
  }
  return { name: name.trim() || '清單', items };
}

/** Where `symbol` sits in the list; null when it is not in it (e.g. the chart switched to another stock). */
export function listPosition(context: ChartListContext | null | undefined, symbol: string | undefined): ListPosition | null {
  if (!context || !symbol) return null;
  const wanted = symbol.toUpperCase();
  const index = context.items.findIndex((item) => item.symbol.toUpperCase() === wanted);
  if (index < 0) return null;
  return {
    index,
    total: context.items.length,
    previous: index > 0 ? context.items[index - 1]! : null,
    next: index < context.items.length - 1 ? context.items[index + 1]! : null,
  };
}

/** History-state field that names a remembered list; anything else in history.state is ignored. */
export const LIST_STATE_KEY = 'atlasChartList';
const MAX_REMEMBERED = 30;
const remembered = new Map<string, ChartListContext>();
let sequence = 0;

/** Keeps a context for this page load and returns the history state that refers to it. */
export function rememberChartList(context: ChartListContext): Record<string, string> {
  const key = `list-${++sequence}`;
  remembered.set(key, context);
  while (remembered.size > MAX_REMEMBERED) remembered.delete(remembered.keys().next().value!);
  return { [LIST_STATE_KEY]: key };
}

/** The context a history state refers to, if it was remembered in this page load. */
export function chartListFor(state: unknown): ChartListContext | null {
  if (!state || typeof state !== 'object') return null;
  const key = (state as Record<string, unknown>)[LIST_STATE_KEY];
  return typeof key === 'string' ? (remembered.get(key) ?? null) : null;
}

/** Only the list reference is carried to the next chart/financials entry. */
export function listState(state: unknown): Record<string, string> | null {
  if (!state || typeof state !== 'object') return null;
  const key = (state as Record<string, unknown>)[LIST_STATE_KEY];
  return typeof key === 'string' && remembered.has(key) ? { [LIST_STATE_KEY]: key } : null;
}

/** Test helper: forget every remembered list, as a reload would. */
export function forgetChartLists() {
  remembered.clear();
}
