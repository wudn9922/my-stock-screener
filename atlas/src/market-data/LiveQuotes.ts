import type { Bar, BarResult, Quote } from './MarketDataProvider';
import { normalizeSymbol } from './MarketDataProvider';
import { calendarPeriodStart, getMarketProfile, type MarketProfile } from './MarketProfile';
import { normalizeYahooResponse, YAHOO_SOURCE } from './YahooNormalizer';
import { defaultMarketProxyUrl, EDGE_YAHOO_SOURCE } from './EdgeYahooProvider';
import { worldAssetFor } from './worldAssets';
import { calendarSessionOpen, marketCalendar, type MarketCalendarStore } from './MarketCalendar';

/**
 * Live refresh of the stocks on screen: one batched `market-chart?quotes=` request returns the last
 * month of daily rows for up to MAX_BATCH symbols. Symbols are fetched as soon as something
 * subscribes (opening a page always shows a fresh price), then once a minute while their market is
 * in session and the page is visible. Rate limiting or a failed request doubles the interval up to
 * five minutes; a success restores it.
 */
export interface LiveDaily {
  symbol: string;
  /** Validated daily rows (about one month), oldest first. */
  bars: readonly Bar[];
  quote?: Quote;
  /** Unix seconds the batch was received. */
  retrievedAt: number;
}

export type LiveListener = (value: LiveDaily) => void;

export const LIVE_INTERVAL_MS = 60_000;
export const LIVE_MAX_INTERVAL_MS = 300_000;
/** Matches MAX_QUOTE_SYMBOLS in supabase/functions/market-chart/validate.ts. */
export const MAX_BATCH = 40;
/** A new subscriber reuses a fetch younger than this instead of asking again. */
const FRESH_MS = 20_000;
/** Minutes after the close that still poll, so the final print replaces the last intraday one. */
const CLOSE_GRACE_MINUTES = 15;

const weekdayFormatters = new Map<string, Intl.DateTimeFormat>();
/** Published sessions include holidays/early closes; unavailable calendars retain the old rules. */
export function inRegularSession(profile: MarketProfile, nowMs: number, graceMinutes = CLOSE_GRACE_MINUTES): boolean {
  const known = calendarSessionOpen(marketCalendar, profile, nowMs, graceMinutes);
  if (known !== undefined) return known;
  let format = weekdayFormatters.get(profile.timezone);
  if (!format) {
    format = new Intl.DateTimeFormat('en-US', { timeZone: profile.timezone, weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
    weekdayFormatters.set(profile.timezone, format);
  }
  const parts = Object.fromEntries(format.formatToParts(new Date(nowMs)).map((part) => [part.type, part.value]));
  if (parts.weekday === 'Sat' || parts.weekday === 'Sun') return false;
  const minute = Number(parts.hour) * 60 + Number(parts.minute);
  return minute >= profile.sessionOpenMinutes && minute < profile.sessionCloseMinutes + graceMinutes;
}

/**
 * Replaces rows with the same time and appends newer ones. Older history is never touched, and
 * rows older than the existing first bar are ignored.
 */
export function mergeDailyBars(existing: readonly Bar[], recent: readonly Bar[]): Bar[] {
  if (!existing.length) return [...recent];
  const first = existing[0]!.time;
  const updates = new Map(recent.filter((bar) => bar.time >= first).map((bar) => [bar.time, bar]));
  if (!updates.size) return [...existing];
  const merged = existing.map((bar) => updates.get(bar.time) ?? bar);
  const last = existing.at(-1)!.time;
  for (const bar of recent) if (bar.time > last && updates.get(bar.time) === bar) merged.push(bar);
  return merged;
}

/**
 * Rebuilds the current week/month bar from daily rows, like the provider's calendar rebuild. Returns
 * the patched bars, or null when the daily rows do not cover the whole period (the caller keeps what it has).
 */
export function mergePeriodBars(existing: readonly Bar[], daily: readonly Bar[], timeframe: '1W' | '1M', market: MarketProfile): Bar[] | null {
  const newest = daily.at(-1);
  if (!newest || !existing.length) return null;
  const period = calendarPeriodStart(newest.time, timeframe, market);
  const rows = daily.filter((bar) => calendarPeriodStart(bar.time, timeframe, market) === period);
  // The first daily row must belong to an earlier period, so no day of this one is missing.
  if (!rows.length || calendarPeriodStart(daily[0]!.time, timeframe, market) === period) return null;
  const last = existing.at(-1)!;
  if (period < last.time) return null;
  const bar: Bar = {
    time: period,
    open: rows[0]!.open,
    high: Math.max(...rows.map((row) => row.high)),
    low: Math.min(...rows.map((row) => row.low)),
    close: rows.at(-1)!.close,
    volume: rows.reduce((sum, row) => sum + row.volume, 0),
  };
  return period === last.time ? [...existing.slice(0, -1), bar] : [...existing, bar];
}

/** Symbols this feed can serve: Yahoo stocks and indices, not the world-asset series. */
export function isLiveSymbol(symbol: string): boolean {
  try {
    return !worldAssetFor(symbol) && !!normalizeSymbol(symbol);
  } catch {
    return false;
  }
}

export function quotesUrl(proxyUrl: string, symbols: readonly string[]): string {
  const separator = proxyUrl.includes('?') ? '&' : '?';
  return `${proxyUrl}${separator}quotes=${symbols.map(encodeURIComponent).join(',')}`;
}

/** Normalizes one symbol of a batch with the same validation as the chart; invalid rows give null. */
export function normalizeLive(raw: unknown, symbol: string, retrievedAt: number): LiveDaily | null {
  try {
    const result: BarResult = normalizeYahooResponse(raw, retrievedAt, '1D', symbol);
    const quote = result.quote && { ...result.quote, market: result.market, source: (result.quote.source ?? YAHOO_SOURCE).replace(YAHOO_SOURCE, EDGE_YAHOO_SOURCE) };
    return { symbol, bars: result.bars, ...(quote ? { quote } : {}), retrievedAt };
  } catch {
    return null;
  }
}

interface Entry {
  listeners: Set<LiveListener>;
  fetchedAt: number;
  last?: LiveDaily;
}

export interface LiveQuoteFeedOptions {
  fetcher?: typeof fetch;
  now?: () => number;
  /** Defaults to document visibility. */
  visible?: () => boolean;
  inSession?: (symbol: string, nowMs: number) => boolean;
  calendar?: MarketCalendarStore;
}

export class LiveQuoteFeed {
  private readonly entries = new Map<string, Entry>();
  private readonly fetcher: typeof fetch;
  private readonly now: () => number;
  private readonly visible: () => boolean;
  private readonly inSession: (symbol: string, nowMs: number) => boolean;
  private readonly calendar: MarketCalendarStore;
  private readonly loadCalendar: boolean;
  private interval = LIVE_INTERVAL_MS;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private kickTimer: ReturnType<typeof setTimeout> | null = null;
  private running: Promise<void> | null = null;
  private onVisibility: (() => void) | null = null;
  private readonly updateListeners = new Set<(atMs: number) => void>();
  /** Milliseconds of the last batch that came back (0 before the first). */
  lastUpdatedAt = 0;

  constructor(readonly proxyUrl: string, options: LiveQuoteFeedOptions = {}) {
    this.fetcher = options.fetcher ?? ((input, init) => fetch(input, init));
    this.now = options.now ?? Date.now;
    this.visible = options.visible ?? (() => typeof document === 'undefined' || !document.hidden);
    this.calendar = options.calendar ?? marketCalendar;
    this.loadCalendar = !options.inSession;
    this.inSession = options.inSession ?? ((symbol, nowMs) => calendarSessionOpen(this.calendar, getMarketProfile(symbol), nowMs)
      ?? inRegularSession(getMarketProfile(symbol), nowMs));
  }

  get currentInterval(): number {
    return this.interval;
  }

  /** Called with the receive time of every batch that came back, for an "updated at" label. */
  onUpdate(listener: (atMs: number) => void): () => void {
    this.updateListeners.add(listener);
    return () => this.updateListeners.delete(listener);
  }

  /** Starts live updates for a symbol; the returned function stops them. */
  subscribe(symbolInput: string, listener: LiveListener): () => void {
    if (!this.proxyUrl || !isLiveSymbol(symbolInput)) return () => undefined;
    const symbol = normalizeSymbol(symbolInput);
    let entry = this.entries.get(symbol);
    if (!entry) this.entries.set(symbol, (entry = { listeners: new Set(), fetchedAt: 0 }));
    entry.listeners.add(listener);
    const last = entry.last;
    if (last) queueMicrotask(() => entry!.listeners.has(listener) && listener(last));
    this.start();
    this.kick();
    return () => {
      entry!.listeners.delete(listener);
      if (!entry!.listeners.size) this.entries.delete(symbol);
      if (!this.entries.size) this.stop();
    };
  }

  /** Fetches subscribers that have nothing recent (new symbols), whatever the session. */
  private kick() {
    if (this.kickTimer) return;
    this.kickTimer = setTimeout(() => {
      this.kickTimer = null;
      void this.poll(false);
    }, 250);
  }

  private start() {
    if (this.timer) return;
    this.schedule();
    if (typeof document !== 'undefined' && !this.onVisibility) {
      this.onVisibility = () => {
        if (this.visible()) void this.poll(true);
      };
      document.addEventListener('visibilitychange', this.onVisibility);
    }
  }

  private stop() {
    if (this.timer) clearTimeout(this.timer);
    if (this.kickTimer) clearTimeout(this.kickTimer);
    this.timer = this.kickTimer = null;
    if (this.onVisibility) document.removeEventListener('visibilitychange', this.onVisibility);
    this.onVisibility = null;
  }

  private schedule() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.poll(true).finally(() => this.entries.size && this.schedule());
    }, this.interval);
  }

  /** `periodic`: also refresh in-session symbols older than the interval. */
  poll(periodic: boolean): Promise<void> {
    if (this.running) return this.running;
    this.running = this.run(periodic).finally(() => {
      this.running = null;
    });
    return this.running;
  }

  private async run(periodic: boolean) {
    if (!this.visible()) return;
    if (this.loadCalendar) await this.calendar.ensure(this.now());
    if (!this.visible()) return;
    const now = this.now();
    const due = [...this.entries].filter(([symbol, entry]) =>
      now - entry.fetchedAt >= FRESH_MS &&
      // Opening still reads the last close once. Known closed sessions never
      // retry periodically, including an unsuccessful first quote request.
      (!periodic || !this.loadCalendar || calendarSessionOpen(this.calendar, getMarketProfile(symbol), now) !== false) &&
      (!entry.fetchedAt || (periodic && now - entry.fetchedAt >= this.interval - 5000 && this.inSession(symbol, now))))
      .map(([symbol]) => symbol);
    let failed = false;
    for (let i = 0; i < due.length; i += MAX_BATCH) {
      const batch = due.slice(i, i + MAX_BATCH);
      for (const symbol of batch) {
        const entry = this.entries.get(symbol);
        if (entry) entry.fetchedAt = now;
      }
      try {
        const response = await this.fetcher(quotesUrl(this.proxyUrl, batch), { headers: { Accept: 'application/json' } });
        if (!response.ok) throw new Error(`quotes ${response.status}`);
        const body = (await response.json()) as { results?: Record<string, unknown>; errors?: Record<string, string> };
        if (Object.values(body.errors ?? {}).includes('rate_limited')) failed = true;
        const retrievedAt = this.now() / 1000;
        this.lastUpdatedAt = retrievedAt * 1000;
        for (const listener of [...this.updateListeners]) listener(this.lastUpdatedAt);
        for (const symbol of batch) {
          const raw = body.results?.[symbol];
          const value = raw === undefined ? null : normalizeLive(raw, symbol, retrievedAt);
          const entry = this.entries.get(symbol);
          if (!value || !entry) continue;
          entry.last = value;
          for (const listener of [...entry.listeners]) listener(value);
        }
      } catch {
        // Retried on the next tick even outside the session (an unanswered first fetch counts as none).
        for (const symbol of batch) {
          const entry = this.entries.get(symbol);
          if (entry && !entry.last) entry.fetchedAt = 0;
        }
        failed = true;
        break;
      }
    }
    if (!due.length) return;
    const next = failed ? Math.min(this.interval * 2, LIVE_MAX_INTERVAL_MS) : LIVE_INTERVAL_MS;
    if (next !== this.interval) {
      this.interval = next;
      if (this.timer) this.schedule();
    }
  }
}

let shared: LiveQuoteFeed | null = null;
/** The site-wide feed (one poll loop for every page). */
export function liveQuotes(): LiveQuoteFeed {
  shared ??= new LiveQuoteFeed(defaultMarketProxyUrl());
  return shared;
}

/** Same times and OHLCV, bar for bar. */
export function sameBars(a: readonly Bar[], b: readonly Bar[]): boolean {
  return a.length === b.length && a.every((bar, i) => {
    const other = b[i]!;
    return bar.time === other.time && bar.open === other.open && bar.high === other.high &&
      bar.low === other.low && bar.close === other.close && bar.volume === other.volume;
  });
}

/** "12:35:09" in the viewer's clock, for the live "updated at" labels. */
export function formatLiveTime(atMs: number): string {
  return new Date(atMs).toLocaleTimeString('zh-TW', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
