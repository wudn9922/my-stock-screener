import { z } from 'zod';
import { reportUrl } from './loadReport';

/**
 * Recommended moving averages (`report/ma-selector/<symbol>.json`), written monthly by the my-stock-screener
 * workflow `ma-recommendations.yml`, which runs ma-selector's `scripts/build_recommendations.py`. The selection rules
 * live in the ma-selector repository (production SMA selector); Atlas only reads and displays the result.
 */

/** The Streamlit app with the full candidate tables, charts and backtests. */
export const MA_SELECTOR_URL: string = import.meta.env?.VITE_MA_SELECTOR_URL || 'https://ma-selector-hfz29vjvvcgnnvatmo5ac3.streamlit.app/';
export const maSelectorLink = (symbol: string, base = MA_SELECTOR_URL): string => {
  const url = new URL(base);
  url.searchParams.set('ticker', symbol);
  return url.href;
};

export const MA_RANGE_KEYS = ['short', 'medium', 'long'] as const;
export type MaRangeKey = (typeof MA_RANGE_KEYS)[number];
export const MA_RANGE_LABELS: Record<MaRangeKey, string> = { short: '短期', medium: '中期', long: '長期' };

const finiteOrNull = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null);
const period = z.number().int().min(1).max(1000);
const candidateSchema = z.object({
  period,
  score: z.unknown().optional().transform(finiteOrNull),
  sarReturn: z.unknown().optional().transform(finiteOrNull),
  sarDrawdown: z.unknown().optional().transform(finiteOrNull),
  sarWinRate: z.unknown().optional().transform(finiteOrNull),
  sarTrades: z.unknown().optional().transform(finiteOrNull),
});
const rangeSchema = z.object({
  key: z.enum(MA_RANGE_KEYS),
  min: period,
  max: period,
  windowYears: z.unknown().optional().transform(finiteOrNull),
  final: period,
  candidates: z.array(candidateSchema).catch([]),
});
export const maRecommendationSchema = z.object({
  ticker: z.string(),
  kind: z.literal('SMA'),
  asof: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  bars: z.unknown().optional().transform(finiteOrNull),
  limited: z.unknown().optional().transform((value) => value === true),
  referenceAsof: z.unknown().optional().transform((value) => (typeof value === 'string' ? value : null)),
  suitability: z
    .unknown()
    .optional()
    .transform((value) => {
      const record = value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
      return { short: finiteOrNull(record.short), long: finiteOrNull(record.long) };
    }),
  ranges: z
    .array(rangeSchema)
    .refine((ranges) => MA_RANGE_KEYS.every((key) => ranges.some((range) => range.key === key)), '短中長期都要有'),
});
export type MaRecommendation = z.infer<typeof maRecommendationSchema>;
export type MaRecommendationRange = MaRecommendation['ranges'][number];

export type MaRecommendationLoadResult =
  | { status: 'ok'; data: MaRecommendation; url: string }
  | { status: 'missing' | 'invalid' | 'error'; message: string; url: string };

/** `ma-selector/<symbol>.json` sits next to `latest.json` (or next to `VITE_REPORT_URL`). */
export function maRecommendationUrl(
  symbol: string,
  base: string = import.meta.env?.BASE_URL ?? '/',
  override: string | undefined = import.meta.env?.VITE_REPORT_URL,
  origin: string = typeof location === 'undefined' ? 'http://localhost' : location.origin,
): string {
  return new URL(`ma-selector/${encodeURIComponent(symbol)}.json`, reportUrl(base, override, origin)).href;
}

const MISSING = '這檔股票還沒有建議均線（每月更新；只涵蓋報告的台股與 S&P 500）。';

/** Parses an already-fetched body. Never throws. */
export function parseMaRecommendation(data: unknown, symbol: string, url = ''): MaRecommendationLoadResult {
  const parsed = maRecommendationSchema.safeParse(data);
  if (!parsed.success) return { status: 'invalid', message: '建議均線資料格式無法辨識。', url };
  if (parsed.data.ticker.toUpperCase() !== symbol.toUpperCase()) return { status: 'invalid', message: '建議均線資料與股票代號不符。', url };
  return { status: 'ok', data: parsed.data, url };
}

/** Fetches and validates one symbol's file. Network, HTTP and JSON failures resolve to a friendly state. */
export async function fetchMaRecommendation(
  symbol: string,
  url = maRecommendationUrl(symbol),
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<MaRecommendationLoadResult> {
  let response: Response;
  try {
    response = await fetcher(url, { cache: 'no-cache', signal });
  } catch {
    return { status: 'error', message: '無法連線取得建議均線，請稍後再試。', url };
  }
  if (response.status === 404) return { status: 'missing', message: MISSING, url };
  if (!response.ok) return { status: 'error', message: `建議均線暫時無法取得（HTTP ${response.status}）。`, url };
  let data: unknown;
  try {
    // An SPA fallback can answer with index.html; a non-JSON body means the file does not exist here.
    data = JSON.parse(await response.text());
  } catch {
    return { status: 'missing', message: MISSING, url };
  }
  return parseMaRecommendation(data, symbol, url);
}

/** Final pick of each range, short → long (always three entries for a valid file). */
export const recommendedPeriods = (data: MaRecommendation): number[] =>
  MA_RANGE_KEYS.map((key) => data.ranges.find((range) => range.key === key)!.final);
