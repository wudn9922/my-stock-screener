import { z } from "zod";
import { reportUrl } from "../report/loadReport";
import type { Quote } from './MarketDataProvider';
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

// Shared only within this page. Mounting a row/header does not add a polling loop.
const snapshots = new Map<string, { expires: number; value: Promise<ExtendedSnapshot> }>();
export function loadExtendedSnapshot(symbol: string, retry = false): Promise<ExtendedSnapshot> {
  const cached = snapshots.get(symbol);
  if (!retry && cached && cached.expires > Date.now()) return cached.value;
  const value = fetchExtendedQuotes(symbol);
  snapshots.set(symbol, { expires: Date.now() + 300_000, value });
  if (snapshots.size > 256) snapshots.delete(snapshots.keys().next().value!);
  void value.catch(() => { if (snapshots.get(symbol)?.value === value) snapshots.delete(symbol); });
  return value;
}

export interface SelectedExtendedPrice extends ExtendedPrice {
  mode: 'pre' | 'post';
  changePercent?: number;
}
/** Reject invalid/future observations before choosing the freshest source, then freshest session. */
export function selectExtendedQuote(symbol: string, quote: Quote | null | undefined, snapshot?: ExtendedSnapshot, now = Date.now() / 1000): SelectedExtendedPrice | undefined {
  const valid = (value: ExtendedPrice | undefined): value is ExtendedPrice => !!value && Number.isFinite(value.price) && value.price > 0 && Number.isFinite(value.asOf) && value.asOf > 0 && value.asOf <= now + 300;
  const values: SelectedExtendedPrice[] = [];
  for (const mode of ['pre', 'post'] as const) {
    const inline = quote?.symbol === symbol ? quote.extended?.[mode] : undefined;
    const slot = snapshot?.symbol === symbol ? snapshot[mode] : undefined;
    const recorded = slot?.status !== 'unavailable' && slot?.price && slot.asOf ? { price: slot.price, asOf: Date.parse(slot.asOf) / 1000, source: snapshot?.source } : undefined;
    const value = valid(inline) && (!valid(recorded) || inline.asOf >= recorded.asOf) ? { ...inline, source: 'Yahoo 延遲行情' } : valid(recorded) ? recorded : undefined;
    if (!value) continue;
    // Never compare an old extended trade with a regular price observed later than that trade.
    const changePercent = quote?.symbol === symbol && quote.price > 0 && quote.asOf > 0 && quote.asOf <= value.asOf ? (value.price / quote.price - 1) * 100 : undefined;
    values.push({ ...value, mode, ...(Number.isFinite(changePercent) ? { changePercent } : {}) });
  }
  return values.sort((a, b) => b.asOf - a.asOf)[0];
}
