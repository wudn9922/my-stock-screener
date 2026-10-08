import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';
import {
  normalizeYahooCalendarResponse,
  normalizeYahooResponse,
} from '../src/market-data/YahooNormalizer';
import type { Bar } from '../src/market-data/MarketDataProvider';

const asOf = Date.parse('2026-10-07T20:05:00Z') / 1000;
const dailyCloseTime = Date.parse('2026-10-07T13:30:00Z') / 1000;
const quoteTime = Date.parse('2026-10-07T20:00:00Z') / 1000;

function fixture(name: string) {
  return JSON.parse(readFileSync(new URL(`./fixtures/market/${name}`, import.meta.url), 'utf8'));
}

function currentRows(timeframe: '1W' | '1M') {
  return {
    native: fixture(timeframe === '1W' ? 'SMCI-current-1wk.json' : 'SMCI-current-1mo.json'),
    daily: fixture('SMCI-current-1d.json'),
  };
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

const expected: Record<'1W' | '1M', Bar> = {
  '1W': {
    time: Date.parse('2026-10-05T00:00:00Z') / 1000,
    open: 43.56999969482422,
    high: 45.76499938964844,
    low: 42.459999084472656,
    close: 44.94,
    volume: 90284251,
  },
  '1M': {
    time: Date.parse('2026-10-01T00:00:00Z') / 1000,
    open: 40.88999938964844,
    high: 45.76499938964844,
    low: 40.130001068115234,
    close: 44.94,
    volume: 161285951,
  },
};

it('heals only a valid latest daily null close from same-session Yahoo market metadata', () => {
  const raw = fixture('SMCI-current-1d.json');
  const normalized = normalizeYahooResponse(raw, asOf, '1D', 'SMCI');
  expect(normalized.bars.at(-1)).toMatchObject({ time: dailyCloseTime, close: 44.94 });
  expect(normalized.quote).toMatchObject({ price: 44.94, asOf: quoteTime });
  expect(normalized.normalization?.healedDailyClose).toEqual({
    method: 'validated-regular-market-price',
    time: dailyCloseTime,
    quoteAsOf: quoteTime,
  });

  const historicalNull = clone(raw);
  const previousIndex = historicalNull.chart.result[0].timestamp.length - 2;
  historicalNull.chart.result[0].indicators.quote[0].close[previousIndex] = null;
  const withHistoricalNull = normalizeYahooResponse(historicalNull, asOf, '1D', 'SMCI');
  expect(withHistoricalNull.bars.some((bar: Bar) => bar.time === historicalNull.chart.result[0].timestamp[previousIndex])).toBe(false);
  expect(withHistoricalNull.bars.at(-1)?.time).toBe(dailyCloseTime);
  expect(withHistoricalNull.normalization?.healedDailyClose?.time).toBe(dailyCloseTime);

  const missingClose = clone(raw);
  missingClose.chart.result[0].indicators.quote[0].close.pop();
  const withoutLatest = normalizeYahooResponse(missingClose, asOf, '1D', 'SMCI');
  expect(withoutLatest.bars.at(-1)?.time).toBeLessThan(dailyCloseTime);
  expect(withoutLatest.normalization?.healedDailyClose).toBeUndefined();

  const missingVolume = clone(raw);
  missingVolume.chart.result[0].indicators.quote[0].volume.pop();
  const withoutRealVolume = normalizeYahooResponse(missingVolume, asOf, '1D', 'SMCI');
  expect(withoutRealVolume.bars.at(-1)?.time).toBeLessThan(dailyCloseTime);
  expect(withoutRealVolume.normalization?.healedDailyClose).toBeUndefined();
});

it.each(['1W', '1M'] as const)(
  'reconstructs only the current %s bucket from daily OHLCV and retains native history',
  (timeframe) => {
    const { native: rawNative, daily: rawDaily } = currentRows(timeframe);
    const nativeBefore = normalizeYahooResponse(rawNative, asOf, timeframe, 'SMCI');
    const result = normalizeYahooCalendarResponse(rawNative, rawDaily, asOf, timeframe, 'SMCI');
    const periodStart = expected[timeframe].time;

    expect(result.bars.at(-1)).toEqual(expected[timeframe]);
    expect(result.bars.filter((bar) => bar.time !== periodStart)).toEqual(
      nativeBefore.bars.filter((bar) => bar.time !== periodStart),
    );
    expect(result.source).toContain('current period derived from daily OHLCV');
    expect(result.source).toContain(timeframe === '1W' ? '3 daily bars' : '5 daily bars');
    expect(result.quote).toMatchObject({ price: 44.94, asOf: quoteTime });
    expect(result.normalization?.currentPeriod).toMatchObject({
      method: 'daily-ohlcv',
      timeframe,
      periodStart,
      lastDailyTime: dailyCloseTime,
      dailyCount: timeframe === '1W' ? 3 : 5,
      quoteAsOf: quoteTime,
    });
    expect(result.normalization?.healedDailyClose).toEqual({
      method: 'validated-regular-market-price',
      time: dailyCloseTime,
      quoteAsOf: quoteTime,
    });
  },
);

it.each(['1W', '1M'] as const)(
  'reconstructs %s when native data contains only the period aggregate and current metadata',
  (timeframe) => {
    const { native, daily } = currentRows(timeframe);
    const nativeResult = native.chart.result[0];
    const appendedIndex = nativeResult.timestamp.length - 1;
    nativeResult.timestamp.splice(appendedIndex, 1);
    for (const values of Object.values(nativeResult.indicators.quote[0])) {
      if (Array.isArray(values)) values.splice(appendedIndex, 1);
    }

    const result = normalizeYahooCalendarResponse(native, daily, asOf, timeframe, 'SMCI');
    expect(result.bars.at(-1)).toEqual(expected[timeframe]);
    expect(result.normalization?.currentPeriod?.native).toMatchObject({
      time: expected[timeframe].time,
      open: expected[timeframe].open,
    });
  },
);

it('rejects invalid current-period rows and mismatched or stale Yahoo metadata', () => {
  const makeResult = (native: unknown, daily: unknown) =>
    normalizeYahooCalendarResponse(native, daily, asOf, '1W', 'SMCI');

  const historicalNull = currentRows('1W');
  historicalNull.daily.chart.result[0].indicators.quote[0].close[
    historicalNull.daily.chart.result[0].timestamp.length - 2
  ] = null;
  expect(() => makeResult(historicalNull.native, historicalNull.daily)).toThrow(/unhealed null close/);

  const missingClose = currentRows('1W');
  missingClose.daily.chart.result[0].indicators.quote[0].close.pop();
  expect(() => makeResult(missingClose.native, missingClose.daily)).toThrow();

  const duplicateSession = currentRows('1W');
  const dailyResult = duplicateSession.daily.chart.result[0];
  const lastTime = dailyResult.timestamp.at(-1)!;
  dailyResult.timestamp.push(lastTime);
  for (const values of Object.values(dailyResult.indicators.quote[0])) {
    if (Array.isArray(values)) values.push(values.at(-1));
  }
  expect(() => makeResult(duplicateSession.native, duplicateSession.daily)).toThrow(/duplicate UTC session dates/);

  const missingVolume = currentRows('1W');
  missingVolume.daily.chart.result[0].indicators.quote[0].volume[
    missingVolume.daily.chart.result[0].timestamp.length - 1
  ] = null;
  expect(() => makeResult(missingVolume.native, missingVolume.daily)).toThrow();

  const mismatch = currentRows('1W');
  mismatch.native.chart.result[0].meta.regularMarketPrice += 0.02;
  expect(() => makeResult(mismatch.native, mismatch.daily)).toThrow(/quotes disagree/);

  const stale = currentRows('1W');
  stale.daily.chart.result[0].meta.regularMarketTime = Date.parse('2026-10-06T20:00:00Z') / 1000;
  expect(() => makeResult(stale.native, stale.daily)).toThrow(/stale or inconsistent/);

  const reversedMarketTimes = currentRows('1W');
  reversedMarketTimes.native.chart.result[0].meta.regularMarketTime =
    Date.parse('2026-10-07T20:01:00Z') / 1000;
  expect(() => makeResult(reversedMarketTimes.native, reversedMarketTimes.daily)).toThrow(/older than the native/);

  const symbolMismatch = currentRows('1W');
  symbolMismatch.daily.chart.result[0].meta.symbol = 'NFLX';
  expect(() => makeResult(symbolMismatch.native, symbolMismatch.daily)).toThrow(/symbol mismatch/);
});

it('requires checked daily coverage when no native aggregate is present', () => {
  const { native, daily } = currentRows('1W');
  const nativeResult = native.chart.result[0];
  const keepIndexes = nativeResult.timestamp
    .map((time: number, index: number) => ({ time, index }))
    .filter(({ time }: { time: number }) => time < expected['1W'].time);
  nativeResult.timestamp = keepIndexes.map(({ time }: { time: number }) => time);
  for (const [key, values] of Object.entries(nativeResult.indicators.quote[0])) {
    if (Array.isArray(values)) {
      nativeResult.indicators.quote[0][key] = keepIndexes.map(({ index }: { index: number }) => values[index]);
    }
  }

  const result = normalizeYahooCalendarResponse(native, daily, asOf, '1W', 'SMCI');
  expect(result.bars.at(-1)).toEqual(expected['1W']);
  expect(result.normalization?.currentPeriod?.native).toBeUndefined();

  const onlyCurrentDaily = clone(daily);
  const dailyResult = onlyCurrentDaily.chart.result[0];
  const currentIndices = dailyResult.timestamp
    .map((time: number, index: number) => ({ time, index }))
    .filter(({ time }: { time: number }) => time >= expected['1W'].time);
  dailyResult.timestamp = currentIndices.map(({ time }: { time: number }) => time);
  for (const [key, values] of Object.entries(dailyResult.indicators.quote[0])) {
    if (Array.isArray(values)) {
      dailyResult.indicators.quote[0][key] = currentIndices.map(({ index }: { index: number }) => values[index]);
    }
  }
  expect(() => normalizeYahooCalendarResponse(native, onlyCurrentDaily, asOf, '1W', 'SMCI')).toThrow(
    /history must begin before the period/,
  );
});

it('rejects overflow in the derived daily volume total', () => {
  const { native, daily } = currentRows('1W');
  const result = daily.chart.result[0];
  const volume = result.indicators.quote[0].volume;
  for (let i = result.timestamp.length - 3; i < result.timestamp.length; i++) {
    volume[i] = Number.MAX_VALUE;
  }
  expect(() => normalizeYahooCalendarResponse(native, daily, asOf, '1W', 'SMCI')).toThrow(
    /aggregate is invalid/,
  );
});
