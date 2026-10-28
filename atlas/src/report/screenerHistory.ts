import { z } from "zod";
import { reportUrl, parseReport } from "./loadReport";
import {
  parseUniverse,
  type UniverseMarket,
  type UniverseMarketKey,
} from "./universe";
import {
  mergeGrowth,
  parseGrowthFile,
  parseReportGrowth,
  type GrowthFile,
} from "../fundamentals/GrowthProvider";
import { ValuationProvider } from "../fundamentals/ValuationProvider";
import {
  getScreenerPeFigures,
  type ScreenerPeFigures,
} from "../app/dataSources";
import type { ReportGroup } from "./schema";

const day = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/)
  .refine((value) => {
    const parsed = new Date(value + "T00:00:00Z");
    return (
      Number.isFinite(parsed.getTime()) &&
      parsed.toISOString().slice(0, 10) === value
    );
  });
const indexSchema = z.object({
  version: z.literal(1),
  markets: z.object({ TW: z.array(day), US: z.array(day) }),
});
export function parseHistoryIndex(value: unknown) {
  const parsed = indexSchema.safeParse(value);
  if (!parsed.success) return null;
  return {
    TW: [...new Set(parsed.data.markets.TW)].sort(),
    US: [...new Set(parsed.data.markets.US)].sort(),
  };
}
export interface ScreenerSnapshot {
  dataDate: string;
  capturedAt: string;
  market: UniverseMarket;
  groups: ReportGroup[];
  growth: GrowthFile | null;
  pe: ScreenerPeFigures | null;
}
export async function parseScreenerSnapshot(
  value: unknown,
  market: UniverseMarketKey,
  date: string,
): Promise<ScreenerSnapshot | null> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  if (
    raw.version !== 1 ||
    raw.market !== market ||
    raw.dataDate !== date ||
    typeof raw.capturedAt !== "string" ||
    !Number.isFinite(Date.parse(raw.capturedAt))
  )
    return null;
  const universe = parseUniverse(raw.universe),
    report = parseReport(raw.report);
  if (
    universe.status !== "ok" ||
    !universe.universe.markets[market] ||
    universe.universe.markets[market]!.asOf !== date
  )
    return null;
  const source = universe.universe.markets[market]!;
  const valuation = new ValuationProvider("https://captured.invalid/", {
    fetcher: async () => new Response(JSON.stringify(raw.valuation ?? null)),
  });
  return {
    dataDate: date,
    capturedAt: raw.capturedAt,
    market: source,
    groups: report.status === "ok" ? report.report.groups : [],
    growth: mergeGrowth(
      parseGrowthFile(raw.usGrowth),
      parseReportGrowth(raw.growth),
    ),
    pe: await getScreenerPeFigures(source.items, market, valuation),
  };
}
export async function fetchHistoryIndex(signal?: AbortSignal) {
  const response = await fetch(
    new URL("screener-history/index.json", reportUrl()),
    { cache: "no-cache", signal },
  );
  if (!response.ok)
    throw new Error(
      response.status === 404
        ? "歷史快照尚未開始累積；保存至少兩個交易日後即可比較。"
        : `歷史日期讀取失敗（HTTP ${response.status}）。`,
    );
  const parsed = parseHistoryIndex(await response.json());
  if (!parsed) throw new Error("歷史日期格式無法辨識。");
  return parsed;
}
export async function fetchScreenerSnapshot(
  market: UniverseMarketKey,
  date: string,
  signal?: AbortSignal,
) {
  if (!day.safeParse(date).success) throw new Error("歷史日期不正確。");
  const response = await fetch(
    new URL(`screener-history/${market}-${date}.json`, reportUrl()),
    { signal },
  );
  if (!response.ok) throw new Error(`無法讀取 ${date} 的歷史快照。`);
  const parsed = await parseScreenerSnapshot(
    await response.json(),
    market,
    date,
  );
  if (!parsed) throw new Error(`${date} 的歷史快照格式不正確。`);
  return parsed;
}
