import { z } from "zod";
import { reportUrl } from "../report/loadReport";
export interface ExtendedPrice {
  price: number;
  asOf: number;
  source?: string;
}
const slot = z.object({
  status: z.enum(["ok", "stale", "unavailable"]),
  price: z.number().positive().finite().nullable().optional(),
  asOf: z.string().datetime({ offset: true }).nullable().optional(),
  reason: z.string().nullable().optional(),
});
const schema = z.object({
  version: z.literal(1),
  symbol: z.string(),
  checkedAt: z.string(),
  source: z.string(),
  pre: slot,
  post: slot,
});
export type ExtendedSnapshot = z.infer<typeof schema>;
export async function fetchExtendedQuotes(
  symbol: string,
  signal?: AbortSignal,
): Promise<ExtendedSnapshot> {
  if (!/^[A-Z][A-Z0-9.-]{0,14}$/.test(symbol)) throw new Error("非美股代號");
  const response = await fetch(
    new URL(`extended-quotes/${encodeURIComponent(symbol)}.json`, reportUrl()),
    { cache: "no-cache", signal },
  );
  if (!response.ok)
    throw new Error(
      response.status === 404
        ? "此股票尚未收錄延長時段快照"
        : `延長時段快照載入失敗（HTTP ${response.status}）`,
    );
  const value = schema.parse(await response.json());
  if (value.symbol !== symbol) throw new Error("快照代號不符");
  return value;
}
