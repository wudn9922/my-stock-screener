import type { Bar, Timeframe } from '../market-data/MarketDataProvider';
import { intervalSeconds } from '../market-data/MarketDataProvider';
import {
  calendarPeriodStart,
  getMarketProfile,
  sessionDate,
  type MarketProfile,
} from '../market-data/MarketProfile';
import type { Anchor } from '../drawing/DrawingModel';
const formatters = new Map<string, Intl.DateTimeFormat>();

function formatterFor(profile: MarketProfile): Intl.DateTimeFormat {
  let formatter = formatters.get(profile.timezone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('en-US', {
      timeZone: profile.timezone,
      weekday: 'short',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    });
    formatters.set(profile.timezone, formatter);
  }
  return formatter;
}

function partsAt(time: number, profile: MarketProfile): Record<string, string> {
  return Object.fromEntries(
    formatterFor(profile).formatToParts(new Date(time * 1000)).map((part) => [part.type, part.value]),
  );
}

function dateParts(time: number, profile: MarketProfile): { year: number; month: number; day: number } {
  const [year, month, day] = sessionDate(time, profile).split('-').map(Number);
  return { year, month, day };
}

function marketEpoch(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  profile: MarketProfile,
): number {
  const wanted = Date.UTC(year, month - 1, day, hour, minute);
  let guess = wanted;
  for (let i = 0; i < 3; i++) {
    const parts = partsAt(guess / 1000, profile);
    const observed = Date.UTC(
      Number(parts.year),
      Number(parts.month) - 1,
      Number(parts.day),
      Number(parts.hour),
      Number(parts.minute),
    );
    guess += wanted - observed;
  }
  return guess / 1000;
}

/** Regular-session schedule, weekends excluded. Holidays require a production exchange calendar. */
export function nextSessionTime(time: number, timeframe: Timeframe, market?: MarketProfile): number {
  const profile = market ?? getMarketProfile();
  if (timeframe === '1M') {
    if (profile.market === 'TW') {
      const { year, month } = dateParts(time, profile);
      const nextMonthSeed = Date.UTC(year, month, 15, 12) / 1000;
      return calendarPeriodStart(nextMonthSeed, '1M', profile);
    }
    const date = new Date(time * 1000);
    return Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1) / 1000;
  }
  if (profile.market === 'TW' && (timeframe === '1D' || timeframe === '1W')) {
    const { year, month, day } = dateParts(time, profile);
    const local = partsAt(time, profile);
    const scheduledDate = new Date(Date.UTC(year, month - 1, day));
    if (timeframe === '1D') scheduledDate.setUTCDate(scheduledDate.getUTCDate() + 1);
    else scheduledDate.setUTCDate(scheduledDate.getUTCDate() + 7);
    while (timeframe === '1D' && [0, 6].includes(scheduledDate.getUTCDay())) {
      scheduledDate.setUTCDate(scheduledDate.getUTCDate() + 1);
    }
    const scheduled = marketEpoch(
      scheduledDate.getUTCFullYear(),
      scheduledDate.getUTCMonth() + 1,
      scheduledDate.getUTCDate(),
      Number(local.hour),
      Number(local.minute),
      profile,
    );
    return scheduled + new Date(time * 1000).getUTCSeconds();
  }
  let next = time + intervalSeconds[timeframe];
  if (timeframe === '1D') {
    while ([0, 6].includes(new Date(next * 1000).getUTCDay())) next += 86400;
    return next;
  }
  if (timeframe === '1W') {
    while ([0, 6].includes(new Date(next * 1000).getUTCDay())) next += 86400;
    return next;
  }
  // All intraday intervals begin at 09:30 ET. A final bar may be shorter than
  // its nominal interval; jump across the closed session without scanning every
  // five minutes (Demo generates thousands of these timestamps).
  const p = partsAt(next, profile);
  const minute = Number(p.hour) * 60 + Number(p.minute);
  if (
    p.weekday !== 'Sat' &&
    p.weekday !== 'Sun' &&
    minute >= profile.sessionOpenMinutes &&
    minute < profile.sessionCloseMinutes
  ) return next;

  const date = new Date(Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day)));
  if (p.weekday === 'Sat') date.setUTCDate(date.getUTCDate() + 2);
  else if (p.weekday === 'Sun') date.setUTCDate(date.getUTCDate() + 1);
  else if (minute >= profile.sessionCloseMinutes) date.setUTCDate(date.getUTCDate() + 1);
  while ([0, 6].includes(date.getUTCDay())) date.setUTCDate(date.getUTCDate() + 1);
  return regularSessionOpen(
    date.getUTCFullYear(),
    date.getUTCMonth() + 1,
    date.getUTCDate(),
    profile,
  );
}

/** Public regular-session opening timestamp for a market-local calendar date. */
export function regularSessionOpen(
  year: number,
  month: number,
  day: number,
  market?: MarketProfile,
): number {
  const profile = market ?? getMarketProfile();
  return marketEpoch(
    year,
    month,
    day,
    Math.floor(profile.sessionOpenMinutes / 60),
    profile.sessionOpenMinutes % 60,
    profile,
  );
}

function calendarMonthTime(anchor: number, monthOffset: number, profile: MarketProfile): number {
  if (profile.market === 'TW') {
    const { year, month } = dateParts(anchor, profile);
    const monthSeed = Date.UTC(year, month - 1 + monthOffset, 15, 12) / 1000;
    return calendarPeriodStart(monthSeed, '1M', profile);
  }
  const date = new Date(anchor * 1000);
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + monthOffset, 1) / 1000;
}

/** Fractional month index relative to a canonical market-local month anchor. */
function calendarMonthLogical(time: number, anchor: number, profile: MarketProfile): number {
  if (profile.market === 'TW') {
    const a = dateParts(anchor, profile);
    const date = dateParts(time, profile);
    const monthOffset = (date.year - a.year) * 12 + date.month - a.month;
    const lower = calendarMonthTime(anchor, monthOffset, profile);
    const upper = calendarMonthTime(anchor, monthOffset + 1, profile);
    return monthOffset + (time - lower) / (upper - lower);
  }
  const anchorDate = new Date(anchor * 1000);
  const date = new Date(time * 1000);
  const monthOffset =
    (date.getUTCFullYear() - anchorDate.getUTCFullYear()) * 12 +
    date.getUTCMonth() - anchorDate.getUTCMonth();
  const lower = calendarMonthTime(anchor, monthOffset, profile);
  const upper = calendarMonthTime(anchor, monthOffset + 1, profile);
  return monthOffset + (time - lower) / (upper - lower);
}

/** Calendar-aware extrapolation around the buffered timestamps. */
function calendarMonthAtLogical(anchor: number, logicalOffset: number, profile: MarketProfile): number {
  const lowerMonth = Math.floor(logicalOffset);
  const fraction = logicalOffset - lowerMonth;
  const lower = calendarMonthTime(anchor, lowerMonth, profile);
  const upper = calendarMonthTime(anchor, lowerMonth + 1, profile);
  return lower + fraction * (upper - lower);
}

export class TimeMapper {
  readonly times: number[];
  readonly lastRealLogical: number;
  constructor(
    readonly bars: readonly Bar[],
    readonly timeframe: Timeframe,
    futureCount = 500,
    readonly market: MarketProfile = getMarketProfile(),
  ) {
    this.times = bars.map((b) => b.time);
    this.lastRealLogical = bars.length - 1;
    for (let i = 0; i < futureCount && this.times.length; i++)
      this.times.push(nextSessionTime(this.times.at(-1)!, timeframe, market));
  }
  toTime(logical: number): number {
    if (!this.times.length) return 0;
    const i = Math.floor(logical),
      fraction = logical - i;
    if (i < 0) {
      if (this.timeframe === '1M') {
        return calendarMonthAtLogical(this.times[0], logical, this.market);
      }
      return this.times[0] + logical * intervalSeconds[this.timeframe];
    }
    if (i >= this.times.length - 1) {
      if (this.timeframe === '1M') {
        return calendarMonthAtLogical(
          this.times.at(-1)!,
          logical - (this.times.length - 1),
          this.market,
        );
      }
      return (
        this.times.at(-1)! + (logical - this.times.length + 1) * intervalSeconds[this.timeframe]
      );
    }
    return this.times[i] + fraction * (this.times[i + 1] - this.times[i]);
  }
  toLogical(time: number): number {
    if (!this.times.length) return 0;
    if (time < this.times[0]) {
      return this.timeframe === '1M'
        ? calendarMonthLogical(time, this.times[0], this.market)
        : (time - this.times[0]) / intervalSeconds[this.timeframe];
    }
    const end = this.times.length - 1;
    if (time >= this.times[end]) {
      if (this.timeframe === '1M') {
        return end + calendarMonthLogical(time, this.times[end], this.market);
      }
      return end + (time - this.times[end]) / intervalSeconds[this.timeframe];
    }
    let lo = 0,
      hi = end;
    while (lo + 1 < hi) {
      const mid = (lo + hi) >> 1;
      if (this.times[mid] <= time) lo = mid;
      else hi = mid;
    }
    return lo + (time - this.times[lo]) / (this.times[hi] - this.times[lo]);
  }
  anchor(logical: number, price: number): Anchor {
    return { time: this.toTime(logical), logical, price, timeframe: this.timeframe };
  }
  futureWhitespace(): { time: number }[] {
    return this.times.slice(this.lastRealLogical + 1).map((time) => ({ time }));
  }
  isFuture(logical: number): boolean {
    return logical > this.lastRealLogical;
  }
}
