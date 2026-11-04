import type { BarResult } from "./MarketDataProvider";
import { getMarketProfile, sessionDate } from "./MarketProfile";

export type SameTimeVolume =
  | { status: "unavailable"; reason: string }
  | {
      status: "ok";
      ratio: number;
      currentVolume: number;
      previousVolume: number;
      date: string;
      previousDate: string;
      cutoff: string;
      source: string;
      stale: boolean;
    };
/** Five-minute regular-session buckets only. Incomplete current buckets never enter either sum. */
export function sameTimeVolume(
  result: BarResult,
  symbol: string,
  now = Date.now() / 1000,
): SameTimeVolume {
  const no = (reason: string): SameTimeVolume => ({
    status: "unavailable",
    reason,
  });
  if (result.dataState === "simulated")
    return no("模擬資料無法計算真實同期量比");
  if (/^\^|=|CRYPTO-|-[A-Z]+$/.test(symbol))
    return no("此商品不支援股票交易時段同期量比");
  const profile = getMarketProfile(symbol),
    step = 300;
  const clock = new Intl.DateTimeFormat("en-GB", {
    timeZone: profile.timezone,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  const minutes = (time: number) => {
    const p = Object.fromEntries(
      clock.formatToParts(time * 1000).map((x) => [x.type, x.value]),
    );
    return Number(p.hour) * 60 + Number(p.minute) + Number(p.second) / 60;
  };
  const missingVolume = new Set(result.missingVolumeAt ?? []);
  const byDay = new Map<string, Map<number, number>>();
  const asOf = Math.min(now, result.asOf ?? now);
  for (const b of result.bars) {
    const minute = minutes(b.time);
    if (
      minute < profile.sessionOpenMinutes ||
      minute >= profile.sessionCloseMinutes ||
      b.time + step > asOf
    )
      continue;
    const offset = minute - profile.sessionOpenMinutes;
    if (offset % 5 !== 0 || !Number.isFinite(b.volume) || b.volume < 0)
      continue;
    if (missingVolume.has(b.time)) continue;
    const date = sessionDate(b.time, profile),
      day = byDay.get(date) ?? new Map<number, number>();
    // Reject duplicate bucket payloads rather than double-count volume.
    if (day.has(offset)) return no("分時資料有重複時段，無法可靠比較");
    day.set(offset, b.volume);
    byDay.set(date, day);
  }
  const dates = [...byDay.keys()].sort();
  const date = dates.at(-1),
    previousDate = dates.at(-2);
  if (!date || !previousDate)
    return no("需要最近兩個有成交交易日的完整 5 分鐘資料");
  const current = byDay.get(date)!,
    previous = byDay.get(previousDate)!;
  const offsets = [...current.keys()].sort((a, b) => a - b),
    end = offsets.at(-1)!;
  let currentVolume = 0,
    previousVolume = 0;
  for (let minute = 0; minute <= end; minute += 5) {
    if (!current.has(minute) || !previous.has(minute))
      return no("兩個交易日的同時間分時資料不完整（或前次為提早收盤日）");
    currentVolume += current.get(minute)!;
    previousVolume += previous.get(minute)!;
  }
  if (previousVolume <= 0)
    return no("前一交易日同時間成交量為零，無法計算量比");
  const cutoffMinute = profile.sessionOpenMinutes + end + 5;
  return {
    status: "ok",
    ratio: currentVolume / previousVolume,
    currentVolume,
    previousVolume,
    date,
    previousDate,
    cutoff: `${String(Math.floor(cutoffMinute / 60)).padStart(2, "0")}:${String(cutoffMinute % 60).padStart(2, "0")}`,
    source: result.source,
    stale: result.cacheStatus === "stale" || date !== sessionDate(now, profile),
  };
}
