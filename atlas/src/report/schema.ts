import { z } from 'zod';

/**
 * Daily screener report (`report/latest.json`, written by the Python report job).
 *
 * The schema is deliberately tolerant: unknown fields are ignored, missing optional fields get
 * neutral defaults, and a malformed entry inside a list is dropped instead of rejecting the whole
 * report. Only a document that is not an object at all (or has no usable content) is invalid.
 */

export const MARKET_KEYS = ['tw', 'jp', 'kr', 'eu', 'us'] as const;
export type MarketKey = (typeof MARKET_KEYS)[number];
export const TRENDS = ['bull', 'bear', 'neutral', 'unknown'] as const;
export type Trend = (typeof TRENDS)[number];

// zod 4 treats a bare `unknown().transform()` key as required; `.optional()` keeps missing keys legal.
const text = (fallback = '') =>
  z
    .unknown()
    .optional()
    .transform((value) => (typeof value === 'string' ? value.trim() : typeof value === 'number' ? String(value) : fallback));
const optionalText = z
  .unknown()
  .optional()
  .transform((value) => (typeof value === 'string' && value.trim() ? value.trim() : null));
/** Finite number from a number or numeric string, else null. */
export const toFinite = (value: unknown): number | null => {
  const number = typeof value === 'string' && value.trim() !== '' ? Number(value) : value;
  return typeof number === 'number' && Number.isFinite(number) ? number : null;
};
const finiteOrNull = z.unknown().optional().transform(toFinite);
const maList = z.unknown().optional().transform((value) =>
  Array.isArray(value)
    ? [...new Set(value.map(Number).filter((n) => Number.isInteger(n) && n > 0 && n <= 1000))]
    : [],
);
const numberRecord = z.unknown().optional().transform((value) => {
  const out: Record<string, number> = {};
  if (value && typeof value === 'object' && !Array.isArray(value))
    for (const [key, raw] of Object.entries(value as Record<string, unknown>)) {
      const number = typeof raw === 'string' && raw.trim() !== '' ? Number(raw) : raw;
      if (typeof number === 'number' && Number.isFinite(number)) out[key] = number;
    }
  return out;
});
const trend = z.unknown().optional().transform((value): Trend => {
  const normalized = typeof value === 'string' ? value.trim().toLowerCase() : '';
  return (TRENDS as readonly string[]).includes(normalized) ? (normalized as Trend) : 'unknown';
});
const symbol = z
  .string()
  .trim()
  .min(1)
  .max(32)
  .transform((value) => value.toUpperCase());

/** Keeps the valid entries of a list; anything that is not an array becomes []. */
function tolerantArray<T extends z.ZodType>(item: T) {
  return z.unknown().optional().transform((value): z.output<T>[] => {
    if (!Array.isArray(value)) return [];
    const out: z.output<T>[] = [];
    for (const entry of value) {
      const parsed = item.safeParse(entry);
      if (parsed.success) out.push(parsed.data);
    }
    return out;
  });
}

export const indexStatusSchema = z
  .object({
    symbol,
    name: text(),
    market: text(),
    maList,
    close: finiteOrNull,
    changePct: finiteOrNull,
    trend,
    trendLabel: text(),
    score: finiteOrNull,
    /** Optional: number of MAs scored and the report's wording (看多 / 偏多 / 多空不明 / 偏空 / 看空). */
    scoreMax: finiteOrNull,
    scoreLabel: optionalText,
    maValues: numberRecord,
    asOf: optionalText,
    source: optionalText,
  })
  .transform((value) => ({ ...value, name: value.name || value.symbol }));
export type IndexStatus = z.output<typeof indexStatusSchema>;

export const reportMarketSchema = z
  .object({
    key: z
      .string()
      .trim()
      .toLowerCase()
      .pipe(z.enum(MARKET_KEYS)),
    name: text(),
    flag: text(),
    lineText: text(),
    indices: tolerantArray(indexStatusSchema),
  });
export type ReportMarket = z.output<typeof reportMarketSchema>;

/** Moving averages covered by the screener metrics (`metrics.ma`) and the full-market universe. */
export const METRIC_MAS = [20, 50, 200] as const;
export type MetricMa = (typeof METRIC_MAS)[number];
export type MetricTrend = 'up' | 'down' | 'mixed';

/**
 * Screener metrics per stock (`groups[].items[].metrics`, `report/universe.json`). Percentages are in %;
 * `ma` holds the close's distance from each SMA, `fromHi52` is ≤ 0, `rsRank` is a 1–99 percentile
 * within the market's universe. Any field can be null when the history is too short.
 */
export interface StockMetrics {
  bars: number;
  r5: number | null;
  r21: number | null;
  r63: number | null;
  ma: Record<`${MetricMa}`, number | null>;
  ma20Slope5: number | null;
  trend: MetricTrend | null;
  hi52: number | null;
  fromHi52: number | null;
  /** Bars in the 52-week-high window: min(bars, 252). Below ~240 the "52-week" high is a shorter one. */
  hiBars: number;
  rs21: number | null;
  rs63: number | null;
  rsBench: 'SPY' | '^TWII' | null;
  rsRank: number | null;
  volRatio: number | null;
  turnover20: number | null;
}

const toCount = (value: unknown) => {
  const number = toFinite(value);
  return number !== null && number > 0 ? Math.floor(number) : 0;
};

/** Tolerant metrics parser: anything but an object becomes null; bad fields become null (counts 0). */
export function parseMetrics(value: unknown): StockMetrics | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  const maRaw = raw.ma && typeof raw.ma === 'object' && !Array.isArray(raw.ma) ? (raw.ma as Record<string, unknown>) : {};
  const trendText = typeof raw.trend === 'string' ? raw.trend.trim().toLowerCase() : '';
  const bench = typeof raw.rsBench === 'string' ? raw.rsBench.trim().toUpperCase() : '';
  const rank = toFinite(raw.rsRank);
  const fromHi = toFinite(raw.fromHi52);
  const metrics: StockMetrics = {
    bars: toCount(raw.bars),
    r5: toFinite(raw.r5),
    r21: toFinite(raw.r21),
    r63: toFinite(raw.r63),
    ma: { '20': toFinite(maRaw['20']), '50': toFinite(maRaw['50']), '200': toFinite(maRaw['200']) },
    ma20Slope5: toFinite(raw.ma20Slope5),
    trend: trendText === 'up' || trendText === 'down' || trendText === 'mixed' ? trendText : null,
    hi52: toFinite(raw.hi52),
    // The close can never be above the window high; clamp rounding noise.
    fromHi52: fromHi === null ? null : Math.min(0, fromHi),
    hiBars: toCount(raw.hiBars),
    rs21: toFinite(raw.rs21),
    rs63: toFinite(raw.rs63),
    rsBench: bench === 'SPY' || bench === '^TWII' ? bench : null,
    rsRank: rank === null ? null : Math.min(99, Math.max(1, Math.round(rank))),
    volRatio: toFinite(raw.volRatio),
    turnover20: toFinite(raw.turnover20),
  };
  return metrics;
}
const metrics = z.unknown().optional().transform(parseMetrics);

export const groupItemSchema = z
  .object({
    symbol,
    name: text(),
    maList,
    close: finiteOrNull,
    changePct: finiteOrNull,
    maValues: numberRecord,
    note: optionalText,
    volume: finiteOrNull,
    asOf: optionalText,
    metrics,
  })
  .transform((value) => ({ ...value, name: value.name || value.symbol }));
export type GroupItem = z.output<typeof groupItemSchema>;

export const GROUP_KINDS = ['fixed', 'custom', 'scan'] as const;
export type GroupKind = (typeof GROUP_KINDS)[number];
export const reportGroupSchema = z
  .object({
    key: z.string().trim().min(1).max(80),
    name: text(),
    market: z.unknown().optional().transform((value): 'TW' | 'US' =>
      typeof value === 'string' && value.trim().toUpperCase() === 'US' ? 'US' : 'TW',
    ),
    kind: z.unknown().optional().transform((value): GroupKind =>
      typeof value === 'string' && (GROUP_KINDS as readonly string[]).includes(value) ? (value as GroupKind) : 'fixed',
    ),
    maList,
    items: tolerantArray(groupItemSchema),
  })
  .transform((value) => ({ ...value, name: value.name || value.key }));
export type ReportGroup = z.output<typeof reportGroupSchema>;

export const sectorSchema = z.object({
  symbol,
  name: text(),
  trend: text(),
  status: text(),
  rank: finiteOrNull,
  changePct: finiteOrNull,
});
export type ReportSector = z.output<typeof sectorSchema>;

export const reportSchema = z
  .object({
    version: finiteOrNull,
    generatedAt: optionalText,
    reportDate: optionalText,
    markets: tolerantArray(reportMarketSchema),
    worldIndices: tolerantArray(indexStatusSchema),
    lineMessages: z
      .unknown()
      .optional()
      .transform((value) => {
        const record = value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
        return {
          index: typeof record.index === 'string' ? record.index : null,
          sectors: typeof record.sectors === 'string' ? record.sectors : null,
        };
      }),
    groups: tolerantArray(reportGroupSchema),
    sectors: tolerantArray(sectorSchema),
  })
  .transform((report) => {
    // Duplicate market keys or group keys: the first one wins.
    const seenMarkets = new Set<string>();
    const seenGroups = new Set<string>();
    return {
      ...report,
      markets: report.markets.filter((m) => !seenMarkets.has(m.key) && !!seenMarkets.add(m.key)),
      groups: report.groups.filter((g) => !seenGroups.has(g.key) && !!seenGroups.add(g.key)),
    };
  });
export type Report = z.output<typeof reportSchema>;

/** True when the parsed report carries nothing a page could show. */
export function isEmptyReport(report: Report): boolean {
  return (
    !report.markets.length && !report.worldIndices.length && !report.groups.length && !report.sectors.length
  );
}
