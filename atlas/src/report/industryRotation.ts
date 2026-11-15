import { z } from "zod";
import { reportUrl } from "./loadReport";
const numeric = z.number().finite().nullable();
const row = z.object({
  name: z.string(),
  count: z.number().int().nonnegative(),
  validCount: z.number().int().nonnegative(),
  returnPct: numeric,
  // Added later and optional so files published before the change still parse.
  liquidCount: z.number().int().nonnegative().optional(),
  medianReturnPct: numeric.optional(),
  capWeightedReturnPct: numeric.optional(),
  upShare: numeric,
  volumeRatio: numeric,
  volumeCount: z.number().int().nonnegative(),
  pairedCount: z.number().int().nonnegative(),
  previousReturnPct: numeric,
  momentumChange: numeric,
  rank: z.number().int().nullable(),
});
const period = z.object({
  startDate: z.string(),
  endDate: z.string(),
  volumePreviousStart: z.string(),
  rows: z.array(row).max(200),
});
const method = z.object({
  rankBy: z.literal("median"),
  minAmount: z.number().finite().nonnegative(),
  minPrice: z.number().finite().nonnegative(),
  minMembers: z.number().int().positive(),
});
const market = z.object({
  asOf: z.string(),
  scope: z.enum(["full", "liquid"]),
  method: method.optional(),
  periods: z.object({ d1: period, w1: period, m1: period, m3: period }),
});
const schema = z.object({
  version: z.literal(1),
  generatedAt: z.string(),
  markets: z.object({ TW: market.optional(), US: market.optional() }),
});
export type IndustryRotation = z.infer<typeof schema>;
export type IndustryRotationRow = z.infer<typeof row>;
export type IndustryRotationMethod = z.infer<typeof method>;
/** Headline return: the median when the file carries it (even if null = unranked), else the legacy mean. */
export function headlineReturn(r: IndustryRotationRow): number | null {
  return r.medianReturnPct === undefined ? r.returnPct : r.medianReturnPct;
}
export function parseIndustryRotation(value: unknown): IndustryRotation | null {
  const parsed = schema.safeParse(value);
  return parsed.success ? parsed.data : null;
}
export async function fetchIndustryRotation(signal?: AbortSignal) {
  const response = await fetch(new URL("industry-rotation.json", reportUrl()), {
    signal,
    cache: "no-cache",
  });
  if (!response.ok)
    throw new Error(
      response.status === 404
        ? "產業輪動比較資料尚未產生。"
        : `產業輪動資料讀取失敗（HTTP ${response.status}）。`,
    );
  const parsed = parseIndustryRotation(await response.json());
  if (!parsed) throw new Error("產業輪動資料格式不正確。");
  return parsed;
}
export function sortIndustryRotation(
  rows: readonly IndustryRotationRow[],
  metric: "returnPct" | "upShare" | "volumeRatio",
) {
  const value = (r: IndustryRotationRow) =>
    metric === "returnPct" ? headlineReturn(r) : r[metric];
  return [...rows].sort((a, b) => {
    const x = value(a),
      y = value(b);
    return x === null
      ? y === null
        ? a.name.localeCompare(b.name)
        : 1
      : y === null
        ? -1
        : y - x || a.name.localeCompare(b.name);
  });
}

function money(value: number, currency: "NT$" | "US$") {
  const [scaled, unit] =
    value >= 1e8
      ? [value / 1e8, "億"]
      : value >= 1e4
        ? [value / 1e4, "萬"]
        : [value, ""];
  return `${currency}${Number(scaled.toFixed(2)).toLocaleString("en-US")}${unit}`;
}
/** Visible one-line method note, built only from the published method block. */
export function industryMethodNote(
  method: IndustryRotationMethod,
  market: "TW" | "US",
) {
  const currency = market === "TW" ? "NT$" : "US$";
  return `排名依中位數；僅計入 20 日均成交值 ≥ ${money(method.minAmount, currency)}、股價 ≥ ${money(method.minPrice, currency)} 的股票；少於 ${method.minMembers} 檔不排名。`;
}
