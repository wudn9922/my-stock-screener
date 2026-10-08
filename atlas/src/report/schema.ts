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

const text = (fallback = '') =>
  z
    .unknown()
    .transform((value) => (typeof value === 'string' ? value.trim() : typeof value === 'number' ? String(value) : fallback));
const optionalText = z
  .unknown()
  .transform((value) => (typeof value === 'string' && value.trim() ? value.trim() : null));
const finiteOrNull = z.unknown().transform((value) => {
  const number = typeof value === 'string' && value.trim() !== '' ? Number(value) : value;
  return typeof number === 'number' && Number.isFinite(number) ? number : null;
});
const maList = z.unknown().transform((value) =>
  Array.isArray(value)
    ? [...new Set(value.map(Number).filter((n) => Number.isInteger(n) && n > 0 && n <= 1000))]
    : [],
);
const numberRecord = z.unknown().transform((value) => {
  const out: Record<string, number> = {};
  if (value && typeof value === 'object' && !Array.isArray(value))
    for (const [key, raw] of Object.entries(value as Record<string, unknown>)) {
      const number = typeof raw === 'string' && raw.trim() !== '' ? Number(raw) : raw;
      if (typeof number === 'number' && Number.isFinite(number)) out[key] = number;
    }
  return out;
});
const trend = z.unknown().transform((value): Trend => {
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
  return z.unknown().transform((value): z.output<T>[] => {
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

export const groupItemSchema = z
  .object({
    symbol,
    name: text(),
    maList,
    close: finiteOrNull,
    changePct: finiteOrNull,
    maValues: numberRecord,
    note: optionalText,
  })
  .transform((value) => ({ ...value, name: value.name || value.symbol }));
export type GroupItem = z.output<typeof groupItemSchema>;

export const GROUP_KINDS = ['fixed', 'custom', 'scan'] as const;
export type GroupKind = (typeof GROUP_KINDS)[number];
export const reportGroupSchema = z
  .object({
    key: z.string().trim().min(1).max(80),
    name: text(),
    market: z.unknown().transform((value): 'TW' | 'US' =>
      typeof value === 'string' && value.trim().toUpperCase() === 'US' ? 'US' : 'TW',
    ),
    kind: z.unknown().transform((value): GroupKind =>
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
