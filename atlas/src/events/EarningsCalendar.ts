import { z } from 'zod';
import { getMarketProfile, isIndexSymbol } from '../market-data/MarketProfile';
import { normalizeSymbol } from '../market-data/MarketDataProvider';

/**
 * Next earnings date of each US stock: `${BASE_URL}valuation/us-events.json` (scripts/build_events_us.py,
 * Yahoo Finance). Taiwan has no such file by design: TWSE / TPEx publish no per-company date in advance.
 */
export interface EarningsEvent {
  symbol: string;
  /** YYYY-MM-DD, a US Eastern calendar date. */
  earningsDate: string;
  /** Yahoo's timestamp in Unix seconds, when given. */
  earningsAt: number | null;
  /** [first, last] of Yahoo's estimated date range; null for a single date. */
  window: [string, string] | null;
  /** Yahoo has not confirmed the date yet. */
  estimate: boolean;
  /** bmo: before the open; amc: after the close; null when the time of day is unknown. */
  session: 'bmo' | 'amc' | null;
  /** When the file was built (ms since epoch); null when the file has no usable timestamp. */
  generatedAt: number | null;
}

export type EarningsTone = 'soon' | 'later';

export interface EarningsBadge {
  days: number;
  /** 「今天財報」「明天財報」「N 天後財報」 */
  text: string;
  /** Within a week the badge uses the warning colour. */
  tone: EarningsTone;
  /** Tooltip: date (US Eastern), session and estimate. */
  title: string;
}

/** A file older than this hides every badge (the builder runs with each Atlas build). */
export const EARNINGS_STALE_DAYS = 4;
/** Badges show from 0 to this many days ahead. */
export const EARNINGS_WINDOW_DAYS = 21;
export const EARNINGS_SOON_DAYS = 7;

const DAY_MS = 86_400_000;
const EASTERN_DAY = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

/** Today's calendar date in US Eastern time (YYYY-MM-DD) at the instant `now`. */
export function easternDate(now: number): string {
  const parts = EASTERN_DAY.formatToParts(new Date(now));
  const part = (type: string) => parts.find((item) => item.type === type)?.value ?? '';
  return `${part('year')}-${part('month')}-${part('day')}`;
}

function dayNumber(isoDate: string): number {
  const [year, month, day] = isoDate.split('-').map(Number);
  return Math.round(Date.UTC(year, month - 1, day) / DAY_MS);
}

/** Calendar days from today (US Eastern) to `earningsDate`: 0 today, 1 tomorrow, negative once passed. */
export function daysUntilEarnings(earningsDate: string, now: number): number {
  return dayNumber(earningsDate) - dayNumber(easternDate(now));
}

const slashDate = (isoDate: string) => isoDate.replaceAll('-', '/');

/**
 * The badge for one stock, or null when nothing should show: no record, the file is stale, the date has
 * passed or it is more than 21 days away.
 */
export function earningsBadge(event: EarningsEvent | null, now: number): EarningsBadge | null {
  if (!event || !Number.isFinite(now)) return null;
  if (event.generatedAt === null || now - event.generatedAt > EARNINGS_STALE_DAYS * DAY_MS) return null;
  const days = daysUntilEarnings(event.earningsDate, now);
  if (days < 0 || days > EARNINGS_WINDOW_DAYS) return null;
  const text = days === 0 ? '今天財報' : days === 1 ? '明天財報' : `${days} 天後財報`;
  const details = [`${slashDate(event.earningsDate)}（美東）`];
  if (event.session === 'bmo') details.push('盤前公布');
  if (event.session === 'amc') details.push('盤後公布');
  if (event.estimate) details.push('預估日期，尚未確認');
  if (event.window) details.push(`預估區間 ${slashDate(event.window[0])}–${slashDate(event.window[1])}`);
  return {
    days,
    text,
    tone: days <= EARNINGS_SOON_DAYS ? 'soon' : 'later',
    title: details.join(' · '),
  };
}

const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const itemSchema = z.object({
  earningsDate: isoDate,
  earningsAt: z.number().finite().nullable().optional(),
  window: z.tuple([isoDate, isoDate]).nullable().optional(),
  estimate: z.boolean().optional(),
  session: z.enum(['bmo', 'amc']).nullable().optional(),
});
const documentSchema = z.object({
  version: z.literal(1),
  market: z.literal('US'),
  generatedAt: z.string().optional(),
  items: z.record(z.string(), z.unknown()),
});

export interface EarningsFile {
  generatedAt: number | null;
  items: Readonly<Record<string, unknown>>;
}

export interface EarningsCalendarOptions {
  fetcher?: typeof fetch;
  now?: () => number;
}

const RETRY_AFTER_FAILURE_MS = 60_000;
/** The file is rebuilt several times a day; reuse a loaded copy for this long. */
const RELOAD_AFTER_MS = 30 * 60_000;

interface FileEntry {
  at: number;
  failed: boolean;
  promise: Promise<EarningsFile | null>;
}

export class EarningsCalendar {
  readonly url: string;
  private readonly fetcher: typeof fetch;
  private readonly now: () => number;
  private entry: FileEntry | null = null;
  /** Last successfully loaded file, served while a reload fails. */
  private lastGood: EarningsFile | null = null;

  constructor(base = `${import.meta.env?.BASE_URL ?? '/'}valuation/`, options: EarningsCalendarOptions = {}) {
    const normalized = base.endsWith('/') ? base : `${base}/`;
    this.url = `${normalized}us-events.json`;
    this.fetcher = options.fetcher ?? ((input, init) => fetch(input, init));
    this.now = options.now ?? Date.now;
  }

  /** The earnings record for a US stock, or null (Taiwan, indices, unknown symbol, file missing or invalid). */
  async getEarnings(symbolInput: string, signal?: AbortSignal): Promise<EarningsEvent | null> {
    signal?.throwIfAborted();
    let symbol: string;
    try {
      symbol = normalizeSymbol(symbolInput);
    } catch {
      return null;
    }
    if (isIndexSymbol(symbol) || getMarketProfile(symbol).market !== 'US') return null;
    const file = await this.loadFile();
    signal?.throwIfAborted();
    if (!file || !Object.hasOwn(file.items, symbol)) return null;
    const parsed = itemSchema.safeParse(file.items[symbol]);
    if (!parsed.success) return null;
    const record = parsed.data;
    return {
      symbol,
      earningsDate: record.earningsDate,
      earningsAt: record.earningsAt ?? null,
      window: record.window ?? null,
      estimate: record.estimate ?? false,
      session: record.session ?? null,
      generatedAt: file.generatedAt,
    };
  }

  /** The whole file, reused for 30 minutes; a failed load is retried after a minute. */
  loadFile(): Promise<EarningsFile | null> {
    const cached = this.entry;
    if (cached && this.now() - cached.at < (cached.failed ? RETRY_AFTER_FAILURE_MS : RELOAD_AFTER_MS)) return cached.promise;
    const entry: FileEntry = { at: this.now(), failed: false, promise: Promise.resolve(null) };
    entry.promise = (async (): Promise<EarningsFile | null> => {
      try {
        const response = await this.fetcher(this.url, { cache: 'no-cache' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const document = documentSchema.parse(await response.json());
        const built = document.generatedAt ? Date.parse(document.generatedAt) : Number.NaN;
        const file: EarningsFile = { generatedAt: Number.isFinite(built) ? built : null, items: document.items };
        this.lastGood = file;
        return file;
      } catch {
        // Not built yet, offline or invalid: keep the last good copy and retry after a minute.
        entry.failed = true;
        entry.at = this.now();
        return this.lastGood;
      }
    })();
    this.entry = entry;
    return entry.promise;
  }
}

let defaultCalendar: EarningsCalendar | null = null;

/** Shared calendar reading `${BASE_URL}valuation/us-events.json`. */
export function getEarnings(symbol: string, signal?: AbortSignal): Promise<EarningsEvent | null> {
  defaultCalendar ??= new EarningsCalendar();
  return defaultCalendar.getEarnings(symbol, signal);
}
