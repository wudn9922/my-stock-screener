import { expect, it } from 'vitest';
import { nextSessionTime, TimeMapper } from '../src/chart/TimeMapper';
import { barEndTime, closedBars } from '../src/market-data/MarketTiming';
import { getMarketProfile, type Bar } from '../src/market-data/MarketDataProvider';

const taiwan = getMarketProfile('2330.TW');
const bar = (time: number): Bar => ({ time, open: 10, high: 11, low: 9, close: 10, volume: 100 });
const unix = (iso: string): number => Date.parse(iso) / 1000;

it('clamps the final Taiwan 5m and 1H bars to the 13:30 local session close', () => {
  const finalFiveMinuteBar = bar(unix('2026-10-08T05:25:00Z'));
  const finalHourlyBar = bar(unix('2026-10-08T05:00:00Z'));
  const close = unix('2026-10-08T05:30:00Z');

  expect(barEndTime(finalFiveMinuteBar.time, '5m', taiwan)).toBe(close);
  expect(barEndTime(finalHourlyBar.time, '1H', taiwan)).toBe(close);
  expect(closedBars({ bars: [finalHourlyBar], market: taiwan }, '1H', close - 1)).toEqual([]);
  expect(closedBars({ bars: [finalHourlyBar], market: taiwan }, '1H', close)).toEqual([
    finalHourlyBar,
  ]);
});

it('includes only marked, validated Taiwan terminal close observations once their timestamp arrives', () => {
  const closeTime = unix('2026-10-08T05:30:00Z');
  const prior = {
    ...bar(unix('2026-10-08T05:25:00Z')),
    open: 2555,
    high: 2565,
    low: 2545,
    close: 2560,
  };
  const terminal = {
    time: closeTime,
    open: 2550,
    high: 2550,
    low: 2550,
    close: 2550,
    volume: 0,
  };
  const marked = {
    bars: [prior, terminal],
    market: taiwan,
    sessionCloseObservations: [closeTime],
  };

  expect(barEndTime(closeTime, '5m', taiwan)).toBe(Infinity);
  expect(closedBars(marked, '5m', closeTime - 1)).toEqual([]);
  expect(closedBars(marked, '5m', closeTime)).toEqual([prior, terminal]);
  expect(closedBars({ bars: [terminal], market: taiwan }, '5m', closeTime)).toEqual([]);

  const invalidRows = [
    { ...terminal, high: 2551 },
    { ...terminal, time: unix('2026-10-09T05:30:00Z'), volume: 1 },
    { ...terminal, time: unix('2026-10-08T05:31:00Z') },
    { ...terminal, time: unix('2026-10-10T05:30:00Z') },
  ];
  expect(
    closedBars(
      {
        bars: invalidRows,
        market: taiwan,
        sessionCloseObservations: invalidRows.map(({ time }) => time),
      },
      '5m',
      closeTime + 60 * 60 * 24,
    ),
  ).toEqual([]);
});

it('keeps US close timing unchanged when Taiwan terminal metadata is present', () => {
  const us = getMarketProfile('AAPL');
  const time = unix('2026-10-08T19:55:00Z');
  const row = { time, open: 2550, high: 2550, low: 2550, close: 2550, volume: 0 };
  const marked = { bars: [row], market: us, sessionCloseObservations: [time] };

  expect(closedBars(marked, '5m', time)).toEqual([]);
  expect(closedBars(marked, '5m', time + 300)).toEqual([row]);
});

it('uses Taiwan calendar dates for daily bars and closes weekly bars on that same week Friday', () => {
  const dailyTime = unix('2026-10-07T16:00:00Z'); // Thursday, October 8 in Taipei.
  const dailyClose = unix('2026-10-08T05:30:00Z');
  const dailyBar = bar(dailyTime);
  expect(barEndTime(dailyTime, '1D', taiwan)).toBe(dailyClose);
  expect(closedBars({ bars: [dailyBar], market: taiwan }, '1D', dailyClose - 1)).toEqual([]);
  expect(closedBars({ bars: [dailyBar], market: taiwan }, '1D', dailyClose)).toEqual([dailyBar]);

  const mondayLocalMidnight = unix('2026-10-04T16:00:00Z');
  const saturday = unix('2026-10-10T01:00:00Z');
  const sunday = unix('2026-10-11T04:00:00Z');
  const fridayClose = unix('2026-10-09T05:30:00Z');
  expect(barEndTime(mondayLocalMidnight, '1W', taiwan)).toBe(fridayClose);
  expect(barEndTime(saturday, '1W', taiwan)).toBe(fridayClose);
  expect(barEndTime(sunday, '1W', taiwan)).toBe(fridayClose);
});

it('advances Taiwan intraday and weekly whitespace to the next local session', () => {
  const mondayOpen = unix('2026-10-12T01:00:00Z');
  expect(nextSessionTime(unix('2026-10-09T05:25:00Z'), '5m', taiwan)).toBe(mondayOpen);
  expect(nextSessionTime(unix('2026-10-09T05:00:00Z'), '1H', taiwan)).toBe(mondayOpen);
  expect(nextSessionTime(unix('2026-10-05T01:00:00Z'), '1W', taiwan)).toBe(
    unix('2026-10-12T01:00:00Z'),
  );
});

it('closes Taiwan monthly bars only at next local month midnight across leap February and year rollover', () => {
  const february = bar(unix('2024-01-31T16:00:00Z'));
  const februaryClose = unix('2024-02-29T16:00:00Z');
  expect(barEndTime(february.time, '1M', taiwan)).toBe(februaryClose);
  expect(closedBars({ bars: [february], market: taiwan }, '1M', unix('2024-02-15T12:00:00Z'))).toEqual(
    [],
  );
  expect(closedBars({ bars: [february], market: taiwan }, '1M', februaryClose - 1)).toEqual([]);
  expect(closedBars({ bars: [february], market: taiwan }, '1M', februaryClose)).toEqual([
    february,
  ]);

  const december = bar(unix('2025-11-30T16:00:00Z'));
  expect(barEndTime(december.time, '1M', taiwan)).toBe(unix('2025-12-31T16:00:00Z'));
  expect(nextSessionTime(february.time, '1M', taiwan)).toBe(februaryClose);
  expect(nextSessionTime(december.time, '1M', taiwan)).toBe(unix('2025-12-31T16:00:00Z'));
});

it('round trips Taiwan monthly actual timestamps and calendar extrapolation beyond the future buffer', () => {
  const october = unix('2026-09-30T16:00:00Z'); // October 1 at midnight in Taipei.
  const november = unix('2026-10-31T16:00:00Z'); // November 1 at midnight in Taipei.
  const mapper = new TimeMapper([bar(october), bar(november)], '1M', 2, taiwan);

  expect(mapper.times.slice(0, 2)).toEqual([october, november]);
  expect(mapper.futureWhitespace().map(({ time }) => time)).toEqual([
    unix('2026-11-30T16:00:00Z'),
    unix('2026-12-31T16:00:00Z'),
  ]);

  for (const logical of [-1.25, 0.5, 5.25]) {
    const time = mapper.toTime(logical);
    expect(mapper.toLogical(time)).toBeCloseTo(logical, 10);
  }
});
