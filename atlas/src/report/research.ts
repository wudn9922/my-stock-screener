/** Versioned public research contracts. Fetches never change user settings or provider. */
import { z } from "zod";
import { reportUrl } from "./loadReport";
import { fetchMarketRecord, requireResearchResponse } from "./researchStore";
const n = z.number().finite();
const nullable = n.nullable();
const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const meta = z.object({
  methodVersion: z.literal("research-v1"),
  source: z.string(),
  sourceUrl: z.string().url(),
  asOf: day.nullable(),
  status: z.enum(["ok", "stale", "unavailable", "error"]),
  fetchedAt: z.string().nullable().default(null),
  publishedAt: z.string().nullable(),
  missingReason: z.string().nullable(),
  generatedAt: z.string().optional(),
});
export type ResearchMeta = z.infer<typeof meta>;
const flow = z.object({ buy: n.nonnegative(), sell: n.nonnegative(), net: n });
const observation = z.object({
  date: day,
  foreign: flow,
  trust: flow,
  dealer: flow,
  foreignExDealer: flow,
  foreignDealer: flow,
  dealerOwn: flow,
  dealerHedge: flow,
  close: nullable,
  netVolumePct: nullable,
});
const flowWindow = z.object({
  net: nullable,
  observations: n.int().nonnegative(),
  requested: n.int().positive(),
  complete: z.boolean(),
});
const cost = z.object({
  currentAsOf: day.nullable().optional(),
  currentClose: nullable.optional(),
  window: n,
  observations: n,
  positiveDays: n,
  coveragePct: nullable,
  reference: nullable,
  closeLow: nullable,
  closeHigh: nullable,
  low: nullable,
  high: nullable,
  distancePct: nullable,
  priceBasis: z.string(),
  days: z.array(
    z.object({
      date: day,
      net: n,
      close: n,
      marketAverage: n,
      basis: z.string(),
    }),
  ),
});
export const institutionsSchema = meta.extend({
  symbol: z.string(),
  market: z.enum(["TW", "TWO"]),
  currency: z.literal("TWD"),
  unit: z.literal("shares"),
  rows: z.array(observation),
  marketPriceSource: meta.nullable().optional(),
  summary: z.record(
    z.enum(["foreign", "trust", "dealer"]),
    z.object({ windows: z.record(z.string(), flowWindow), streak: n }),
  ),
  costs: z.record(
    z.enum(["foreign", "trust", "dealer"]),
    z.record(z.string(), cost),
  ),
  priceBasis: z.string(),
});
export type Institutions = z.infer<typeof institutionsSchema>;
const feature = z.object({
  officialVolume: nullable.optional(),
  date: day,
  close: nullable,
  volume: nullable,
  valid: z.boolean(),
  returnPct: nullable,
  priorAvg20: nullable,
  rvol: nullable,
  amount: nullable,
  amountBasis: z.string(),
  rangePct: nullable,
  missingReason: z.string().nullable(),
});
const average = z.object({
  officialObservations: n.int().nonnegative().optional(),
  value: nullable,
  observations: n,
  requested: n,
});
export const volumeSchema = meta.extend({
  marketAmountSource: meta.nullable().optional(),
  symbol: z.string(),
  market: z.enum(["TW", "US"]),
  currency: z.enum(["TWD", "USD"]),
  priceBasis: z.string(),
  latest: feature,
  features: z.array(feature),
  volumeAverages: z.record(z.string(), average),
  amountAverages: z.record(z.string(), average),
  volumeTotals: z.record(z.string(), nullable),
  amountTotals: z.record(z.string(), nullable),
  volumePercentile: nullable,
  maxVolumeDate: day.nullable(),
  zeroVolumeDays: n,
  validDayRatio: nullable,
  amihud20: nullable,
  amihudObservations: n,
  amihudUnit: z.string(),
  upShrinkStreak: n,
  downExpandStreak: n,
  lastAbnormalDate: day.nullable(),
  risks: z.array(z.string()),
});
export type VolumeResearch = z.infer<typeof volumeSchema>;
export type VolumeFeature = z.infer<typeof feature>;
const alert = z.object({
  id: z.string(),
  symbol: z.string(),
  market: z.enum(["TW", "TWO"]),
  kind: z.enum(["notice", "disposal"]),
  date: day,
  name: z.string(),
  reason: z.string(),
  start: day.nullable(),
  end: day.nullable(),
  period: z.string().nullable(),
  measures: z.string(),
  content: z.string(),
  sourceUrl: z.string().url(),
  fetchedAt: z.string(),
  publishedAt: z.string().nullable(),
});
export const alertsSchema = z.object({
  version: z.literal(1),
  generatedAt: z.string(),
  asOf: day,
  sources: z.record(
    z.string(),
    meta.extend({
      historyFrom: day.nullable().optional(),
      historyUrl: z.string().url().optional(),
    }),
  ),
  events: z.array(alert),
});
export type OfficialAlert = z.infer<typeof alert>;
const secEvent = z.object({
  id: z.string(),
  symbol: z.string(),
  symbols: z.array(z.string()).optional(),
  cik: z.string().regex(/^\d{10}$/),
  companyName: z.string(),
  form: z.enum(["8-K", "8-K/A"]),
  date: day,
  acceptedAt: z.string().nullable(),
  accession: z.string().regex(/^\d{10}-\d{2}-\d{6}$/),
  items: z.array(z.string()),
  categories: z.array(
    z.object({ item: z.string(), category: z.string(), title: z.string() }),
  ),
  title: z.string(),
  summary: z.string().nullable(),
  summaryStatus: z.enum(["parsed", "unparsed"]),
  sourceUrl: z.string().url(),
  fetchedAt: z.string().nullable(),
  amendment: z.boolean(),
});
export const secSchema = meta.extend({
  version: z.literal(1),
  universe: n,
  mapped: n,
  checkedSymbols: z.record(
    z.string(),
    z.object({
      cik: z.string(),
      fetchedAt: z.string(),
      asOf: day,
      status: z.string(),
      applicability: z.string(),
    }),
  ),
  indexedDates: z.array(day),
  events: z.array(secEvent),
  coverageNote: z.string(),
});
export type SecRadar = z.infer<typeof secSchema>;
const asset = meta.extend({
  name: z.string().optional(),
  frequency: z.enum(["daily", "weekly", "monthly"]).optional(),
  unit: z.string().optional(),
  instrumentType: z.string().optional(),
  latest: n.optional(),
  history: z.array(z.tuple([day, n])).optional(),
  changes: z.record(z.string(), nullable).optional(),
  baseCurrency: z.string().optional(),
  quoteCurrency: z.string().optional(),
  quoteDirection: z.string().optional(),
});
export const worldAssetsSchema = z.object({
  version: z.literal(1),
  generatedAt: z.string(),
  series: z.record(z.string(), asset),
  limitations: z.string(),
});
export type WorldAsset = z.infer<typeof asset>;
export type WorldAssets = z.infer<typeof worldAssetsSchema>;
export function researchUrl(path: string): string {
  return new URL(`research/${path}.json`, reportUrl()).href;
}
export async function fetchResearch<T>(
  path: string,
  schema: z.ZodType<T>,
  signal?: AbortSignal,
  symbol?: string,
): Promise<T> {
  const stock = /^(volume|institutions)\/([^/]+)$/.exec(path);
  let raw: unknown;
  if (stock) {
    if (symbol && stock[2] !== symbol)
      throw new Error("股票代號不符，已拒絕顯示其他股票資料。");
    raw = await fetchMarketRecord(
      stock[1] as "volume" | "institutions",
      stock[2]!,
      researchUrl,
      signal,
    );
  } else {
    const response = await fetch(researchUrl(path), {
      signal,
      cache: "no-cache",
    });
    requireResearchResponse(response);
    raw = await response.json();
  }
  const result = schema.safeParse(raw);
  if (!result.success) throw new Error("研究資料格式驗證失敗，請稍後重試。");
  if (symbol && (result.data as { symbol?: string }).symbol !== symbol)
    throw new Error("股票代號不符，已拒絕顯示其他股票資料。");
  return result.data;
}
export function volumeStructure(
  features: VolumeFeature[],
  window: number,
  shrink = 1,
  expand = 1,
) {
  const sample = features
    .slice(-window)
    .filter((r) => r.valid && r.returnPct !== null && r.rvol !== null);
  const up = sample.filter((r) => r.returnPct! > 0),
    down = sample.filter((r) => r.returnPct! < 0);
  const fraction = (
    days: VolumeFeature[],
    test: (r: VolumeFeature) => boolean,
  ) => {
    const numerator = days.filter(test).length,
      denominator = days.length;
    return {
      numerator,
      denominator,
      pct: denominator ? (numerator / denominator) * 100 : null,
    };
  };
  return {
    observations: sample.length,
    upShrink: fraction(up, (r) => r.rvol! < shrink),
    upExpand: fraction(up, (r) => r.rvol! > expand),
    downExpand: fraction(down, (r) => r.rvol! > expand),
    downShrink: fraction(down, (r) => r.rvol! < shrink),
  };
}
