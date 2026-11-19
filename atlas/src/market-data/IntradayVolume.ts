import type { Bar, BarResult } from './MarketDataProvider';
import { calendarDate, getMarketProfile, isIndexSymbol, sessionDate, type MarketProfile } from './MarketProfile';

/**
 * Today's index volume rebuilt from the same source's 5-minute bars, accepted only when that source's
 * completed earlier sessions add up to the daily bars' volume (same unit as the history). Yahoo leaves
 * ^TWII's daily volume at 0 until after the session and TWSE MIS index rows carry no share volume, so
 * without this the live daily bar has no volume. Nothing is estimated when the units cannot be checked.
 */
export type IntradayIndexVolume =
  | { status: 'unavailable'; reason: string }
  | { status: 'ok'; date: string; volume: number; ratio: number; checkedSessions: number };

const STEP_MINUTES = 5;
/** Loose on purpose: it rejects unit mismatches (×1000 lots, trade value), not auction rounding. */
export const UNIT_TOLERANCE = { min: 0.8, max: 1.25 } as const;
const MIN_CHECKED_SESSIONS = 2;
const MAX_CHECKED_SESSIONS = 5;

const clocks = new Map<string, Intl.DateTimeFormat>();
function minuteOfDay(time: number, profile: MarketProfile): number {
  let clock = clocks.get(profile.timezone);
  if (!clock) {
    clock = new Intl.DateTimeFormat('en-GB', { timeZone: profile.timezone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
    clocks.set(profile.timezone, clock);
  }
  const parts = Object.fromEntries(clock.formatToParts(time * 1000).map((part) => [part.type, part.value]));
  return Number(parts.hour) * 60 + Number(parts.minute);
}

interface Session { volume: number; buckets: Set<number>; unknown: boolean }

/** Regular-session 5-minute volume per exchange date. */
function sessions(intraday: BarResult, profile: MarketProfile): Map<string, Session> {
  const missing = new Set(intraday.missingVolumeAt ?? []);
  const byDate = new Map<string, Session>();
  for (const bar of intraday.bars) {
    const minute = minuteOfDay(bar.time, profile);
    if (minute < profile.sessionOpenMinutes || minute > profile.sessionCloseMinutes) continue;
    const date = sessionDate(bar.time, profile);
    const session = byDate.get(date) ?? { volume: 0, buckets: new Set<number>(), unknown: false };
    if (missing.has(bar.time) || !Number.isFinite(bar.volume) || bar.volume < 0) session.unknown = true;
    else session.volume += bar.volume;
    session.buckets.add(minute - profile.sessionOpenMinutes);
    byDate.set(date, session);
  }
  return byDate;
}

/** Every bucket from the open to the last full bucket before the close (an early close is incomplete). */
function complete(session: Session, profile: MarketProfile): boolean {
  if (session.unknown) return false;
  for (let offset = 0; offset < profile.sessionCloseMinutes - profile.sessionOpenMinutes; offset += STEP_MINUTES)
    if (!session.buckets.has(offset)) return false;
  return true;
}

/** Current exchange-date index volume from 5-minute bars, checked against the daily history's unit. */
export function intradayIndexVolume(
  daily: readonly Bar[],
  intraday: BarResult | null | undefined,
  symbol: string,
  now = Date.now() / 1000,
): IntradayIndexVolume {
  const no = (reason: string): IntradayIndexVolume => ({ status: 'unavailable', reason });
  if (!isIndexSymbol(symbol)) return no('僅指數使用盤中量重建');
  if (!intraday) return no('盤中 5 分 K 無法取得');
  if (intraday.dataState === 'simulated') return no('模擬資料不重建成交量');
  const profile = intraday.market ?? getMarketProfile(symbol);
  const today = sessionDate(now, profile);
  const byDate = sessions(intraday, profile);
  const current = byDate.get(today);
  if (!current) return no('今日尚無盤中 5 分 K');
  if (current.unknown || current.volume <= 0) return no('資料來源盤中未提供此指數成交量');
  const dailyVolume = new Map(daily.map((bar) => [calendarDate(bar.time, profile), bar.volume]));
  const ratios = [...byDate].filter(([date, session]) => date < today && complete(session, profile) && session.volume > 0
    && (dailyVolume.get(date) ?? 0) > 0)
    .sort(([a], [b]) => a.localeCompare(b)).slice(-MAX_CHECKED_SESSIONS)
    .map(([date, session]) => session.volume / dailyVolume.get(date)!);
  if (ratios.length < MIN_CHECKED_SESSIONS) return no('近期日線量不足，無法驗證盤中量與歷史同一口徑');
  const sorted = [...ratios].sort((a, b) => a - b);
  const ratio = sorted.length % 2 ? sorted[(sorted.length - 1) / 2]! : (sorted[sorted.length / 2 - 1]! + sorted[sorted.length / 2]!) / 2;
  if (ratio < UNIT_TOLERANCE.min || ratio > UNIT_TOLERANCE.max) return no('盤中量與日線歷史口徑不一致，不混用');
  return { status: 'ok', date: today, volume: Math.round(current.volume), ratio, checkedSessions: ratios.length };
}

export const INTRADAY_VOLUME_NOTE = '今日量為同來源 5 分 K 累計（已用前幾個交易日驗證與日線同口徑）';

/** Daily bars whose volume this module wrote; only those may be overwritten by a newer estimate. */
const estimatedBars = new WeakSet<Bar>();

/**
 * Puts a verified estimate on today's daily bar when the source left its volume zero or unknown (or it
 * is an earlier estimate). Returns the same object when nothing changes, so callers can compare by identity.
 */
export function withIntradayIndexVolume(result: BarResult, estimate: IntradayIndexVolume | null | undefined, symbol: string): BarResult {
  const last = result.bars.at(-1);
  // A source volume that replaced an estimate must not keep the estimate's provenance note.
  const unchanged = () => last && !estimatedBars.has(last) && result.source.includes(INTRADAY_VOLUME_NOTE)
    ? { ...result, source: result.source.replace(` · ${INTRADAY_VOLUME_NOTE}`, '') } : result;
  if (estimate?.status !== 'ok' || !last) return unchanged();
  const profile = result.market ?? getMarketProfile(symbol);
  if (calendarDate(last.time, profile) !== estimate.date) return unchanged();
  const missing = result.missingVolumeAt?.includes(last.time) ?? false;
  if (last.volume > 0 && !missing && !estimatedBars.has(last)) return unchanged();
  if (estimatedBars.has(last) && last.volume === estimate.volume) return result;
  const bar = { ...last, volume: estimate.volume };
  estimatedBars.add(bar);
  const missingVolumeAt = result.missingVolumeAt?.filter((time) => time !== last.time);
  return {
    ...result,
    bars: [...result.bars.slice(0, -1), bar],
    ...(missingVolumeAt ? { missingVolumeAt } : {}),
    source: result.source.includes(INTRADAY_VOLUME_NOTE) ? result.source : `${result.source} · ${INTRADAY_VOLUME_NOTE}`,
  };
}

/** True when the newest daily bar is today's and still has no usable volume. */
export function todayVolumeMissing(result: BarResult | null | undefined, symbol: string, now = Date.now() / 1000): boolean {
  const last = result?.bars.at(-1);
  if (!result || !last) return false;
  const profile = result.market ?? getMarketProfile(symbol);
  return calendarDate(last.time, profile) === sessionDate(now, profile)
    && (last.volume <= 0 || (result.missingVolumeAt?.includes(last.time) ?? false));
}
