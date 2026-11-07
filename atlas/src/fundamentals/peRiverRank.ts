import { z } from 'zod';

/**
 * Where each stock's current P/E sits on its own P/E river (本益比河流圖), precomputed for the screener by
 * scripts/build-pe-river.ts into `${BASE_URL}valuation/pe-river-{tw,us}.json`.
 *
 * items[symbol] = [percentile 0..100 (one decimal), short-history flag]. A stock is absent when its river
 * cannot be drawn or it has no current P/E (loss, EPS close to zero, no recent EPS); the filter excludes it.
 */
export const PE_RIVER_FILE_VERSION = 1;
export type PeRiverMarket = 'TW' | 'US';
export type PeRiverRow = [number, 0 | 1];
export interface PeRiverFile {
  version: typeof PE_RIVER_FILE_VERSION;
  market: PeRiverMarket;
  generatedAt: string;
  /** Latest archived session behind the weekly bars. */
  priceThrough: string | null;
  basis: string;
  items: Record<string, PeRiverRow>;
}

const rowSchema = z.tuple([z.number().min(0).max(100), z.union([z.literal(0), z.literal(1)])]);
const fileSchema = z.object({
  version: z.literal(PE_RIVER_FILE_VERSION),
  market: z.enum(['TW', 'US']),
  generatedAt: z.string(),
  priceThrough: z.string().nullable(),
  basis: z.string(),
  items: z.record(z.string(), z.unknown()),
});

/** Invalid rows are dropped one by one; an invalid file is rejected as a whole. */
export function parsePeRiverFile(raw: unknown, market: PeRiverMarket): PeRiverFile | null {
  const parsed = fileSchema.safeParse(raw);
  if (!parsed.success || parsed.data.market !== market) return null;
  const items: Record<string, PeRiverRow> = {};
  for (const [symbol, value] of Object.entries(parsed.data.items)) {
    const row = rowSchema.safeParse(value);
    if (row.success) items[symbol] = row.data as PeRiverRow;
  }
  return { ...parsed.data, version: PE_RIVER_FILE_VERSION, items };
}

const RELOAD_AFTER_MS = 30 * 60 * 1000;
const loads = new Map<string, { at: number; promise: Promise<PeRiverFile | null> }>();

/** One file per market, reused for 30 minutes. A missing or invalid file resolves to null (filter unavailable). */
export function loadPeRiverFile(
  market: PeRiverMarket,
  base = `${import.meta.env?.BASE_URL ?? '/'}valuation/`,
  fetcher: typeof fetch = (input, init) => fetch(input, init),
  now = Date.now(),
): Promise<PeRiverFile | null> {
  const url = `${base.endsWith('/') ? base : `${base}/`}pe-river-${market.toLowerCase()}.json`;
  const cached = loads.get(url);
  if (cached && now - cached.at < RELOAD_AFTER_MS) return cached.promise;
  const promise = (async () => {
    try {
      const response = await fetcher(url, { cache: 'no-cache' });
      if (!response.ok) return null;
      return parsePeRiverFile(await response.json(), market);
    } catch {
      return null;
    }
  })();
  loads.set(url, { at: now, promise });
  void promise.then((file) => { if (!file) loads.delete(url); });
  return promise;
}

/** Percentile of a symbol, or null when the river or a current P/E is unavailable. */
export const peRiverPercentile = (file: PeRiverFile | null | undefined, symbol: string): number | null =>
  file?.items[symbol]?.[0] ?? null;
