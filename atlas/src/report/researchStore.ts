/** Static Pages range transport. The 200 fallback also works on ordinary dev servers. */
import { z } from "zod";

const marketSchema = z.object({
  path: z.string().regex(/^(volume|institutions)\/(TW|TWO|US)\.json$/),
  records: z.record(
    z.string(),
    z.tuple([
      z.number().int().nonnegative(),
      z.number().int().positive(),
      z.number().int().nonnegative(),
    ]),
  ),
  shapes: z.record(z.string(), z.array(z.string())),
});
export const marketIndexSchema = z.object({
  generatedAt: z.string(),
  storage: z.object({
    format: z.literal("research-market-v1"),
    markets: z.record(z.string(), marketSchema),
  }),
  sources: z
    .record(
      z.string(),
      z.object({ fetchedAt: z.string().nullable().optional() }),
    )
    .optional(),
});

export function decodeResearchRecord(
  value: unknown,
  shapes: Record<string, string[]>,
  depth = 0,
): unknown {
  if (depth > 64) throw new Error("研究資料格式驗證失敗，請稍後重試。");
  if (!Array.isArray(value)) return value;
  if (value[0] === null)
    return value
      .slice(1)
      .map((v) => decodeResearchRecord(v, shapes, depth + 1));
  const keys = typeof value[0] === "string" ? shapes[value[0]] : undefined;
  if (!keys || keys.length !== value.length - 1)
    throw new Error("研究資料格式驗證失敗，請稍後重試。");
  return Object.fromEntries(
    keys.map((key, i) => [
      key,
      decodeResearchRecord(value[i + 1], shapes, depth + 1),
    ]),
  );
}

export function requireResearchResponse(response: Response) {
  if (!response.ok)
    throw new Error(
      response.status === 404
        ? "這個資料集尚未收錄，歷史覆蓋累積中。"
        : `資料來源暫時無法取得（HTTP ${response.status}）。`,
    );
}

export async function fetchMarketRecord(
  module: "volume" | "institutions",
  symbol: string,
  url: (path: string) => string,
  signal?: AbortSignal,
): Promise<unknown> {
  const market =
    module === "volume"
      ? /\.(TW|TWO)$/.test(symbol)
        ? "TW"
        : "US"
      : symbol.split(".").at(-1)!;
  // A Pages deployment between the two requests can invalidate byte offsets. Reload once.
  for (let attempt = 0; attempt < 2; attempt++) {
    const response = await fetch(url(module), { signal, cache: "no-cache" });
    requireResearchResponse(response);
    const parsed = marketIndexSchema.safeParse(await response.json());
    if (!parsed.success) throw new Error("研究資料格式驗證失敗，請稍後重試。");
    const index = parsed.data,
      spec = index.storage.markets[market];
    const range = spec?.records[symbol];
    if (!range) throw new Error("這個資料集尚未收錄，歷史覆蓋累積中。");
    if (spec.path !== `${module}/${market}.json`)
      throw new Error("研究資料格式驗證失敗，請稍後重試。");
    const [offset, length, ordinal] = range;
    const data = await fetch(url(spec.path.replace(/\.json$/, "")), {
      signal,
      cache: "no-cache",
      headers: { Range: `bytes=${offset}-${offset + length - 1}` },
    });
    requireResearchResponse(data);
    try {
      const body = await data.arrayBuffer();
      const json: unknown = JSON.parse(new TextDecoder().decode(body));
      if (data.status === 206 && body.byteLength !== length)
        throw new Error("研究資料範圍不符");
      const encoded =
        data.status === 206 ? json : Array.isArray(json) ? json[ordinal] : null;
      const record = decodeResearchRecord(encoded, spec.shapes) as Record<
        string,
        unknown
      > | null;
      if (!record || record.symbol !== symbol)
        throw new Error("股票代號不符，已拒絕顯示其他股票資料。");
      return {
        ...record,
        generatedAt: index.generatedAt,
        fetchedAt: index.sources?.[market]?.fetchedAt ?? null,
      };
    } catch (error) {
      if (signal?.aborted || attempt === 1) throw error;
    }
  }
  throw new Error("研究資料格式驗證失敗，請稍後重試。");
}
