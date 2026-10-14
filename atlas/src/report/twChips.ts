import { z } from 'zod';
import { reportUrl } from './loadReport';

/**
 * Taiwan chips (`report/tw-chips.json`, written by scripts/build_tw_chips.py): foreign ownership ratio and
 * total shareholder count per stock. See that script's docstring for the file format.
 *
 * Tolerant like breadth.ts: unknown fields are ignored, numbers may arrive as strings, and one broken
 * stock entry only empties that stock (entries are parsed lazily, per symbol).
 */

export const CHIP_SOURCE_KEYS = ['twse', 'tpex', 'tdcc'] as const;
export type ChipSourceKey = (typeof CHIP_SOURCE_KEYS)[number];
export type ChipSourceStatus = 'ok' | 'stale' | 'unavailable' | 'unknown';

/** A symbol the chips file can hold: 4-digit code with a .TW / .TWO suffix. */
export const CHIPS_SYMBOL_REGEX = /^[1-9]\d{3}\.(TW|TWO)$/;
/** Chips are shown for every Taiwan stock page (ETFs and unknown codes get an empty state). */
export const isChipsPageSymbol = (symbol: string | undefined | null): boolean => /^\d{4,6}[A-Z]?\.(TW|TWO)$/.test((symbol ?? '').toUpperCase());

/** Foreign ownership older than this many weekdays is flagged; shareholder counts (weekly) older than this many days. */
export const FOREIGN_STALE_WEEKDAYS = 5;
export const HOLDERS_STALE_DAYS = 10;

const finiteOrNull = (value: unknown): number | null => {
  const number = typeof value === 'string' && value.trim() !== '' ? Number(value) : value;
  return typeof number === 'number' && Number.isFinite(number) ? number : null;
};
const isoDateOrNull = (value: unknown): string | null => (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(value)) ? value : null);
const optionalText = z
  .unknown()
  .optional()
  .transform((value) => (typeof value === 'string' && value.trim() ? value.trim() : null));

const sourceSchema = z
  .object({
    label: optionalText,
    asOf: z.unknown().optional().transform(isoDateOrNull),
    status: z
      .unknown()
      .optional()
      .transform((value): ChipSourceStatus => (value === 'ok' || value === 'stale' || value === 'unavailable' ? value : 'unknown')),
    message: optionalText,
  })
  .transform((source) => ({ label: source.label, asOf: source.asOf, status: source.status, message: source.message }));

export interface ChipSource {
  label: string | null;
  asOf: string | null;
  status: ChipSourceStatus;
  message: string | null;
}

const stringArray = (value: unknown): string[] | null => {
  if (!Array.isArray(value)) return null;
  const days = value.map(isoDateOrNull);
  return days.every((day): day is string => day !== null) ? days : null;
};

export const twChipsSchema = z.object({
  generatedAt: optionalText,
  sources: z
    .unknown()
    .optional()
    .transform((value) => {
      const record = value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
      const out: Partial<Record<ChipSourceKey, ChipSource>> = {};
      for (const key of CHIP_SOURCE_KEYS) {
        const parsed = sourceSchema.safeParse(record[key]);
        if (parsed.success) out[key] = parsed.data;
      }
      return out;
    }),
  axes: z
    .unknown()
    .optional()
    .transform((value) => {
      const axes = value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
      const foreign = axes.foreign && typeof axes.foreign === 'object' ? (axes.foreign as Record<string, unknown>) : {};
      return {
        foreignTwse: stringArray(foreign.twse) ?? [],
        foreignTpex: stringArray(foreign.tpex) ?? [],
        holders: stringArray(axes.holders) ?? [],
      };
    }),
  stocks: z.unknown().optional().transform((value) => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {})),
});

export interface ChipsFile {
  generatedAt: string | null;
  sources: Partial<Record<ChipSourceKey, ChipSource>>;
  axes: { foreignTwse: string[]; foreignTpex: string[]; holders: string[] };
  /** Raw per-symbol entries; use `chipsForSymbol` to read one. */
  stocks: Record<string, unknown>;
}

export interface ForeignChips {
  pct: number;
  asOf: string;
  /** Change versus the previous trading day with data, in percentage points. */
  d1: number | null;
  d1Date: string | null;
  m1: number | null;
  m1Date: string | null;
  dates: string[];
  series: (number | null)[];
}
export interface HolderChips {
  count: number;
  asOf: string;
  wow: number | null;
  wowDate: string | null;
  dates: string[];
  series: (number | null)[];
}
export interface StockChips {
  foreign: ForeignChips | null;
  holders: HolderChips | null;
  foreignSourceKey: 'twse' | 'tpex';
  foreignSource: ChipSource | null;
  holdersSource: ChipSource | null;
}

/** Aligns a series to the end of its date axis; a length mismatch trims the older side instead of misplacing points. */
function align(axis: readonly string[], raw: unknown, clean: (value: unknown) => number | null): { dates: string[]; series: (number | null)[] } {
  const values = Array.isArray(raw) ? raw : [];
  const length = Math.min(axis.length, values.length);
  return { dates: axis.slice(axis.length - length), series: values.slice(values.length - length).map(clean) };
}

const percentOrNull = (value: unknown): number | null => {
  const number = finiteOrNull(value);
  return number !== null && number >= 0 && number <= 100 ? number : null;
};
const countOrNull = (value: unknown): number | null => {
  const number = finiteOrNull(value);
  return number !== null && number >= 0 && Number.isInteger(number) ? number : null;
};

const foreignSchema = z.object({
  pct: z.unknown().transform(percentOrNull),
  asOf: z.unknown().transform(isoDateOrNull),
  d1: z.unknown().optional().transform(finiteOrNull),
  d1Date: z.unknown().optional().transform(isoDateOrNull),
  m1: z.unknown().optional().transform(finiteOrNull),
  m1Date: z.unknown().optional().transform(isoDateOrNull),
  series: z.unknown().optional(),
});
const holdersSchema = z.object({
  count: z.unknown().transform(countOrNull),
  asOf: z.unknown().transform(isoDateOrNull),
  wow: z.unknown().optional().transform(finiteOrNull),
  wowDate: z.unknown().optional().transform(isoDateOrNull),
  series: z.unknown().optional(),
});

/** One stock's chips; every part that is missing or malformed comes back `null`. Never throws. */
export function chipsForSymbol(data: ChipsFile, symbol: string): StockChips {
  const key = symbol.trim().toUpperCase();
  const foreignSourceKey = key.endsWith('.TWO') ? 'tpex' : 'twse';
  const out: StockChips = {
    foreign: null,
    holders: null,
    foreignSourceKey,
    foreignSource: data.sources[foreignSourceKey] ?? null,
    holdersSource: data.sources.tdcc ?? null,
  };
  const entry = data.stocks[key];
  if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return out;
  const record = entry as Record<string, unknown>;

  const foreign = foreignSchema.safeParse(record.foreign);
  if (foreign.success && foreign.data.pct !== null && foreign.data.asOf) {
    const axis = foreignSourceKey === 'tpex' ? data.axes.foreignTpex : data.axes.foreignTwse;
    const { dates, series } = align(axis, foreign.data.series, percentOrNull);
    out.foreign = {
      pct: foreign.data.pct,
      asOf: foreign.data.asOf,
      d1: foreign.data.d1,
      d1Date: foreign.data.d1Date,
      m1: foreign.data.m1,
      m1Date: foreign.data.m1Date,
      dates,
      series,
    };
  }
  const holders = holdersSchema.safeParse(record.holders);
  if (holders.success && holders.data.count !== null && holders.data.asOf) {
    const { dates, series } = align(data.axes.holders, holders.data.series, countOrNull);
    out.holders = {
      count: holders.data.count,
      asOf: holders.data.asOf,
      wow: holders.data.wow,
      wowDate: holders.data.wowDate,
      dates,
      series,
    };
  }
  return out;
}

export type TwChipsLoadResult =
  | { status: 'ok'; data: ChipsFile; url: string }
  | { status: 'missing' | 'invalid' | 'error'; message: string; url: string };

/** `tw-chips.json` sits next to `latest.json` (or next to `VITE_REPORT_URL`). */
export function twChipsUrl(
  base: string = import.meta.env?.BASE_URL ?? '/',
  override: string | undefined = import.meta.env?.VITE_REPORT_URL,
  origin: string = typeof location === 'undefined' ? 'http://localhost' : location.origin,
): string {
  return new URL('tw-chips.json', reportUrl(base, override, origin)).href;
}

/** Parses an already-fetched body. Never throws. */
export function parseTwChips(data: unknown, url = ''): TwChipsLoadResult {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return { status: 'invalid', message: '籌碼資料格式無法辨識。', url };
  const parsed = twChipsSchema.safeParse(data);
  if (!parsed.success) return { status: 'invalid', message: '籌碼資料格式無法辨識。', url };
  if (!Object.keys(parsed.data.stocks).length) return { status: 'missing', message: '籌碼資料尚未產生。', url };
  return { status: 'ok', data: parsed.data, url };
}

/** Fetches and validates the file. Network, HTTP and JSON failures resolve to a friendly state. */
export async function fetchTwChips(url = twChipsUrl(), fetcher: typeof fetch = fetch, signal?: AbortSignal): Promise<TwChipsLoadResult> {
  let response: Response;
  try {
    response = await fetcher(url, { cache: 'no-cache', signal });
  } catch {
    return { status: 'error', message: '無法連線取得籌碼資料，請稍後再試。', url };
  }
  if (response.status === 404) return { status: 'missing', message: '籌碼資料尚未產生。', url };
  if (!response.ok) return { status: 'error', message: `籌碼資料暫時無法取得（HTTP ${response.status}）。`, url };
  let data: unknown;
  try {
    // An SPA fallback can answer with index.html; treat non-JSON as an unreadable file.
    data = JSON.parse(await response.text());
  } catch {
    return { status: 'invalid', message: '籌碼資料格式無法辨識。', url };
  }
  return parseTwChips(data, url);
}

let shared: Promise<TwChipsLoadResult> | null = null;

/** One shared load per page session; `refresh` forces a new request. */
export function loadTwChips(refresh = false): Promise<TwChipsLoadResult> {
  if (!shared || refresh) {
    const request = fetchTwChips().then((result) => {
      // Do not pin a transient failure for the rest of the session; the next mount retries.
      if (result.status === 'error' && shared === request) shared = null;
      return result;
    });
    shared = request;
  }
  return shared;
}

// -- Staleness and formatting (pure; `today` is injectable for tests)

const isoOf = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
const dayNumber = (iso: string) => Math.floor(Date.parse(`${iso}T00:00:00Z`) / 86_400_000);

/** Monday–Friday days in (from, to]; holidays are not known here, so this over-counts after long closures. */
export function weekdaysBetween(from: string, to: string): number {
  const start = dayNumber(from);
  const end = dayNumber(to);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return 0;
  let count = 0;
  for (let day = start + 1; day <= end; day++) {
    const weekday = new Date(day * 86_400_000).getUTCDay();
    if (weekday !== 0 && weekday !== 6) count++;
  }
  return count;
}
export const isForeignStale = (asOf: string, today: Date = new Date()): boolean => weekdaysBetween(asOf, isoOf(today)) > FOREIGN_STALE_WEEKDAYS;
export const isHoldersStale = (asOf: string, today: Date = new Date()): boolean => dayNumber(isoOf(today)) - dayNumber(asOf) > HOLDERS_STALE_DAYS;

/** `2026-10-14` → `10/14`. */
export const monthDay = (iso: string | null): string => (iso && /^\d{4}-(\d{2})-(\d{2})$/.test(iso) ? `${iso.slice(5, 7)}/${iso.slice(8, 10)}` : '—');

/** `+0.03 個百分點`, `-0.20 個百分點`, `0.00 個百分點`; null → em dash. */
export function formatPoints(value: number | null, digits = 2): string {
  if (value === null || !Number.isFinite(value)) return '—';
  const rounded = Number(value.toFixed(digits));
  return `${rounded > 0 ? '+' : ''}${rounded.toFixed(digits)} 個百分點`;
}
/** `1,234,567`. */
export const formatCount = (value: number | null): string => (value === null || !Number.isFinite(value) ? '—' : Math.round(value).toLocaleString('en-US'));
/** `+1,234 人（+0.10%）`; the percentage is relative to the previous week. */
export function formatCountChange(change: number | null, latest: number): string {
  if (change === null || !Number.isFinite(change)) return '—';
  const sign = change > 0 ? '+' : change < 0 ? '-' : '';
  const base = latest - change;
  const pct = base > 0 ? `（${change > 0 ? '+' : change < 0 ? '-' : ''}${Math.abs((change / base) * 100).toFixed(2)}%）` : '';
  return `${sign}${Math.abs(Math.round(change)).toLocaleString('en-US')} 人${pct}`;
}

export interface Sparkline {
  /** SVG path over a `width` × `height` box; a missing value lifts the pen. */
  d: string;
  min: number;
  max: number;
  points: number;
  last: { x: number; y: number };
}

/** Index-spaced polyline scaled to the series' own range (not 0–100): this card shows the shape, labels give the numbers. */
export function sparkline(values: readonly (number | null)[], width = 300, height = 64, pad = 4): Sparkline | null {
  const numbers = values.filter((value): value is number => value !== null && Number.isFinite(value));
  if (numbers.length < 2) return null;
  const min = Math.min(...numbers);
  const max = Math.max(...numbers);
  const span = max - min;
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  const round1 = (value: number) => Math.round(value * 10) / 10;
  const y = (value: number) => (span === 0 ? height / 2 : pad + (1 - (value - min) / span) * (height - pad * 2));
  const parts: string[] = [];
  let pen: 'M' | 'L' = 'M';
  let last = { x: 0, y: height / 2 };
  values.forEach((value, index) => {
    if (value === null || !Number.isFinite(value)) {
      pen = 'M';
      return;
    }
    last = { x: round1(index * step), y: round1(y(value)) };
    parts.push(`${pen}${last.x},${last.y}`);
    pen = 'L';
  });
  return { d: parts.join(' '), min, max, points: numbers.length, last };
}
