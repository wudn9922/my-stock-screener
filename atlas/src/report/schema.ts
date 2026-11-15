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
 * within the market's universe (of `rsScore`, the weighted 3／6／9／12-month relative return, in files that carry it).
 * Any field can be null when the history is too short.
 */
export interface StockMetrics {
  bars: number;
  r5: number | null;
  r21: number | null;
  r63: number | null;
  ma: Record<`${MetricMa}`, number | null>;
  /** Close's distance from the 20-day EMA in % (null on reports older than the EMA20 filter). */
  ema20: number | null;
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
  /** Weighted blend of the 63／126／189／252-day relative returns vs the benchmark (weights 40／20／20／20); null on older reports. */
  rsScore: number | null;
  /** Wilder ATR(14) ÷ close × 100; null on older reports or short history. */
  atrPct: number | null;
  /** (highest high − lowest low of the latest 20 bars) ÷ lowest low × 100; null on older reports or short history. */
  range20: number | null;
  /** Today's volume ÷ the previous session's volume (null on older reports, whose `volRatio` meant something else). */
  volVsPrev: number | null;
  turnover20: number | null;
  /** Mean traded shares over the latest 20 sessions, including asOf; null on older reports or short history. */
  volumeAvg20: number | null;
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
    ema20: toFinite(raw.ema20),
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
    rsScore: toFinite(raw.rsScore),
    atrPct: toFinite(raw.atrPct),
    range20: toFinite(raw.range20),
    volVsPrev: toFinite(raw.volVsPrev),
    turnover20: toFinite(raw.turnover20),
    volumeAvg20: toFinite(raw.volumeAvg20),
  };
  return metrics;
}
const metrics = z.unknown().optional().transform(parseMetrics);

/**
 * Row status in a non-scan group: `matched` passed the group's MA rule (the report's "成功股票"), `unmatched`
 * has prices but missed the rule, `failed` has no usable prices (`close` / `changePct` are null).
 * Reports written before this field existed only list matched rows, so a missing status means `matched`.
 */
export const ITEM_STATUSES = ['matched', 'unmatched', 'failed'] as const;
export type ItemStatus = (typeof ITEM_STATUSES)[number];
const itemStatus = z.unknown().optional().transform((value): Exclude<ItemStatus, 'matched'> | undefined => {
  const normalized = typeof value === 'string' ? value.trim().toLowerCase() : '';
  return normalized === 'unmatched' || normalized === 'failed' ? normalized : undefined;
});

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
    status: itemStatus,
  })
  .transform((value) => ({ ...value, name: value.name || value.symbol }));
/** `status` is left out for matched rows (and for full-market rows built outside this schema). */
/**
 * Company profile carried by full-market universe rows (`sector`, `industry`, `marketCap` columns of
 * universe.json). Report group rows have none. `marketCap` is in the market's currency (TWD / USD).
 */
export interface StockProfile {
  sector: string | null;
  industry: string | null;
  marketCap: number | null;
}
export type GroupItem = Omit<z.output<typeof groupItemSchema>, 'status'> & {
  status?: Exclude<ItemStatus, 'matched'>;
  profile?: StockProfile;
};

/** The row's status, with the missing value read as `matched`. */
export const itemStatusOf = (item: Pick<GroupItem, 'status'>): ItemStatus => item.status ?? 'matched';

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
  .transform((value) => {
    // An unmatched / failed row never repeats a symbol listed before it (matched rows come first).
    const seen = new Set<string>();
    const items = value.items.filter((item) => {
      const keep = item.status === undefined || !seen.has(item.symbol);
      seen.add(item.symbol);
      return keep;
    });
    return { ...value, name: value.name || value.key, items };
  });
export type ReportGroup = Omit<z.output<typeof reportGroupSchema>, 'items'> & { items: GroupItem[] };

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

// Versioned warning documents fail as a whole; never coerce corrupt values to zero.
const warningDate = z.iso.date();
const fraction = z.number().finite().min(0).max(1).nullable();
const countValue = z.number().finite().nonnegative().nullable();
const warningFlag = z.boolean().nullable();
const volumeWarningPoint = z.object({
  date: warningDate,
  up_vol_up_ratio_5: fraction, down_vol_down_ratio_5: fraction,
  up_vol_up_ratio_10: fraction, down_vol_down_ratio_10: fraction,
  up_vol_up_ratio_20: fraction, down_vol_down_ratio_20: fraction,
  up_vol_up_ratio_60: fraction, down_vol_down_ratio_60: fraction,
  avg60: fraction, median756: fraction, vol60_weak: warningFlag,
});
const marketWarningPoint = z.object({
  date: warningDate, leader_success: fraction, market_success: fraction,
  leader_success_n: countValue, market_success_n: countValue,
  breakout_count_per_1000: countValue, count_p30: countValue,
  uptrend: warningFlag, leader_lag: warningFlag, breadth_divergence: warningFlag,
  avg60: fraction, median756: fraction, vol60_weak: warningFlag,
  warning_level: z.number().int().min(0).max(3), warning_complete: z.boolean(),
});
const ordered = <T extends { date: string }>(rows: T[]) => rows.every((row, i) => i === 0 || row.date > rows[i - 1]!.date);
const warningIndexSource = z.object({
  source: z.enum(['yahoo', 'kline-archive']), lastDate: warningDate,
  validBars: z.number().int().positive(), attempts: z.number().int().min(1).max(3),
  priceBasis: z.literal('split-adjusted'), dividendAdjusted: z.literal(false),
  fallbackReason: z.string().min(1).nullable(),
}).refine(value => value.source === 'kline-archive' ? value.fallbackReason !== null : value.fallbackReason === null);
const warningUpdate = z.discriminatedUnion('status', [
  z.object({ status: z.literal('updated'), checkedAt: z.iso.datetime({ offset: true }),
    generatedAt: z.iso.datetime({ offset: true }), reason: z.null() }),
  z.object({ status: z.literal('retained'), checkedAt: z.iso.datetime({ offset: true }),
    generatedAt: z.iso.datetime({ offset: true }), reason: z.string().min(1) }),
]);
const indexWarningSchema = z.object({
  proxy: z.string().nullable(), validated: z.boolean(),
  meta: z.object({ price: warningIndexSource, volume: warningIndexSource.nullable() }).optional(),
  series: z.array(volumeWarningPoint).min(1).refine(ordered), latest: volumeWarningPoint,
  corrections: z.array(z.object({ start: warningDate, end: warningDate, drawdown: z.number().finite().max(-0.07), confirmed: z.boolean() })
    .refine(value => value.start <= value.end)),
}).refine(value => JSON.stringify(value.latest) === JSON.stringify(value.series.at(-1)));
const warningMarketSchema = z.object({
  asOf: warningDate, universeCount: z.number().int().positive(), evaluated: z.number().int().nonnegative(),
  listingAsOf: warningDate, series: z.array(marketWarningPoint).min(1).refine(ordered), latest: marketWarningPoint,
  indices: z.record(z.string(), indexWarningSchema), limitations: z.array(z.string()),
  update: warningUpdate.optional(),
}).refine(value => value.latest.date === value.asOf && JSON.stringify(value.latest) === JSON.stringify(value.series.at(-1)) && value.evaluated <= value.universeCount);
const warningUnavailableSchema = z.strictObject({
  update: z.object({ status: z.literal('unavailable'), checkedAt: z.iso.datetime({ offset: true }),
    generatedAt: z.null(), reason: z.string().min(1) }),
});
const warningMarketResult = z.union([warningMarketSchema, warningUnavailableSchema]);
const marketParams = z.object({ indices: z.array(z.string()).min(1), leaderShare: z.number().positive().max(1),
  minAmount: z.number().positive(), minPrice: z.number().positive(), successWindow: z.number().int().positive() });
export const marketWarningsSchema = z.object({
  version: z.literal(1), generatedAt: z.iso.datetime({ offset: true }),
  params: z.object({
    windows: z.tuple([z.literal(5), z.literal(10), z.literal(20), z.literal(60)]), defaultWindow: z.literal(60),
    medianWindow: z.number().int().positive(), medianMinValid: z.number().int().positive(), volumeGap: z.number().positive(),
    maxReturn: z.number().positive(), countWindow: z.number().int().positive(), leaderReturnDays: z.number().int().positive(),
    stockJumpLimit: z.number().positive(), minCoverage: z.number().positive().max(1), minEvaluated: z.number().int().positive(),
    liquidityDays: z.number().int().positive(), markets: z.object({ tw: marketParams, us: marketParams }),
    indexVolumeProxies: z.record(z.string(), z.string()), minBreakouts: z.number().int().positive(), smoothDays: z.number().int().positive(),
    leaderGap: z.number().positive(), trendDays: z.number().int().positive(), trendSlopeDays: z.number().int().positive(),
    highWindow: z.number().int().positive(), nearHigh: z.number().positive().max(1), quantileWindow: z.number().int().positive(),
    quantileMinValid: z.number().int().positive(), countQuantile: z.number().positive().max(1), correctionDrop: z.number().positive().max(1),
    historyYears: z.number().int().positive(), closeBufferAtr: z.number().positive(), atrPeriod: z.number().int().positive(),
    observationDays: z.number().int().positive(), barrier: z.number().positive(), swingAtrPeriod: z.number().int().positive(),
    swingReversalAtr: z.number().positive(), swingCountingModel: z.literal('first-higher-close-v1'),
    countDenominator: z.string(), leaderMembership: z.string(), firstPivot: z.string(),
    diagnosticDenominators: z.string(),
  }),
  evidence: z.array(z.string()).min(6), markets: z.object({ tw: warningMarketResult, us: warningMarketResult }),
}).superRefine((data, ctx) => {
  for (const key of ['tw', 'us'] as const) {
    const market = data.markets[key];
    if (market.update) {
      if (market.update.checkedAt !== data.generatedAt ||
        (market.update.status === 'updated' && market.update.generatedAt !== data.generatedAt) ||
        (market.update.generatedAt !== null && Date.parse(market.update.generatedAt) > Date.parse(market.update.checkedAt)))
        ctx.addIssue({ code: 'custom', message: 'Inconsistent market update provenance' });
    }
    if (!('asOf' in market)) continue;
    if (market.evaluated / market.universeCount < data.params.minCoverage || market.evaluated < data.params.minEvaluated)
      ctx.addIssue({ code: 'custom', message: 'Insufficient full-market coverage' });
    if (data.params.markets[key].indices.some(symbol => !market.indices[symbol])) ctx.addIssue({ code: 'custom', message: 'Missing index' });
    for (const [symbol, index] of Object.entries(market.indices)) {
      if (index.latest.date !== market.asOf || index.validated !== (key === 'tw' && symbol === '^TWII') ||
        index.proxy !== (data.params.indexVolumeProxies[symbol] ?? null)) ctx.addIssue({ code: 'custom', message: 'Inconsistent index provenance' });
    }
    for (const row of market.series) {
      const flags = key === 'tw' ? [row.vol60_weak, row.leader_lag, row.breadth_divergence] : [row.leader_lag, row.breadth_divergence];
      if (row.warning_level !== flags.filter(value => value === true).length || row.warning_complete !== flags.every(value => value !== null) || (key === 'us' && row.vol60_weak !== null))
        ctx.addIssue({ code: 'custom', message: 'Inconsistent warning count' });
    }
  }
});
export type MarketWarnings = z.infer<typeof marketWarningsSchema>;
export type MarketWarningPoint = z.infer<typeof marketWarningPoint>;
