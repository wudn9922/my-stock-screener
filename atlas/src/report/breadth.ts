import { z } from 'zod';
import { reportUrl } from './loadReport';

/**
 * Market breadth (`report/breadth.json`, written by scripts/build_breadth.py from the local daily bars).
 *
 * Tolerant like schema.ts: unknown fields are ignored, numbers may arrive as strings, a history point
 * with a bad date is dropped, and a market without its `all` segment is left out instead of failing
 * the whole file.
 */

export const BREADTH_MARKET_KEYS = ['tw', 'us'] as const;
export type BreadthMarketKey = (typeof BREADTH_MARKET_KEYS)[number];

const finiteOrNull = (value: unknown): number | null => {
  const number = typeof value === 'string' && value.trim() !== '' ? Number(value) : value;
  return typeof number === 'number' && Number.isFinite(number) ? number : null;
};
const optionalText = z
  .unknown()
  .optional()
  .transform((value) => (typeof value === 'string' && value.trim() ? value.trim() : null));
const text = z
  .unknown()
  .optional()
  .transform((value) => (typeof value === 'string' ? value.trim() : ''));
const countOf = z
  .unknown()
  .optional()
  .transform((value) => Math.max(0, Math.floor(finiteOrNull(value) ?? 0)));
const pctOf = z
  .unknown()
  .optional()
  .transform((value) => finiteOrNull(value));

const ratioSchema = z
  .object({ above: countOf, total: countOf, pct: pctOf })
  .optional()
  .transform((value): BreadthRatio => value ?? { above: 0, total: 0, pct: null });

const segmentSchema = z.object({
  count: countOf,
  ma20: ratioSchema,
  ma60: ratioSchema,
});

const historySchema = z
  .object({
    dates: z.unknown().optional(),
    ma20Pct: z.unknown().optional(),
    ma60Pct: z.unknown().optional(),
  })
  .optional()
  .transform((value): BreadthHistory => {
    const raw: { dates?: unknown; ma20Pct?: unknown; ma60Pct?: unknown } = value ?? {};
    const dates = Array.isArray(raw.dates) ? raw.dates : [];
    const ma20 = Array.isArray(raw.ma20Pct) ? raw.ma20Pct : [];
    const ma60 = Array.isArray(raw.ma60Pct) ? raw.ma60Pct : [];
    const out: BreadthHistory = { dates: [], ma20Pct: [], ma60Pct: [] };
    dates.forEach((day, index) => {
      if (typeof day !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return;
      out.dates.push(day);
      out.ma20Pct.push(finiteOrNull(ma20[index]));
      out.ma60Pct.push(finiteOrNull(ma60[index]));
    });
    return out;
  });

const marketSchema = z
  .object({
    asOf: optionalText,
    universe: text,
    segments: z.object({
      all: segmentSchema,
      twse: segmentSchema.optional(),
      tpex: segmentSchema.optional(),
    }),
    history: historySchema,
  })
  .transform((market) => ({
    asOf: market.asOf,
    universe: market.universe,
    segments: {
      all: market.segments.all,
      twse: market.segments.twse ?? null,
      tpex: market.segments.tpex ?? null,
    },
    history: market.history,
  }));

export const breadthSchema = z.object({
  generatedAt: optionalText,
  markets: z
    .unknown()
    .optional()
    .transform((value) => {
      const record = value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
      const out: Partial<Record<BreadthMarketKey, BreadthMarket>> = {};
      for (const key of BREADTH_MARKET_KEYS) {
        const parsed = marketSchema.safeParse(record[key]);
        if (parsed.success) out[key] = parsed.data;
      }
      return out;
    }),
});

export interface BreadthRatio {
  above: number;
  total: number;
  /** Percent of `total` above the average, one decimal; null when nothing was evaluable. */
  pct: number | null;
}
export interface BreadthSegment {
  /** Stocks with a bar on `asOf` and volume above zero. */
  count: number;
  ma20: BreadthRatio;
  ma60: BreadthRatio;
}
export interface BreadthHistory {
  dates: string[];
  ma20Pct: (number | null)[];
  ma60Pct: (number | null)[];
}
export interface BreadthMarket {
  asOf: string | null;
  universe: string;
  segments: { all: BreadthSegment; twse: BreadthSegment | null; tpex: BreadthSegment | null };
  history: BreadthHistory;
}
export interface Breadth {
  generatedAt: string | null;
  markets: Partial<Record<BreadthMarketKey, BreadthMarket>>;
}

export type BreadthLoadResult =
  | { status: 'ok'; data: Breadth; url: string }
  | { status: 'missing' | 'invalid' | 'error'; message: string; url: string };

/** `breadth.json` sits next to `latest.json` (or next to `VITE_REPORT_URL`). */
export function breadthUrl(
  base: string = import.meta.env?.BASE_URL ?? '/',
  override: string | undefined = import.meta.env?.VITE_REPORT_URL,
  origin: string = typeof location === 'undefined' ? 'http://localhost' : location.origin,
): string {
  return new URL('breadth.json', reportUrl(base, override, origin)).href;
}

/** Parses an already-fetched body. Never throws. */
export function parseBreadth(data: unknown, url = ''): BreadthLoadResult {
  if (!data || typeof data !== 'object' || Array.isArray(data))
    return { status: 'invalid', message: '市場寬度格式無法辨識。', url };
  const parsed = breadthSchema.safeParse(data);
  if (!parsed.success) return { status: 'invalid', message: '市場寬度格式無法辨識。', url };
  if (!Object.keys(parsed.data.markets).length)
    return { status: 'missing', message: '市場寬度資料尚未產生。', url };
  return { status: 'ok', data: parsed.data, url };
}

/** Fetches and validates the file. Network, HTTP and JSON failures resolve to a friendly state. */
export async function fetchBreadth(
  url = breadthUrl(),
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<BreadthLoadResult> {
  let response: Response;
  try {
    response = await fetcher(url, { cache: 'no-cache', signal });
  } catch {
    return { status: 'error', message: '無法連線取得市場寬度，請稍後再試。', url };
  }
  if (response.status === 404) return { status: 'missing', message: '市場寬度資料尚未產生。', url };
  if (!response.ok)
    return { status: 'error', message: `市場寬度暫時無法取得（HTTP ${response.status}）。`, url };
  let data: unknown;
  try {
    // An SPA fallback can answer with index.html; treat non-JSON as an unreadable file.
    data = JSON.parse(await response.text());
  } catch {
    return { status: 'invalid', message: '市場寬度格式無法辨識。', url };
  }
  return parseBreadth(data, url);
}

let shared: Promise<BreadthLoadResult> | null = null;

/** One shared load per page session; `refresh` forces a new request. */
export function loadBreadth(refresh = false): Promise<BreadthLoadResult> {
  if (!shared || refresh) shared = fetchBreadth();
  return shared;
}

export type BreadthTone = 'weak' | 'hot' | 'neutral' | 'none';

/** Below 20% is oversold-weak, above 80% is overheated; the boundaries themselves are neutral. */
export function breadthTone(pct: number | null): BreadthTone {
  if (pct === null || !Number.isFinite(pct)) return 'none';
  if (pct < 20) return 'weak';
  if (pct > 80) return 'hot';
  return 'neutral';
}

const round1 = (value: number) => Math.round(value * 10) / 10;

/**
 * SVG path for a 0–100 series over a `width` × `height` box (y grows downward).
 * A null value lifts the pen, so the line breaks instead of dropping to zero.
 */
export function linePath(values: readonly (number | null)[], width = 300, height = 100): string {
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  const parts: string[] = [];
  let pen: 'M' | 'L' = 'M';
  values.forEach((value, index) => {
    if (value === null || !Number.isFinite(value)) {
      pen = 'M';
      return;
    }
    const clamped = Math.min(100, Math.max(0, value));
    parts.push(`${pen}${round1(index * step)},${round1(height - (clamped / 100) * height)}`);
    pen = 'L';
  });
  return parts.join(' ');
}
