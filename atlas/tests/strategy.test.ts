import { describe, expect, it } from 'vitest';
import type { Bar } from '../src/market-data/MarketDataProvider';
import { movingAverage, runBacktest, type StrategyConfig } from '../src/strategy';

function bars(closes: number[], opens: number[] = closes): Bar[] {
  return closes.map((close, index) => ({
    time: 1_700_000_000 + index * 300,
    open: opens[index],
    high: Math.max(opens[index], close) + 1,
    low: Math.min(opens[index], close) - 1,
    close,
    volume: 100,
  }));
}

function validPriceBars(closes: number[], opens: number[] = closes): Bar[] {
  return closes.map((close, index) => ({
    time: 1_700_000_000 + index * 300,
    open: opens[index],
    high: Math.max(opens[index], close),
    low: Math.min(opens[index], close),
    close,
    volume: 1,
  }));
}

function config(overrides: Partial<StrategyConfig> = {}): StrategyConfig {
  return {
    symbol: 'TEST',
    timeframe: '5m',
    kind: 'ma-cross',
    fastType: 'SMA',
    slowType: 'SMA',
    fastPeriod: 1,
    slowPeriod: 2,
    direction: 'long',
    ...overrides,
  };
}

describe('strategy indicators and signals', () => {
  it('seeds SMA and EMA with the first-period SMA and aligns warmup nulls', () => {
    const data = bars([2, 4, 6, 10]);
    const sma = movingAverage(data, 3, 'SMA');
    expect(sma.slice(0, 3)).toEqual([null, null, 4]);
    expect(sma[3]).toBeCloseTo(20 / 3);
    const ema = movingAverage(data, 2, 'EMA');
    expect(ema.slice(0, 3)).toEqual([null, 3, 5]);
    expect(ema[3]).toBeCloseTo(25 / 3);
  });

  it('uses only a prior finite comparison and treats equality as neutral until a strict crossing', () => {
    const result = runBacktest({
      config: config({ kind: 'price-cross', slowPeriod: 2 }),
      closedBars: bars([8, 9, 10, 10, 12, 8]),
      initialCapital: 1000,
    });
    expect(result.signals.map((signal) => signal.action)).toEqual(['BUY', 'EXIT']);
    expect(result.signals.map((signal) => signal.time)).toEqual([1_700_001_200, 1_700_001_500]);
  });

  it('keeps a final-bar signal without inventing a next-open fill', () => {
    const result = runBacktest({ config: config(), closedBars: bars([10, 9, 10]), initialCapital: 1000 });
    expect(result.signals.map((signal) => signal.action)).toEqual(['BUY']);
    expect(result.markers).toEqual([]);
    expect(result.trades).toEqual([]);
    expect(result.openPosition).toBeNull();
    expect(result.equity).toHaveLength(3);
  });

  it('does not let a later closed bar change an earlier signal or fill it at its own close', () => {
    const prefix = bars([10, 9, 10]);
    const beforeNextBar = runBacktest({ config: config(), closedBars: prefix, initialCapital: 1000 });
    const withNextBar = runBacktest({
      config: config(),
      closedBars: [...prefix, { ...bars([12], [15])[0], time: prefix[2].time + 300 }],
      initialCapital: 1000,
    });
    expect(withNextBar.signals[0]).toEqual(beforeNextBar.signals[0]);
    expect(beforeNextBar.markers).toEqual([]);
    expect(withNextBar.markers[0]).toMatchObject({ action: 'BUY', price: 15, time: prefix[2].time + 300 });
  });

  it('preserves every prior EMA signal and eligible fill when a later suffix is appended', () => {
    const all = bars([10, 9, 10, 11, 8, 7, 12, 14, 6, 5], [10, 9, 10, 11, 8, 6, 12, 15, 6, 8]);
    const prefix = all.slice(0, 8);
    const emaConfig = config({ fastType: 'EMA', slowType: 'EMA' });
    const prefixResult = runBacktest({ config: emaConfig, closedBars: prefix, initialCapital: 1000 });
    const fullResult = runBacktest({ config: emaConfig, closedBars: all, initialCapital: 1000 });
    const beforeLastPrefixBar = prefix.at(-1)!.time;
    expect(fullResult.signals.filter((signal) => signal.time < beforeLastPrefixBar)).toEqual(
      prefixResult.signals.filter((signal) => signal.time < beforeLastPrefixBar),
    );
    expect(fullResult.markers.filter((marker) => marker.signalTime < beforeLastPrefixBar)).toEqual(
      prefixResult.markers.filter((marker) => marker.signalTime < beforeLastPrefixBar),
    );
  });
});

describe('strategy execution and accounting', () => {
  it('fills on the next bar open across gaps and marks the full equity path', () => {
    const result = runBacktest({
      config: config(),
      closedBars: bars([10, 9, 10, 12, 8, 7], [10, 9, 10, 15, 12, 18]),
      initialCapital: 1000,
    });
    expect(result.markers.map(({ action, time, price }) => ({ action, time, price }))).toEqual([
      { action: 'BUY', time: 1_700_000_900, price: 15 },
      { action: 'EXIT', time: 1_700_001_500, price: 18 },
    ]);
    expect(result.trades).toHaveLength(1);
    expect(result.trades[0].pnl).toBeCloseTo(200);
    expect(result.metrics.trades).toBe(1);
    expect(result.metrics.winRate).toBe(1);
    expect(result.metrics.totalReturn).toBeCloseTo(0.2);
    expect(result.metrics.maxDrawdown).toBeGreaterThan(0.45);
    expect(result.metrics.profitFactor).toBeNull();
    expect(result.metrics.averageTrade).toBeCloseTo(200);
    expect(result.metrics.exposure).toBeCloseTo(2 / 6);
  });

  it('charges commission and adverse slippage on each executed notional', () => {
    const result = runBacktest({
      config: config(),
      closedBars: bars([10, 9, 10, 12, 8, 7], [10, 9, 10, 15, 12, 18]),
      initialCapital: 1000,
      costs: { commissionBps: 100, slippageBps: 50 },
    });
    const [entry, exit] = result.markers;
    expect(entry.price).toBeCloseTo(15.075);
    expect(exit.price).toBeCloseTo(17.91);
    expect(entry.costs).toBeGreaterThan(0);
    expect(exit.costs).toBeGreaterThan(0);
    expect(result.trades[0].costs).toBeCloseTo(entry.costs + exit.costs);
    expect(result.trades[0].pnl).toBeCloseTo(result.trades[0].grossPnl - result.trades[0].costs);
    expect(result.assumptions).toMatchObject({ commissionBps: 100, slippageBps: 50, execution: 'next-bar-open' });

    const fullyReserved = runBacktest({
      config: config(),
      closedBars: bars([10, 9, 10, 12], [10, 9, 10, 15]),
      initialCapital: 1000,
      costs: { commissionBps: 10_000 },
    });
    expect(fullyReserved.markers[0].costs).toBeCloseTo(500);
    expect(fullyReserved.equity[3].equity).toBeCloseTo(400);
    expect(fullyReserved.equity.every((point) => Number.isFinite(point.equity))).toBe(true);
  });

  it('accounts for short entry proceeds and cover costs with signed PnL', () => {
    const result = runBacktest({
      config: config({ direction: 'short' }),
      closedBars: bars([10, 11, 9, 8, 12, 13], [10, 11, 9, 10, 12, 8]),
      initialCapital: 100,
    });
    expect(result.markers.map((marker) => [marker.action, marker.price])).toEqual([['SELL', 10], ['COVER', 8]]);
    expect(result.trades[0].direction).toBe('short');
    expect(result.trades[0].grossPnl).toBeCloseTo(20);
    expect(result.metrics.totalReturn).toBeCloseTo(0.2);
  });

  it('stops after a short position becomes insolvent at a closed mark without fake liquidation', () => {
    const result = runBacktest({
      config: config({ direction: 'short' }),
      closedBars: bars([10, 11, 9, 30, 5, 4], [10, 11, 9, 10, 5, 4]),
      initialCapital: 100,
    });
    expect(result.status).toBe('insolvent');
    expect(result.warning).toMatch(/No broker margin model/);
    expect(result.equity).toHaveLength(4);
    expect(result.equity.at(-1)?.equity).toBeLessThan(0);
    expect(result.openPosition?.direction).toBe('short');
    expect(result.openPosition?.markPrice).toBe(30);
    expect(result.markers).toHaveLength(1);
  });

  it('reports exact multi-trade return, drawdown, exposure, and finite profit factor', () => {
    const result = runBacktest({
      config: config(),
      closedBars: bars([10, 9, 10, 11, 8, 7, 12, 14, 6, 5], [10, 9, 10, 11, 8, 6, 12, 6, 6, 8]),
      initialCapital: 1000,
    });
    expect(result.metrics.trades).toBe(2);
    expect(result.metrics.winRate).toBe(0.5);
    expect(result.metrics.totalReturn).toBeCloseTo(-0.2727272727);
    expect(result.metrics.maxDrawdown).toBeCloseTo(0.5714285714);
    expect(result.metrics.averageTrade).toBeCloseTo(-136.3636364);
    expect(result.metrics.profitFactor).toBeCloseTo(0.4);
    expect(result.metrics.exposure).toBe(0.4);
    expect(result.metrics.dateRange).toEqual({ from: 1_700_000_000, to: 1_700_002_700 });
  });

  it('returns stable empty metrics and null profit factor for all losing trades', () => {
    const empty = runBacktest({ config: config(), closedBars: [], initialCapital: 1000 });
    expect(empty.metrics).toEqual({
      trades: 0,
      winRate: 0,
      totalReturn: 0,
      maxDrawdown: 0,
      averageTrade: 0,
      profitFactor: null,
      exposure: 0,
      dateRange: null,
    });
    const oneLoss = runBacktest({
      config: config(),
      closedBars: bars([10, 9, 10, 11, 8, 7], [10, 9, 10, 11, 8, 6]),
      initialCapital: 1000,
    });
    expect(oneLoss.metrics.trades).toBe(1);
    expect(oneLoss.trades[0].pnl).toBeLessThan(0);
    expect(oneLoss.metrics.profitFactor).toBe(0);
  });
});

describe('strategy validation', () => {
  it('rejects invalid ordering, OHLC, periods, and capital instead of guessing', () => {
    const valid = bars([10, 9, 10]);
    expect(() => runBacktest({ config: config(), closedBars: [valid[1], valid[0]], initialCapital: 100 })).toThrow(/strictly increasing/);
    expect(() => runBacktest({ config: config(), closedBars: [{ ...valid[0], high: 1 }, valid[1]], initialCapital: 100 })).toThrow(/inconsistent OHLC/);
    expect(() => runBacktest({ config: config({ fastPeriod: 0 }), closedBars: valid, initialCapital: 100 })).toThrow(/fastPeriod/);
    expect(() => runBacktest({ config: config({ fastPeriod: 3, slowPeriod: 2 }), closedBars: valid, initialCapital: 100 })).toThrow(/fastPeriod must be less/);
    expect(() => runBacktest({ config: config(), closedBars: valid, initialCapital: 0 })).toThrow(/initialCapital/);
    expect(() => runBacktest({ config: config(), closedBars: valid, initialCapital: 100, costs: { slippageBps: 10_000 } })).toThrow(/slippageBps/);
    expect(() => runBacktest({ config: config(), closedBars: valid, initialCapital: 100, costs: { commissionBps: 10_001 } })).toThrow(/commissionBps/);
    expect(() => runBacktest({ config: config({ symbol: '###' }), closedBars: valid, initialCapital: 100 })).toThrow();
  });

  it('rejects arithmetic overflow instead of returning non-finite equity', () => {
    const largeMark = bars([2, 1, 2, 1e308], [2, 1, 2, 1]);
    expect(() => runBacktest({ config: config(), closedBars: largeMark, initialCapital: 100 })).toThrow(/non-finite/);
  });

  it('rejects extreme trade and total return ratios instead of exposing infinity', () => {
    const closes = [0.02, 0.01, 0.02, 1e308, 4e307, 1e308];
    const opens = [0.02, 0.01, 0.02, 0.01, 4e307, 1e308];
    const closedBars = validPriceBars(closes, opens);

    expect(() => runBacktest({ config: config(), closedBars, initialCapital: 0.01 }))
      .toThrow(/non-finite trade return/);
    expect(() => runBacktest({
      config: config(),
      closedBars: validPriceBars(closes.slice(0, 5), opens.slice(0, 5)),
      initialCapital: 0.01,
    })).toThrow(/non-finite total return/);
  });

  it('rejects overflow while aggregating otherwise finite winning trade PnL', () => {
    const closes = [1e307, ...Array.from({ length: 16 }, (_, index) => index % 2 === 0 ? 0.9e307 : 1.1e307)];
    const opens = [...closes];
    for (let trade = 0; trade < 7; trade++) {
      const entryIndex = 3 + trade * 2;
      const exitIndex = entryIndex + 1;
      opens[entryIndex] = 1e307;
      opens[exitIndex] = trade % 2 === 0 ? 1.5e307 : (2 / 3) * 1e307;
    }

    expect(() => runBacktest({
      config: config(),
      closedBars: validPriceBars(closes, opens),
      initialCapital: 1e308,
    })).toThrow(/non-finite gross wins/);
  });
});
