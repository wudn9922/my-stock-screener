import type { Bar, BarResult, Timeframe } from './MarketDataProvider';
import { intervalSeconds } from './MarketDataProvider';
import {
  calendarPeriodStart,
  getMarketProfile,
  isSessionCloseObservation,
  sessionDate,
  type MarketProfile,
} from './MarketProfile';

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

function partsAt(epochSeconds: number, profile: MarketProfile): Record<string, string> {
  return Object.fromEntries(
    formatterFor(profile)
      .formatToParts(new Date(epochSeconds * 1000))
      .map((part) => [part.type, part.value]),
  );
}

/** Convert a local market wall time to Unix seconds, including US DST. */
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
    const part = partsAt(guess / 1000, profile);
    const observed = Date.UTC(
      Number(part.year),
      Number(part.month) - 1,
      Number(part.day),
      Number(part.hour),
      Number(part.minute),
    );
    guess += wanted - observed;
  }
  return guess / 1000;
}

function dateParts(time: number, profile: MarketProfile): { year: number; month: number; day: number } {
  const [year, month, day] = sessionDate(time, profile).split('-').map(Number);
  return { year, month, day };
}

/** Conservative scheduled end of one regular-session bar; holidays are unknown. */
export function barEndTime(time: number, timeframe: Timeframe, market?: MarketProfile): number {
  const profile = market ?? getMarketProfile();
  if (timeframe === '1M') {
    if (profile.market === 'TW') {
      const { year, month } = dateParts(time, profile);
      const nextMonthSeed = Date.UTC(year, month, 15, 12) / 1000;
      return calendarPeriodStart(nextMonthSeed, '1M', profile);
    }
    const date = new Date(time * 1000);
    const nextMonth = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1));
    return marketEpoch(
      nextMonth.getUTCFullYear(),
      nextMonth.getUTCMonth() + 1,
      1,
      0,
      0,
      profile,
    );
  }
  if (timeframe === '1D' || timeframe === '1W') {
    if (profile.market === 'TW') {
      const periodStart = timeframe === '1W' ? calendarPeriodStart(time, '1W', profile) : time;
      const { year, month, day } = dateParts(periodStart, profile);
      const date = new Date(Date.UTC(year, month - 1, day));
      if (timeframe === '1D') {
        if ([0, 6].includes(date.getUTCDay())) return Infinity;
        return marketEpoch(year, month, day, 13, 30, profile);
      }
      date.setUTCDate(date.getUTCDate() + 4);
      return marketEpoch(
        date.getUTCFullYear(),
        date.getUTCMonth() + 1,
        date.getUTCDate(),
        13,
        30,
        profile,
      );
    }
    const date = new Date(time * 1000);
    const year = date.getUTCFullYear();
    const month = date.getUTCMonth();
    const day = date.getUTCDate();
    const sessionDay =
      timeframe === '1D'
        ? new Date(Date.UTC(year, month, day))
        : new Date(Date.UTC(year, month, day + ((5 - date.getUTCDay() + 7) % 7)));
    return marketEpoch(
      sessionDay.getUTCFullYear(),
      sessionDay.getUTCMonth() + 1,
      sessionDay.getUTCDate(),
      16,
      0,
      profile,
    );
  }

  const part = partsAt(time, profile);
  const minuteOfDay = Number(part.hour) * 60 + Number(part.minute);
  if (
    part.weekday === 'Sat' ||
    part.weekday === 'Sun' ||
    minuteOfDay < profile.sessionOpenMinutes ||
    minuteOfDay >= profile.sessionCloseMinutes
  ) {
    return Infinity;
  }
  const endMinute = Math.min(
    minuteOfDay + intervalSeconds[timeframe] / 60,
    profile.sessionCloseMinutes,
  );
  const dateArgs = [Number(part.year), Number(part.month), Number(part.day)] as const;
  return marketEpoch(
    dateArgs[0],
    dateArgs[1],
    dateArgs[2],
    Math.floor(endMinute / 60),
    endMinute % 60,
    profile,
  );
}

/** Return bars with scheduled session ends no later than the supplied as-of time. */
export function closedBars(
  result: Pick<BarResult, 'bars' | 'market' | 'sessionCloseObservations'>,
  timeframe: Timeframe,
  asOf: number,
): Bar[] {
  const market = result.market ?? getMarketProfile();
  return result.bars.filter(
    (bar) =>
      (result.sessionCloseObservations?.includes(bar.time) &&
        isSessionCloseObservation(bar, market) &&
        asOf >= bar.time) ||
      barEndTime(bar.time, timeframe, market) <= asOf,
  );
}
