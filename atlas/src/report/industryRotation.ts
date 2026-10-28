import { z } from "zod";
import { reportUrl } from "./loadReport";
const numeric = z.number().finite().nullable();
const row = z.object({
  name: z.string(),
  count: z.number().int().nonnegative(),
  validCount: z.number().int().nonnegative(),
  returnPct: numeric,
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
const market = z.object({
  asOf: z.string(),
  scope: z.enum(["full", "liquid"]),
  periods: z.object({ d1: period, w1: period, m1: period, m3: period }),
});
const schema = z.object({
  version: z.literal(1),
  generatedAt: z.string(),
  markets: z.object({ TW: market.optional(), US: market.optional() }),
});
export type IndustryRotation = z.infer<typeof schema>;
export type IndustryRotationRow = z.infer<typeof row>;
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
  return [...rows].sort((a, b) =>
    a[metric] === null
      ? b[metric] === null
        ? a.name.localeCompare(b.name)
        : 1
      : b[metric] === null
        ? -1
        : b[metric]! - a[metric]! || a.name.localeCompare(b.name),
  );
}
