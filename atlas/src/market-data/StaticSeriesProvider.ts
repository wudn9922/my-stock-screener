import { z } from 'zod';
import {
  calendarPeriodStart,
  filterBarsByRange,
  getMarketProfile,
  normalizeMarketRange,
  normalizeSymbol,
  type Bar,
  type BarResult,
  type CorporateEventsResult,
  type MarketDataProvider,
  type MarketProfile,
  type MarketRange,
  type Quote,
  type Timeframe,
} from './MarketDataProvider';
import { MarketDataUnavailableError } from './ProviderErrors';

/**
 * Official daily series written by the Python report job (main.py) next to the report:
 * `${reportBase}series/index.json` = {"symbols":["^TWOII"]} and `${reportBase}series/<symbol>.json`.
 */
const finite = z.number().finite();
const barSchema = z.object({
  time: z.number().int().positive(),
  open: finite,
  high: finite,
  low: finite,
  close: finite,
  volume: finite.nullable().optional(),
});
export const staticSeriesSchema = z.object({
  symbol: z.string(),
  name: z.string().optional(),
  source: z.string().optional(),
  interval: z.literal('1D'),
  asOf: z.number().finite().optional(),
  bars: z.array(z.unknown()),
});
export type StaticSeries = z.infer<typeof staticSeriesSchema>;
export const staticSeriesIndexSchema = z.object({ symbols: z.array(z.string()) });

export const STATIC_SERIES_TIMEFRAMES = ['1D', '1W', '1M'] as const;
const INDEX_RETRY_MS = 60_000;
const SERIES_TTL_MS = 5 * 60_000;

/** `${BASE_URL}../report/` with dot segments resolved (`/my-stock-screener/atlas/` → `/my-stock-screener/report/`). */
export function defaultReportBase(base: string = import.meta.env?.BASE_URL ?? '/'): string {
  return resolveDotSegments(`${base.endsWith('/') ? base : `${base}/`}../report/`);
}

export function resolveDotSegments(path: string): string {
  const match = /^([a-z][a-z0-9+.-]*:\/\/[^/]*)?(.*)$/i.exec(path)!;
  const prefix = match[1] ?? '';
  // Relative bases (Vite `base: './'`) are left to the browser's own URL resolution.
  if (!match[2].startsWith('/')) return path;
  const segments = match[2].split('/');
  const out: string[] = [];
  for (const segment of segments) {
    if (segment === '..') {
      if (out.length > 1) out.pop();
    } else if (segment !== '.') {
      out.push(segment);
    }
  }
  if (segments.at(-1) === '..' || segments.at(-1) === '.') out.push('');
  return `${prefix}${out.join('/')}`;
}

/** Validates, de-duplicates (last row wins) and sorts official daily rows; invalid OHLC rows are dropped. */
export function cleanDailyBars(rows: readonly unknown[]): Bar[] {
  const byTime = new Map<number, Bar>();
  for (const row of rows) {
    const parsed = barSchema.safeParse(row);
    if (!parsed.success) continue;
    const { time, open, high, low, close } = parsed.data;
    const volume = parsed.data.volume ?? 0;
    if (
      open <= 0 || high <= 0 || low <= 0 || close <= 0 || volume < 0 ||
      high < Math.max(open, close, low) || low > Math.min(open, close, high)
    ) continue;
    byTime.set(time, { time, open, high, low, close, volume });
  }
  return [...byTime.values()].sort((a, b) => a.time - b.time);
}

/** Aggregates daily bars into ISO weeks (Monday start) or calendar months in the market's own calendar. */
export function aggregateBars(daily: readonly Bar[], timeframe: '1W' | '1M', market: MarketProfile): Bar[] {
  const buckets = new Map<number, Bar>();
  for (const bar of daily) {
    const start = calendarPeriodStart(bar.time, timeframe, market);
    const current = buckets.get(start);
    if (!current) {
      buckets.set(start, { ...bar, time: start });
    } else {
      current.high = Math.max(current.high, bar.high);
      current.low = Math.min(current.low, bar.low);
      current.close = bar.close;
      current.volume += bar.volume;
    }
  }
  return [...buckets.values()].sort((a, b) => a.time - b.time);
}

export interface StaticSeriesProviderOptions {
  fetcher?: typeof fetch;
  now?: () => number;
}

interface LoadedSeries {
  symbol: string;
  name: string;
  source: string;
  asOf: number;
  daily: Bar[];
  market: MarketProfile;
}

/** Serves 1D from official series files and aggregates 1W/1M client-side. Other intervals are unsupported. */
export class StaticSeriesProvider implements MarketDataProvider {
  readonly id = 'static-series';
  readonly cacheVersion = 'static-series-v1';
  readonly supportedTimeframes = STATIC_SERIES_TIMEFRAMES;
  readonly capabilities = { corporateEvents: 'unavailable' } as const;
  readonly reportBase: string;
  private readonly fetcher: typeof fetch;
  private readonly now: () => number;
  private index?: { at: number; symbols: ReadonlySet<string>; ok: boolean };
  private indexRequest?: Promise<ReadonlySet<string>>;
  private readonly series = new Map<string, { at: number; data: LoadedSeries }>();
  private readonly pending = new Map<string, Promise<LoadedSeries>>();

  constructor(reportBase: string = defaultReportBase(), options: StaticSeriesProviderOptions = {}) {
    this.reportBase = reportBase.endsWith('/') ? reportBase : `${reportBase}/`;
    this.fetcher = options.fetcher ?? ((input, init) => fetch(input, init));
    this.now = options.now ?? Date.now;
  }

  /** Symbols listed in `series/index.json`. A missing/invalid index yields an empty set (retried after 60 s). */
  async listSymbols(signal?: AbortSignal): Promise<ReadonlySet<string>> {
    signal?.throwIfAborted();
    const index = this.index;
    if (index && (index.ok || this.now() - index.at < INDEX_RETRY_MS)) return index.symbols;
    this.indexRequest ??= (async () => {
      let symbols: ReadonlySet<string> = new Set();
      let ok = false;
      try {
        const response = await this.fetcher(`${this.reportBase}series/index.json`, { cache: 'no-cache' });
        if (response.ok) {
          const parsed = staticSeriesIndexSchema.parse(await response.json());
          symbols = new Set(parsed.symbols.flatMap((value) => {
            try {
              return [normalizeSymbol(value)];
            } catch {
              return [];
            }
          }));
          ok = true;
        }
      } catch {
        // No index (local dev, report not deployed yet): every symbol uses the live provider.
      }
      this.index = { at: this.now(), symbols, ok };
      return symbols;
    })().finally(() => {
      this.indexRequest = undefined;
    });
    const symbols = await this.indexRequest;
    signal?.throwIfAborted();
    return symbols;
  }

  async hasSymbol(symbol: string, signal?: AbortSignal): Promise<boolean> {
    return (await this.listSymbols(signal)).has(normalizeSymbol(symbol));
  }

  async getSymbolTimeframes(): Promise<readonly Timeframe[]> {
    return STATIC_SERIES_TIMEFRAMES;
  }

  async getBars(symbol: string, timeframe: Timeframe, range?: MarketRange, signal?: AbortSignal): Promise<BarResult> {
    const normalizedRange = normalizeMarketRange(range);
    const data = await this.load(symbol, signal);
    if (!(STATIC_SERIES_TIMEFRAMES as readonly string[]).includes(timeframe)) {
      throw new Error(`${data.name} 只提供日 K、週 K、月 K`);
    }
    const all = timeframe === '1D' ? data.daily : aggregateBars(data.daily, timeframe as '1W' | '1M', data.market);
    const bars = filterBarsByRange(all, normalizedRange);
    if (!bars.length) throw new MarketDataUnavailableError(`${data.symbol} 在此區間沒有官方收盤資料`);
    const quote = this.quoteFrom(data);
    return {
      bars,
      source: this.sourceLabel(data),
      session: 'regular',
      delayed: true,
      adjusted: false,
      priceBasis: 'unadjusted',
      ...(quote ? { quote } : {}),
      asOf: data.asOf,
      latestBarAt: bars.at(-1)!.time,
      dataState: 'delayed',
      cacheStatus: 'fresh',
      market: data.market,
    };
  }

  async getQuote(symbol: string, signal?: AbortSignal): Promise<Quote> {
    const data = await this.load(symbol, signal);
    const quote = this.quoteFrom(data);
    if (!quote) throw new MarketDataUnavailableError(`${data.symbol} 官方收盤資料不足，無法計算漲跌`);
    return quote;
  }

  async getCorporateEvents(symbol: string): Promise<CorporateEventsResult> {
    const normalized = normalizeSymbol(symbol);
    return { status: 'unavailable', events: [], source: `${normalized} 官方指數資料不含除權息事件` };
  }

  private sourceLabel(data: LoadedSeries): string {
    const date = new Date(data.asOf * 1000).toISOString().slice(0, 10);
    return `${data.name} · ${data.source} 官方收盤資料（每日更新，非即時） · 資料日期 ${date}`;
  }

  private quoteFrom(data: LoadedSeries): Quote | undefined {
    const latest = data.daily.at(-1),
      previous = data.daily.at(-2);
    if (!latest || !previous || previous.close <= 0) return undefined;
    return {
      symbol: data.symbol,
      price: latest.close,
      change: latest.close - previous.close,
      changePercent: (latest.close / previous.close - 1) * 100,
      asOf: latest.time,
      source: this.sourceLabel(data),
      dataState: 'delayed',
      retrievedAt: data.asOf,
      cacheStatus: 'fresh',
      market: data.market,
    };
  }

  private async load(symbolInput: string, signal?: AbortSignal): Promise<LoadedSeries> {
    signal?.throwIfAborted();
    const symbol = normalizeSymbol(symbolInput);
    const cached = this.series.get(symbol);
    if (cached && this.now() - cached.at < SERIES_TTL_MS) return cached.data;
    let request = this.pending.get(symbol);
    if (!request) {
      request = (async () => {
        let response: Response;
        try {
          response = await this.fetcher(`${this.reportBase}series/${encodeURIComponent(symbol)}.json`, { cache: 'no-cache' });
        } catch (error) {
          throw new MarketDataUnavailableError(`暫時無法取得 ${symbol} 行情，請稍後再試`, { cause: error });
        }
        if (!response.ok) {
          throw new MarketDataUnavailableError(`暫時無法取得 ${symbol} 行情，請稍後再試（官方序列檔 HTTP ${response.status}）`);
        }
        let parsed: StaticSeries;
        try {
          parsed = staticSeriesSchema.parse(await response.json());
        } catch (error) {
          throw new Error(`${symbol} 官方序列檔格式錯誤`, { cause: error });
        }
        if (parsed.symbol.trim().toUpperCase() !== symbol) throw new Error(`${symbol} 官方序列檔的代號不符`);
        const daily = cleanDailyBars(parsed.bars);
        if (!daily.length) throw new MarketDataUnavailableError(`${symbol} 官方序列檔沒有有效的日 K`);
        const data: LoadedSeries = {
          symbol,
          name: parsed.name?.trim() || symbol,
          source: parsed.source?.trim() || '官方資料',
          asOf: parsed.asOf ?? daily.at(-1)!.time,
          daily,
          market: getMarketProfile(symbol),
        };
        this.series.set(symbol, { at: this.now(), data });
        return data;
      })();
      this.pending.set(symbol, request);
      void request.finally(() => this.pending.delete(symbol)).catch(() => undefined);
    }
    const data = await request;
    signal?.throwIfAborted();
    return data;
  }
}
