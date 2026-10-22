import { z } from "zod";
import type { Bar } from "../market-data/MarketDataProvider";
const price = z.number().finite();
const row = z
  .tuple([
    z
      .string()
      .regex(/^\d{4}-\d{2}-\d{2}$/)
      .refine((d) => {
        const parsed = new Date(d + "T00:00:00Z");
        return (
          Number.isFinite(parsed.getTime()) &&
          parsed.toISOString().slice(0, 10) === d
        );
      }, "Invalid calendar date"),
    price,
    price,
    price,
    price,
    price.nonnegative().nullable(),
  ])
  .refine(
    ([, o, h, l, c]) => h >= Math.max(o, c, l) && l <= Math.min(o, c, h),
    "Invalid OHLC bounds",
  );
export const researchCandlesSchema = z
  .object({
    version: z.literal(1),
    symbol: z.string(),
    interval: z.literal("1D"),
    bars: z.array(row),
  })
  .superRefine((v, ctx) => {
    if (v.bars.some((b, i) => i > 0 && b[0] <= v.bars[i - 1]![0]))
      ctx.addIssue({
        code: "custom",
        message: "Dates must be ascending and unique",
      });
  });
export function researchBars(raw: unknown, symbol: string): Bar[] {
  const data = researchCandlesSchema.parse(raw);
  if (data.symbol !== symbol)
    throw new Error("K線代號不符，已拒絕顯示其他商品資料。");
  // Daily calendar labels, not claimed execution timestamps. Noon UTC keeps the source
  // date stable for both the engine's UTC labels and its weekday future-area mapper.
  return data.bars.map(([date, open, high, low, close, volume]) => ({
    time: Date.parse(date + "T12:00:00Z") / 1000,
    open,
    high,
    low,
    close,
    volume: volume ?? 0,
  }));
}

/** Deterministic canonical identity, separate from stock/ETF drawings in the main workspace. */
export function researchDrawingSymbol(identity: string) {
  let hash = 14695981039346656037n;
  for (const c of identity)
    hash = BigInt.asUintN(
      64,
      (hash ^ BigInt(c.charCodeAt(0))) * 1099511628211n,
    );
  return `^R${hash.toString(36).toUpperCase()}`;
}
