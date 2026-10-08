import { TickMarkType, type DeepPartial, type ChartOptions, type Time } from 'lightweight-charts';
import type { MarketProfile } from '../market-data/MarketProfile';
import type { Timeframe } from '../market-data/MarketDataProvider';

function asDate(time: Time): Date {
  if (typeof time === 'number') return new Date(time * 1000);
  if (typeof time === 'string') return new Date(`${time}T00:00:00Z`);
  return new Date(Date.UTC(time.year, time.month - 1, time.day));
}

/** UTC preserves accepted US calendar anchors; Taiwan labels use the exchange wall clock. */
export function marketTimeOptions(market: MarketProfile, timeframe: Timeframe): DeepPartial<ChartOptions> {
  const timeZone = market.market === 'TW' ? market.timezone : 'UTC';
  const intraday = !['1D', '1W', '1M'].includes(timeframe);
  const full = new Intl.DateTimeFormat('en-GB', {
    timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
    ...(intraday ? { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' } as const : {}),
  });
  const formats = [
    new Intl.DateTimeFormat('en-US', { timeZone, year: 'numeric' }),
    new Intl.DateTimeFormat('en-US', { timeZone, month: 'short' }),
    new Intl.DateTimeFormat('en-US', { timeZone, day: 'numeric' }),
    new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }),
    new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }),
  ];
  return {
    localization: { timeFormatter: (time: Time) => full.format(asDate(time)) },
    timeScale: {
      timeVisible: intraday,
      tickMarkFormatter: (time: Time, type: TickMarkType) => formats[type].format(asDate(time)),
    },
  };
}
