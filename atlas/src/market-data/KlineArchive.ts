/** Public, read-only archive. CSVs use the existing disposable market IndexedDB cache. */
import { CachedMarketDataProvider, type CachedMarketDataOptions } from './CachedMarketDataProvider';
import { usableBarResult } from './KlineIntegrity';
import { calendarDate, getMarketProfile, marketDateTime } from './MarketProfile';
import { normalizeSymbol, type Bar, type BarResult, type MarketRange, type Quote } from './MarketDataProvider';

export const ARCHIVE_BASE = 'https://raw.githubusercontent.com/wudn9922/my-stock-screener/kline-archive/';
export const ARCHIVE_SOURCE = 'GitHub 永久封存';
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const DAY = 86400;
export interface ArchiveEntry {
  version: 1; symbol: string; market: 'tw' | 'us'; path: string; firstDate: string; lastDate: string;
  years: Record<string, { sha256: string; firstDate: string; lastDate: string; rows: number; csvBytes: number }>;
}
export interface ArchiveOptions {
  baseUrl?: string; fetcher?: typeof fetch; now?: () => number;
  cache?: false | CachedMarketDataOptions;
  timeoutMs?: number;
  /** Request owner; shared downloads belong to the document, not an individual chart. */
  page?: Pick<Window, 'addEventListener' | 'removeEventListener'>;
}

export function parseArchiveCsv(text: string, symbol: string, year: number): BarResult {
  const lines = text.trim().split(/\r?\n/);
  if (lines.shift() !== 'Date,Open,High,Low,Close,Volume') throw new Error('封存 CSV 欄位不符');
  const missingVolumeAt: number[] = [];
  const bars: Bar[] = [];
  let previous = '';
  for (const line of lines) {
    const values = line.split(',');
    const date = values[0]!;
    const bucket = Date.parse(date + 'T00:00:00Z') / 1000;
    if (values.length !== 6 || !DATE.test(date) || !Number.isFinite(bucket)
      || new Date(bucket * 1000).toISOString().slice(0, 10) !== date || !date.startsWith(`${year}-`) || date <= previous)
      throw new Error('封存 CSV 日期或排序不符');
    previous = date;
    // Preserve the existing stock/Yahoo daily anchor convention; TPEx's official
    // ^TWOII series is the one existing UTC-date series in the deployed index.
    const profile = getMarketProfile(symbol);
    const time = symbol === '^TWOII' ? bucket : marketDateTime(date, profile.sessionOpenMinutes, profile);
    const [open, high, low, close] = values.slice(1, 5).map(value => value.trim() ? Number(value) : NaN);
    const volume = values[5]!.trim() ? Number(values[5]) : 0;
    if (!values[5]!.trim()) missingVolumeAt.push(time);
    bars.push({ time, open: open!, high: high!, low: low!, close: close!, volume });
  }
  return usableBarResult({ bars, missingVolumeAt, source: ARCHIVE_SOURCE, session: 'regular', delayed: true,
    adjusted: true, priceBasis: 'split-adjusted', dataState: 'delayed', market: getMarketProfile(symbol) }, symbol, '1D');
}

function validDate(value: unknown): value is string {
  return typeof value === 'string' && DATE.test(value) && Number.isFinite(Date.parse(value))
    && new Date(value).toISOString().slice(0, 10) === value;
}
export function parseArchiveIndex(raw: unknown, symbol: string): ArchiveEntry | null {
  if (raw === null) return null;
  const item = raw as Partial<ArchiveEntry> | null;
  const market = getMarketProfile(symbol).market === 'TW' ? 'tw' : 'us';
  if (!item || item.version !== 1 || item.symbol !== symbol || item.market !== market
    || item.path !== `${market}/${encodeURIComponent(symbol)}` || !validDate(item.firstDate)
    || !validDate(item.lastDate) || item.firstDate > item.lastDate || !item.years
    || typeof item.years !== 'object' || Array.isArray(item.years) || !Object.keys(item.years).length)
    throw new Error('封存個股 index 身分、日期或路徑不符');
  const years = Object.entries(item.years);
  for (const [year, detail] of years) {
    if (!/^\d{4}$/.test(year) || year < item.firstDate.slice(0, 4) || year > item.lastDate.slice(0, 4)
      || !detail || !/^[a-f0-9]{64}$/.test(detail.sha256) || !validDate(detail.firstDate) || !validDate(detail.lastDate)
      || !detail.firstDate.startsWith(year + '-') || !detail.lastDate.startsWith(year + '-')
      || detail.firstDate > detail.lastDate || detail.firstDate < item.firstDate || detail.lastDate > item.lastDate
      || !Number.isSafeInteger(detail.rows) || detail.rows < 1 || !Number.isSafeInteger(detail.csvBytes) || detail.csvBytes < 1)
      throw new Error('封存個股 index 年度明細不符');
  }
  if (years.map(([, detail]) => detail.firstDate).sort()[0] !== item.firstDate
    || years.map(([, detail]) => detail.lastDate).sort().at(-1) !== item.lastDate)
    throw new Error('封存個股 index 年度日期不符');
  return item as ArchiveEntry;
}

export class KlineArchive {
  private readonly indexes = new Map<string, Promise<ArchiveEntry | null>>();
  private indexDay = '';
  private readonly years = new Map<string, Promise<BarResult>>();
  private readonly fetcher: typeof fetch;
  private readonly now: () => number;
  private readonly base: string;
  constructor(private readonly options: ArchiveOptions = {}) {
    this.fetcher = options.fetcher ?? ((url, init) => fetch(url, init));
    this.now = options.now ?? Date.now;
    this.base = options.baseUrl ?? ARCHIVE_BASE;
  }
  private async request<T>(path: string, read: (response: Response) => Promise<T>): Promise<T> {
    const owner = this.options.page ?? (typeof window === 'undefined' ? undefined : window);
    const navigation = new AbortController();
    const leave = () => navigation.abort();
    // WebKit cancels network loads *before* pagehide, rejecting them as TypeError.
    // Abort explicitly first so ArchiveFirstProvider can distinguish leaving from
    // an unavailable archive. Keep listeners through body consumption as well.
    owner?.addEventListener('beforeunload', leave, { once: true });
    owner?.addEventListener('pagehide', leave, { once: true });
    const signal = AbortSignal.any([navigation.signal, AbortSignal.timeout(this.options.timeoutMs ?? 8000)]);
    try {
      const response = await this.fetcher(this.base + path, { signal });
      const value = await read(response);
      signal.throwIfAborted();
      return value;
    } catch (error) {
      // Some engines report a transport TypeError even after explicit abort.
      signal.throwIfAborted();
      throw error;
    } finally {
      owner?.removeEventListener('beforeunload', leave);
      owner?.removeEventListener('pagehide', leave);
    }
  }
  private loadIndex(symbol: string): Promise<ArchiveEntry | null> {
    const day = new Date(this.now()).toISOString().slice(0, 10), key = `${day}:${symbol}`;
    if (this.indexDay !== day) { this.indexes.clear(); this.indexDay = day; }
    let pending = this.indexes.get(key);
    if (!pending) {
      const market = getMarketProfile(symbol).market === 'TW' ? 'tw' : 'us';
      const load = async () => {
        // Index filenames are encoded on disk; encode '%' again for HTTP.
        const path = `index/${market}/${encodeURIComponent(encodeURIComponent(symbol))}.json`;
        return this.request(path, async response => {
          if (response.status === 404) return null;
          if (!response.ok) throw new Error(`永久封存 index 讀取失敗（HTTP ${response.status}）`);
          return parseArchiveIndex(await response.json(), symbol);
        });
      };
      if (this.options.cache === false) pending = load();
      else {
        const provider = { id: 'kline-archive-index', cacheVersion: `${this.base}:v1:${day}`, supportedTimeframes: ['1D'] as const,
          getBars: async (): Promise<BarResult> => { throw new Error('Index has no bars'); },
          getQuote: async (): Promise<Quote> => { throw new Error('Index has no quote'); } };
        const cached = new CachedMarketDataProvider(provider, { ...this.options.cache, now: this.now,
          ttlMs: Date.parse(day + 'T00:00:00Z') + DAY * 1000 - this.now(), maxEntries: this.options.cache?.maxEntries ?? 10000 });
        pending = cached.getDocument(symbol, load, value => parseArchiveIndex(value, symbol))
          .finally(() => cached.close().catch(() => undefined));
      }
      this.indexes.set(key, pending);
      // A cancelled navigation can leave this document in use. Its next read
      // must retry, rather than reusing the rejected shared index promise.
      void pending.catch(error => {
        if (error?.name === 'AbortError' && this.indexes.get(key) === pending) this.indexes.delete(key);
      });
      // Covers the complete public TW/US catalog, including visitors without IDB.
      while (this.indexes.size > 10000) this.indexes.delete(this.indexes.keys().next().value!);
    }
    return pending;
  }
  async getDaily(symbolInput: string, range?: MarketRange, signal?: AbortSignal): Promise<BarResult | null> {
    const symbol = normalizeSymbol(symbolInput);
    signal?.throwIfAborted();
    const entry = await this.loadIndex(symbol);
    signal?.throwIfAborted();
    if (!entry) return null;
    const first = Math.max(Number(entry.firstDate.slice(0, 4)), range?.from === undefined ? 0 : new Date(range.from * 1000).getUTCFullYear());
    const last = Math.min(Number(entry.lastDate.slice(0, 4)), range?.to === undefined ? 9999 : new Date(range.to * 1000).getUTCFullYear());
    const needed = Array.from({ length: Math.max(0, last - first + 1) }, (_, i) => first + i)
      .filter(year => Object.hasOwn(entry.years, String(year)));
    const results = await Promise.allSettled(needed.map(year => this.loadYear(symbol, year, entry)));
    signal?.throwIfAborted();
    const aborted = results.find(result => result.status === 'rejected' && result.reason?.name === 'AbortError');
    if (aborted?.status === 'rejected') throw aborted.reason;
    const valid = results.flatMap(result => result.status === 'fulfilled' ? [result.value] : []);
    if (!valid.length) throw new Error('此代號的封存年度檔暫時無法讀取');
    const bars = valid.flatMap(result => result.bars).filter(bar => (range?.from === undefined || bar.time >= range.from) && (range?.to === undefined || bar.time <= range.to));
    if (!bars.length) throw new Error('此區間沒有有效封存資料');
    const missing = valid.flatMap(result => result.missingVolumeAt ?? []).filter(time => bars.some(bar => bar.time === time));
    const incomplete = results.some(result => result.status === 'rejected');
    return usableBarResult({ ...valid[0]!, bars, missingVolumeAt: missing, asOf: this.now() / 1000,
      archive: { lastDate: new Date(bars.at(-1)!.time * 1000).toISOString().slice(0, 10), status: 'archive', incomplete },
      source: ARCHIVE_SOURCE + (incomplete ? ' · 部分年度暫時無法讀取' : '') }, symbol, '1D');
  }
  private loadYear(symbol: string, year: number, entry: ArchiveEntry): Promise<BarResult> {
    const current = year >= new Date(this.now()).getUTCFullYear();
    const sha = entry.years?.[String(year)]?.sha256;
    const version = `${sha ?? 'legacy-v1'}${current ? ':' + new Date(this.now()).toISOString().slice(0, 10) : ''}`;
    const key = `${symbol}:${year}:${version}`;
    let pending = this.years.get(key);
    if (!pending) {
      const load = async (): Promise<BarResult> => {
        const text = await this.request(`${entry.path}/${year}.csv`, async response => {
          if (!response.ok) throw new Error(`永久封存讀取失敗（HTTP ${response.status}）`);
          return response.text();
        });
        if (sha) {
          const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)))].map(byte => byte.toString(16).padStart(2, '0')).join('');
          if (hash !== sha) throw new Error('封存年度檔 SHA-256 不符');
        }
        return { ...parseArchiveCsv(text, symbol, year), asOf: this.now() / 1000 };
      };
      const provider = { id: 'kline-archive-year', cacheVersion: `market-open-v1:${year}:${version}`, supportedTimeframes: ['1D'] as const,
        getBars: load, getQuote: async (): Promise<Quote> => { throw new Error('Archive year has no quote endpoint'); } };
      if (this.options.cache === false) pending = load();
      else {
        const cached = new CachedMarketDataProvider(provider, {
          ...this.options.cache, now: this.now, ttlMs: current ? DAY * 1000 : 3650 * DAY * 1000,
        });
        pending = cached.getBars(symbol, '1D', { from: Date.UTC(year, 0, 1) / 1000, to: Date.UTC(year + 1, 0, 1) / 1000 - 1 })
          .finally(() => cached.close().catch(() => undefined));
      }
      this.years.set(key, pending);
      void pending.catch(() => this.years.delete(key));
      while (this.years.size > 400) this.years.delete(this.years.keys().next().value!);
    }
    return pending;
  }
}

/** Compare the same session date, independent of each provider's daily timestamp convention.
 * Existing archive rows win; only absent sessions are appended. A split uses Yahoo's whole segment.
 */
export function mergeArchiveDaily(archived: readonly Bar[], recent: readonly Bar[], symbol: string, archivedThrough?: string): { bars: Bar[]; splitMismatch: boolean } {
  const market = getMarketProfile(symbol);
  const old = new Map(archived.map(bar => [calendarDate(bar.time, market), bar]));
  const cutoff = archivedThrough ?? (archived.at(-1) ? calendarDate(archived.at(-1)!.time, market) : '');
  const splitMismatch = recent.some(bar => {
    const day = calendarDate(bar.time, market);
    const baseline = day <= cutoff ? old.get(day) : undefined;
    return baseline && (['open', 'high', 'low', 'close'] as const).some(field => Math.abs(bar[field] / baseline[field] - 1) > 0.02);
  });
  if (splitMismatch) return { bars: [...recent], splitMismatch: true };
  for (const bar of recent) {
    const day = calendarDate(bar.time, market);
    if (!old.has(day) || day > cutoff) old.set(day, bar);
  }
  const merged = [...old.values()].sort((a, b) => a.time - b.time);
  return { bars: merged, splitMismatch: false };
}

let shared: KlineArchive | undefined;
export function klineArchive(): KlineArchive { return shared ??= new KlineArchive(); }
