import { z } from 'zod';

/** Existing US ticker grammar plus explicit Taiwan listed/OTC Yahoo symbols and Yahoo `^` indices. */
export const US_SYMBOL_REGEX = /^[A-Z][A-Z0-9.^-]{0,14}$/;
export const TAIWAN_SYMBOL_REGEX = /^[0-9]{4,6}[A-Z]?\.(?:TW|TWO)$/;
/** Yahoo index symbols such as ^TWII, ^TWOII, ^GSPC, ^SOX (my-stock-screener integration). */
export const INDEX_SYMBOL_REGEX = /^\^[A-Z0-9][A-Z0-9.-]{0,14}$/;
/** Shanghai/Shenzhen index codes as Yahoo quotes them (000001.SS 上證, 399001.SZ 深證), used by the world-index page. */
export const CHINA_INDEX_SYMBOL_REGEX = /^[0-9]{6}\.(?:SS|SZ)$/;
export const CANONICAL_SYMBOL_REGEX =
  /^(?:[A-Z][A-Z0-9.^-]{0,14}|[0-9]{4,6}[A-Z]?\.(?:TW|TWO)|\^[A-Z0-9][A-Z0-9.-]{0,14}|[0-9]{6}\.(?:SS|SZ))$/;
/**
 * Taiwan indices quoted by Yahoo in TWD on Asia/Taipei session time. Every other `^` index keeps the
 * US profile; an index whose Yahoo metadata disagrees with its profile is rejected, never relabelled.
 */
export const TAIWAN_INDEX_EXCHANGES: Readonly<Record<string, 'TWSE' | 'TPEx'>> = Object.freeze({
  '^TWII': 'TWSE',
  '^TWOII': 'TPEx',
});

export function isIndexSymbol(symbol: string): boolean {
  const normalized = symbol.trim().toUpperCase();
  return INDEX_SYMBOL_REGEX.test(normalized) || CHINA_INDEX_SYMBOL_REGEX.test(normalized);
}

export interface MarketProfile {
  market: 'US' | 'TW';
  currency: 'USD' | 'TWD';
  exchange: 'US' | 'TWSE' | 'TPEx';
  timezone: 'America/New_York' | 'Asia/Taipei';
  sessionOpenMinutes: 570 | 540;
  sessionCloseMinutes: 960 | 810;
}

export const marketProfileSchema = z.object({
  market: z.enum(['US', 'TW']),
  currency: z.enum(['USD', 'TWD']),
  exchange: z.enum(['US', 'TWSE', 'TPEx']),
  timezone: z.enum(['America/New_York', 'Asia/Taipei']),
  sessionOpenMinutes: z.union([z.literal(570), z.literal(540)]),
  sessionCloseMinutes: z.union([z.literal(960), z.literal(810)]),
}).strict();

const US_PROFILE: MarketProfile = Object.freeze({
  market: 'US',
  currency: 'USD',
  exchange: 'US',
  timezone: 'America/New_York',
  sessionOpenMinutes: 570,
  sessionCloseMinutes: 960,
});
const TWSE_PROFILE: MarketProfile = Object.freeze({
  market: 'TW',
  currency: 'TWD',
  exchange: 'TWSE',
  timezone: 'Asia/Taipei',
  sessionOpenMinutes: 540,
  sessionCloseMinutes: 810,
});
const TPEX_PROFILE: MarketProfile = Object.freeze({
  market: 'TW',
  currency: 'TWD',
  exchange: 'TPEx',
  timezone: 'Asia/Taipei',
  sessionOpenMinutes: 540,
  sessionCloseMinutes: 810,
});

export function isCanonicalSymbol(value: string): boolean {
  return CANONICAL_SYMBOL_REGEX.test(value);
}

export function getMarketProfile(symbol?: string): MarketProfile {
  if (!symbol) return US_PROFILE;
  const normalized = symbol.trim().toUpperCase();
  const taiwanIndex = TAIWAN_INDEX_EXCHANGES[normalized];
  if (taiwanIndex) return taiwanIndex === 'TPEx' ? TPEX_PROFILE : TWSE_PROFILE;
  if (!TAIWAN_SYMBOL_REGEX.test(normalized)) return US_PROFILE;
  return normalized.endsWith('.TWO') ? TPEX_PROFILE : TWSE_PROFILE;
}

const datePartsFormatters = new Map<string, Intl.DateTimeFormat>();
function formatter(timezone: MarketProfile['timezone']): Intl.DateTimeFormat {
  let format = datePartsFormatters.get(timezone);
  if (!format) {
    format = new Intl.DateTimeFormat('en-US', {
      timeZone: timezone,
      weekday: 'short',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    });
    datePartsFormatters.set(timezone, format);
  }
  return format;
}

function partsAt(time: number, profile: MarketProfile): Record<string, string> {
  return Object.fromEntries(
    formatter(profile.timezone)
      .formatToParts(new Date(time * 1000))
      .map((part) => [part.type, part.value]),
  );
}

/** Calendar date in the symbol's exchange timezone. */
export function sessionDate(time: number, profile: MarketProfile): string {
  if (!Number.isFinite(time)) throw new Error('Invalid market timestamp');
  const parts = partsAt(time, profile);
  return `${parts.year}-${parts.month}-${parts.day}`;
}

/** Calendar date paired with the canonical bar bucket convention for a market. */
export function calendarDate(time: number, profile: MarketProfile): string {
  if (!Number.isFinite(time)) throw new Error('Invalid market timestamp');
  return profile.market === 'US'
    ? new Date(time * 1000).toISOString().slice(0, 10)
    : sessionDate(time, profile);
}

export interface SessionCloseBar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export const SESSION_CLOSE_SOURCE_QUALIFIER = 'source-reported session-close observations';

/** True only for Yahoo's observed flat, zero-volume Taiwan terminal close row. */
export function isSessionCloseObservation(bar: SessionCloseBar, profile: MarketProfile): boolean {
  if (
    profile.market !== 'TW' || profile.timezone !== 'Asia/Taipei' || !Number.isFinite(bar.time) ||
    bar.open !== bar.high || bar.open !== bar.low || bar.open !== bar.close || bar.volume !== 0
  ) return false;
  const parts = partsAt(bar.time, profile);
  return ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'].includes(parts.weekday) &&
    Number(parts.hour) === 13 && Number(parts.minute) === 30 && Number(parts.second) === 0;
}

/** Convert a local market midnight to Unix seconds, including zone offset rules. */
function marketMidnightEpoch(year: number, month: number, day: number, profile: MarketProfile): number {
  const wanted = Date.UTC(year, month - 1, day);
  let guess = wanted;
  for (let iteration = 0; iteration < 3; iteration++) {
    const parts = partsAt(guess / 1000, profile);
    const observed = Date.UTC(
      Number(parts.year),
      Number(parts.month) - 1,
      Number(parts.day),
      Number(parts.hour),
      Number(parts.minute),
      Number(parts.second),
    );
    guess += wanted - observed;
  }
  return guess / 1000;
}

/** Canonical weekly/monthly anchor; US keeps its historical UTC bucket convention. */
export function calendarPeriodStart(time: number, timeframe: '1W' | '1M', profile: MarketProfile): number {
  if (!Number.isFinite(time)) throw new Error('Invalid market timestamp');
  if (profile.market === 'US') {
    const date = new Date(time * 1000);
    if (timeframe === '1M') return Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1) / 1000;
    return Date.UTC(
      date.getUTCFullYear(),
      date.getUTCMonth(),
      date.getUTCDate() - (date.getUTCDay() + 6) % 7,
    ) / 1000;
  }

  const [year, month, day] = sessionDate(time, profile).split('-').map(Number);
  const localDate = new Date(Date.UTC(year, month - 1, day));
  if (timeframe === '1M') return marketMidnightEpoch(localDate.getUTCFullYear(), localDate.getUTCMonth() + 1, 1, profile);
  const mondayOffset = (localDate.getUTCDay() + 6) % 7;
  localDate.setUTCDate(localDate.getUTCDate() - mondayOffset);
  return marketMidnightEpoch(localDate.getUTCFullYear(), localDate.getUTCMonth() + 1, localDate.getUTCDate(), profile);
}

export function sameMarketProfile(actual: unknown, expected: MarketProfile): boolean {
  const parsed = marketProfileSchema.safeParse(actual);
  return parsed.success && Object.keys(expected).every((key) => parsed.data[key as keyof MarketProfile] === expected[key as keyof MarketProfile]);
}
