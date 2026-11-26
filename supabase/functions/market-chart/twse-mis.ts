/** Read-only, best-effort TWSE MIS adapter. No cookies, credentials or persistent storage. */
export const MIS_CACHE_MS = 15_000;
const MIN_REQUEST_GAP_MS = 1000;
const TIMEOUT_MS = 2000;
const MAX_QUEUE_WAIT_MS = 2500;
const MAX_SYMBOLS = 40;
const MAX_CACHE_ENTRIES = 10_000;
const TAIPEI_OFFSET = 8 * 3600;
type Fields = "open" | "high" | "low" | "close" | "volume";
type Series = Record<Fields, (number | null)[]>;

export interface MisSnapshot {
  symbol: string;
  date: string;
  time: number;
  price: number | null;
  priceTime: number | null;
  open: number;
  high: number;
  low: number;
  previousClose: number | null;
  volume: number | null;
}

export function misChannel(symbol: string): string | null {
  if (symbol === "^TWII") return "tse_t00.tw";
  if (symbol === "^TWOII") return "otc_o00.tw";
  const match = /^(\d{4,6}[A-Z]?)\.(TW|TWO)$/.exec(symbol);
  return match ? `${match[2] === "TW" ? "tse" : "otc"}_${match[1]}.tw` : null;
}

export function taipeiDate(nowMs: number): string {
  return new Date(nowMs + TAIPEI_OFFSET * 1000).toISOString().slice(0, 10);
}

/** Regular trading hours only. Holidays also fail the source's same-date check. */
export function inMisSession(nowMs: number): boolean {
  const date = new Date(nowMs + TAIPEI_OFFSET * 1000);
  const seconds = date.getUTCHours() * 3600 + date.getUTCMinutes() * 60 +
    date.getUTCSeconds();
  return date.getUTCDay() !== 0 && date.getUTCDay() !== 6 &&
    seconds >= 9 * 3600 && seconds <= 13.5 * 3600;
}

function number(value: unknown): number | null {
  if (typeof value !== "string" && typeof value !== "number") return null;
  const text = String(value).trim();
  if (!/^(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?$/.test(text)) return null;
  const result = Number(text.replaceAll(",", ""));
  return Number.isFinite(result) ? result : null;
}
const positive = (value: unknown) => {
  const n = number(value);
  return n !== null && n > 0 ? n : null;
};
const empty = (value: unknown) =>
  value == null ||
  (typeof value === "string" && ["", "-"].includes(value.trim()));

/** Nested latest trade; rejected unless it is a valid same-day regular-session match. */
function tradePrice(
  raw: unknown,
  date: string,
  nowMs: number,
  high: number,
  low: number,
): { price: number; time: number } | null {
  if (!raw || typeof raw !== "object") return null;
  const trade = raw as Record<string, unknown>;
  const price = positive(trade.z);
  if (
    price === null || price > high || price < low ||
    typeof trade.t !== "string" || !/^\d{2}:\d{2}:\d{2}$/.test(trade.t) ||
    trade.t < "09:00:00" || trade.t > "13:30:00"
  ) return null;
  const time = Date.parse(`${date}T${trade.t}+08:00`) / 1000;
  if (
    !Number.isFinite(time) || time * 1000 > nowMs + 300_000 ||
    new Date((time + TAIPEI_OFFSET) * 1000).toISOString().slice(0, 19) !==
      `${date}T${trade.t}`
  ) return null;
  return { price, time };
}

/** Parses only the requested venue/code; malformed, future and previous-session rows are ignored. */
export function parseMisRow(
  raw: unknown,
  symbol: string,
  nowMs: number,
): MisSnapshot | null {
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>, channel = misChannel(symbol);
  if (!channel) return null;
  const [venue, code] = channel.slice(0, -3).split("_");
  if (
    typeof row.c !== "string" || row.c.toLowerCase() !== code!.toLowerCase() ||
    (row.ex !== undefined && row.ex !== venue)
  ) return null;
  if (
    typeof row.d !== "string" || !/^\d{8}$/.test(row.d) ||
    typeof row.t !== "string" || !/^\d{2}:\d{2}:\d{2}$/.test(row.t)
  ) return null;
  const date = `${row.d.slice(0, 4)}-${row.d.slice(4, 6)}-${row.d.slice(6)}`;
  const time = Date.parse(`${date}T${row.t}+08:00`) / 1000;
  if (
    date !== taipeiDate(nowMs) || !Number.isFinite(time) ||
    time * 1000 > nowMs + 300_000 ||
    new Date((time + TAIPEI_OFFSET) * 1000).toISOString().slice(0, 19) !==
      `${date}T${row.t}` ||
    row.t < "09:00:00" || row.t > "13:30:00"
  ) return null;
  const open = positive(row.o),
    high = positive(row.h),
    low = positive(row.l);
  let price = positive(row.z),
    priceTime: number | null = price === null ? null : time;
  if (
    open === null || high === null || low === null || high < open ||
    low > open || high < low ||
    (price === null && !empty(row.z)) ||
    (price !== null && (price > high || price < low))
  ) return null;
  // Since 2026-11 MIS usually leaves the top-level z at "-" for stocks and ETFs and
  // reports the latest match in a nested trade object, e.g.
  // {"z":"-","trade":{"t":"10:38:35","v":1,"z":"2170.0000"}}. Use it only when it is a
  // same-day regular-session trade inside today's high/low; otherwise keep no price.
  if (price === null) {
    const last = tradePrice(row.trade, date, nowMs, high, low);
    if (last) [price, priceTime] = [last.price, last.time];
  }
  const lots = number(row.v), isIndex = symbol.startsWith("^");
  if (lots === null && !empty(row.v)) return null;
  // Index v is not individual-stock lots. Preserve Yahoo's volume instead of inventing units.
  const volume = isIndex || lots === null ? null : lots * 1000;
  if (volume !== null && !Number.isSafeInteger(volume)) return null;
  return {
    symbol,
    date,
    time,
    price,
    priceTime,
    open,
    high,
    low,
    previousClose: positive(row.y),
    volume,
  };
}

type WarrantStatus =
  | "available"
  | "no_book"
  | "no_quote"
  | "stale"
  | "source_unavailable";
type WarrantBook = {
  bid: number | null;
  bidSize: number | null;
  ask: number | null;
  askSize: number | null;
  quoteTime: number | null;
  status: WarrantStatus;
};

const WARRANT_MAX_AGE_SECONDS = 60;
const emptyBook = (
  status: WarrantStatus,
  quoteTime: number | null = null,
): WarrantBook => ({
  status,
  bid: null,
  bidSize: null,
  ask: null,
  askSize: null,
  quoteTime,
});

function warrantLevel(
  prices: string,
  sizes: unknown,
): [number | null, number | null] {
  const levels = prices.split("_");
  const amounts = typeof sizes === "string" ? sizes.split("_") : [];
  for (const [index, level] of levels.entries()) {
    const price = positive(level);
    if (price !== null) return [price, positive(amounts[index])];
  }
  return [null, null];
}

function warrantBook(raw: unknown, now: number): WarrantBook {
  if (typeof raw !== "object" || raw === null) return emptyBook("no_quote");
  const row = raw as Record<string, unknown>;
  // The MIS warrant rows identify the underlying in rch, including t00 for the TAIEX.
  if (typeof row.rch !== "string" || !row.rch.trim()) {
    return emptyBook("no_quote");
  }
  if (
    typeof row.d !== "string" || !/^\d{8}$/.test(row.d) ||
    typeof row.t !== "string" || !/^\d{2}:\d{2}:\d{2}$/.test(row.t) ||
    row.t < "09:00:00" || row.t > "13:30:00"
  ) return emptyBook("no_quote");
  const date = `${row.d.slice(0, 4)}-${row.d.slice(4, 6)}-${row.d.slice(6)}`;
  const timestamp = Date.parse(`${date}T${row.t}+08:00`);
  if (
    !Number.isFinite(timestamp) || timestamp > now ||
    new Date(timestamp + TAIPEI_OFFSET * 1000).toISOString().slice(0, 19) !==
      `${date}T${row.t}` ||
    (row.tlong !== undefined &&
      Math.floor((number(row.tlong) ?? -1) / 1000) !== timestamp / 1000)
  ) {
    return emptyBook("no_quote");
  }
  const quoteTime = timestamp / 1000;
  if (
    date !== taipeiDate(now) ||
    (inMisSession(now)
      ? now - timestamp > WARRANT_MAX_AGE_SECONDS * 1000
      : row.t !== "13:30:00")
  ) return emptyBook("stale", quoteTime);
  if (typeof row.a !== "string" && typeof row.b !== "string") {
    return emptyBook("no_quote", quoteTime);
  }
  const [bid, bidSize] = warrantLevel(
    typeof row.b === "string" ? row.b : "",
    row.g,
  );
  const [ask, askSize] = warrantLevel(
    typeof row.a === "string" ? row.a : "",
    row.f,
  );
  if (bid === null && ask === null) {
    const noOrders = [row.a, row.b].every((value) =>
      value === undefined ||
      (typeof value === "string" && value.split("_").every(empty))
    );
    return emptyBook(noOrders ? "no_book" : "no_quote", quoteTime);
  }
  return { status: "available", bid, bidSize, ask, askSize, quoteTime };
}

interface MisOptions {
  fetcher?: typeof fetch;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
  timeoutMs?: number;
}

/** Per-isolate shared cache + serialized requests. Failures are negatively cached for 15 s. */
export class TwseMisClient {
  private readonly fetcher: typeof fetch;
  private readonly now: () => number;
  private readonly sleep: (ms: number) => Promise<void>;
  private readonly cache = new Map<
    string,
    { at: number; value: MisSnapshot | null }
  >();
  private readonly prices = new Map<
    string,
    { date: string; price: number; time: number; priceTime: number }
  >();
  private readonly warrantCache = new Map<
    string,
    { at: number; raw?: unknown; failed: boolean }
  >();
  private day = "";
  private queue: Promise<void> = Promise.resolve();
  private lastRequest = -Infinity;
  constructor(private readonly options: MisOptions = {}) {
    this.fetcher = options.fetcher ?? ((url, init) => fetch(url, init));
    this.now = options.now ?? Date.now;
    this.sleep = options.sleep ??
      ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  }
  private async queued<T>(fallback: T, work: () => Promise<T>): Promise<T> {
    const enqueuedAt = this.now();
    let expired = false;
    let timer: ReturnType<typeof setTimeout>;
    const waiting = new Promise<T>((resolve) => {
      timer = setTimeout(() => {
        expired = true;
        resolve(fallback);
      }, MAX_QUEUE_WAIT_MS);
    });
    const run = async () => {
      clearTimeout(timer);
      if (expired || this.now() - enqueuedAt > MAX_QUEUE_WAIT_MS) {
        return fallback;
      }
      return await work();
    };
    const result = this.queue.then(run);
    this.queue = result.then(() => undefined, () => undefined);
    return await Promise.race([result, waiting]).finally(() =>
      clearTimeout(timer)
    );
  }

  private async requestRows(
    symbols: readonly string[],
    allowed = () => true,
  ): Promise<unknown[] | null> {
    const pause = MIN_REQUEST_GAP_MS - (this.now() - this.lastRequest);
    if (pause > 0) await this.sleep(pause);
    if (!allowed()) return null;
    this.lastRequest = this.now();
    try {
      const url = new URL("https://mis.twse.com.tw/stock/api/getStockInfo.jsp");
      url.search = new URLSearchParams({
        ex_ch: symbols.map((s) => misChannel(s)!).join("|"),
        json: "1",
        delay: "0",
      }).toString();
      const response = await this.fetcher(url, {
        headers: { Accept: "application/json" },
        signal: AbortSignal.timeout(this.options.timeoutMs ?? TIMEOUT_MS),
      });
      if (!response.ok) {
        await response.body?.cancel();
        return null;
      }
      const payload = await response.json() as {
        msgArray?: unknown;
        rtcode?: unknown;
      } | null;
      return (payload?.rtcode === undefined || payload.rtcode === "0000") &&
          Array.isArray(payload?.msgArray)
        ? payload.msgArray
        : null;
    } catch {
      return null;
    }
  }

  async getWarrantQuotes(symbols: readonly string[]) {
    const entries = await this.queued(
      new Map<string, WarrantBook>(),
      async () => {
        const missing = symbols.filter((symbol) => {
          const entry = this.warrantCache.get(symbol);
          return !entry || this.now() - entry.at >= MIS_CACHE_MS;
        });
        if (missing.length) {
          const rows = await this.requestRows(missing);
          const byChannel = new Map<string, unknown>();
          for (const raw of rows ?? []) {
            if (typeof raw !== "object" || raw === null) continue;
            const row = raw as Record<string, unknown>;
            if (
              (row.ex !== "tse" && row.ex !== "otc") ||
              typeof row.c !== "string"
            ) continue;
            const channel = `${row.ex}_${row.c.toUpperCase()}.tw`;
            const existing = byChannel.get(channel);
            if (
              !existing ||
              (warrantBook(raw, this.now()).quoteTime ?? -1) >
                (warrantBook(existing, this.now()).quoteTime ?? -1)
            ) {
              byChannel.set(channel, raw);
            }
          }
          for (const symbol of missing) {
            this.warrantCache.set(symbol, {
              at: this.now(),
              raw: byChannel.get(misChannel(symbol)!),
              failed: rows === null,
            });
          }
        }
        while (this.warrantCache.size > MAX_CACHE_ENTRIES) {
          this.warrantCache
            .delete(this.warrantCache.keys().next().value!);
        }
        return new Map(symbols.map((symbol) => {
          const entry = this.warrantCache.get(symbol);
          return [
            symbol,
            !entry || entry.failed
              ? emptyBook("source_unavailable")
              : warrantBook(entry.raw, this.now()),
          ];
        }));
      },
    );
    const now = this.now();
    return {
      asOf: Math.floor(now / 1000),
      source: "twse-mis" as const,
      session: inMisSession(now) ? "regular" as const : "closed" as const,
      results: Object.fromEntries(
        symbols.map(
          (symbol) => [
            symbol,
            entries.get(symbol) ?? emptyBook("source_unavailable"),
          ],
        ),
      ),
    };
  }

  async get(symbols: readonly string[]): Promise<Map<string, MisSnapshot>> {
    if (!inMisSession(this.now())) return new Map();
    const requested = [
      ...new Set(symbols.filter((symbol) => misChannel(symbol) !== null)),
    ];
    if (!requested.length) return new Map();
    return await this.queued(new Map<string, MisSnapshot>(), async () => {
      const now = this.now();
      if (!inMisSession(now)) return new Map();
      const date = taipeiDate(now);
      if (this.day !== date) {
        this.cache.clear();
        this.prices.clear();
        this.day = date;
      }
      const missing = requested.filter((s) =>
        !this.cache.has(s) || now - this.cache.get(s)!.at >= MIS_CACHE_MS
      );
      for (let i = 0; i < missing.length; i += MAX_SYMBOLS) {
        const chunk = missing.slice(i, i + MAX_SYMBOLS);
        const allowed = () =>
          inMisSession(this.now()) && taipeiDate(this.now()) === date;
        const rows = await this.requestRows(chunk, allowed);
        if (!rows && !allowed()) break;
        const found = new Map<string, MisSnapshot>();
        for (const raw of rows ?? []) {
          // An unlabelled ambiguous venue cannot be attributed to either symbol.
          const matches = chunk.map((symbol) =>
            parseMisRow(raw, symbol, this.now())
          ).filter((row): row is MisSnapshot => row !== null);
          if (matches.length !== 1) continue;
          const row = matches[0]!;
          const previous = this.prices.get(row.symbol);
          if (
            row.price !== null && (!previous || row.time >= previous.time)
          ) {
            this.prices.set(row.symbol, {
              date: row.date,
              price: row.price,
              time: row.time,
              priceTime: row.priceTime ?? row.time,
            });
          } else if (
            previous?.date === row.date && previous.time <= row.time &&
            previous.price <= row.high && previous.price >= row.low
          ) {
            row.price = previous.price;
            row.priceTime = previous.priceTime;
          }
          if (
            !found.has(row.symbol) ||
            row.time > found.get(row.symbol)!.time
          ) found.set(row.symbol, row);
        }
        for (const symbol of chunk) {
          this.cache.set(symbol, {
            at: this.now(),
            value: found.get(symbol) ?? null,
          });
        }
      }
      while (this.cache.size > MAX_CACHE_ENTRIES) {
        this.cache.delete(this.cache.keys().next().value!);
      }
      while (this.prices.size > MAX_CACHE_ENTRIES) {
        this.prices.delete(this.prices.keys().next().value!);
      }
      return new Map(requested.flatMap((s) => {
        const entry = this.cache.get(s);
        return entry?.value && this.now() - entry.at < MIS_CACHE_MS &&
            entry.value.date === taipeiDate(this.now())
          ? [[s, entry.value] as const]
          : [];
      }));
    });
  }
}

interface RawResult {
  meta: Record<string, unknown>;
  timestamp: number[];
  indicators: {
    quote: Partial<Series>[];
    adjclose?: { adjclose: (number | null)[] }[];
  };
}

/** Touches today's row only. No valid MIS price means no new row and no live-price marker. */
export function overlayMis(payload: unknown, row: MisSnapshot): unknown {
  const result = (payload as { chart?: { result?: RawResult[] } })?.chart
    ?.result?.[0];
  if (
    result?.meta?.symbol !== row.symbol || !Array.isArray(result.timestamp) ||
    !result.indicators?.quote?.[0]
  ) return payload;
  if (
    !result.timestamp.every(Number.isFinite) ||
    Object.values(result.indicators.quote[0]).some((field) =>
      field != null && !Array.isArray(field)
    )
  ) return payload;
  const indices = result.timestamp.flatMap((t, i) =>
    Number.isFinite(t) && taipeiDate(t * 1000) === row.date ? [i] : []
  );
  if (!indices.length && row.price === null) return payload;
  const copy = structuredClone(payload) as { chart: { result: RawResult[] } };
  const target = copy.chart.result[0]!, q = target.indicators.quote[0]!;
  const fields = ["open", "high", "low", "close", "volume"] as const;
  const time = Date.parse(`${row.date}T09:00:00+08:00`) / 1000;
  let index = indices.find((i) => q.close?.[i] != null) ?? indices[0];
  if (index === undefined) {
    // A future row is malformed; do not reorder or rewrite the existing series.
    if (target.timestamp.some((t) => t > time)) return payload;
    index = target.timestamp.length;
    target.timestamp.push(time);
    for (const field of fields) {
      (q[field] ??= Array(index).fill(null)).push(null);
    }
    target.indicators.adjclose?.forEach((a) => a.adjclose.push(null));
  } else {
    target.timestamp[index] = time;
    for (const field of fields) {
      q[field] ??= Array(target.timestamp.length).fill(null);
    }
  }
  const close = row.price ?? q.close![index];
  // Retained Yahoo close must still form a valid candle with MIS bounds.
  if (
    close !== null && close !== undefined &&
    (close > row.high || close < row.low)
  ) return payload;
  q.open![index] = row.open;
  q.high![index] = row.high;
  q.low![index] = row.low;
  if (row.price !== null) q.close![index] = row.price;
  if (row.volume !== null) q.volume![index] = row.volume;
  if (row.price !== null) {
    target.indicators.adjclose?.forEach((a) => {
      a.adjclose[index] = row.price;
    });
  }
  // Remove duplicate representations of today, including #224 first-5m timestamps.
  for (const duplicate of indices.filter((i) => i !== index).reverse()) {
    target.timestamp.splice(duplicate, 1);
    for (const field of fields) q[field]!.splice(duplicate, 1);
    target.indicators.adjclose?.forEach((a) => a.adjclose.splice(duplicate, 1));
  }
  if (row.price !== null && row.priceTime !== null) {
    Object.assign(target.meta, {
      regularMarketPrice: row.price,
      regularMarketTime: row.priceTime,
      ...(row.previousClose !== null
        ? { chartPreviousClose: row.previousClose }
        : {}),
      atlasLiveSource: "twse-mis",
    });
  } else target.meta.atlasOhlcSource = "twse-mis";
  return copy;
}

/** Yahoo has no ^TWOII quote. Valid MIS can still supply today, without fabricated history. */
export function misOnlyChart(row: MisSnapshot): unknown | null {
  if (row.price === null || row.priceTime === null) return null;
  const payload = {
    chart: {
      result: [{
        meta: {
          symbol: row.symbol,
          currency: "TWD",
          exchangeTimezoneName: "Asia/Taipei",
          gmtoffset: TAIPEI_OFFSET,
          atlasHistoryUnavailable: true,
        },
        timestamp: [],
        indicators: { quote: [{}] },
      }],
      error: null,
    },
  };
  return overlayMis(payload, row);
}
