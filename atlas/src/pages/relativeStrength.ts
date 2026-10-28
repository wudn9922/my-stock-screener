import {
  sessionDate,
  getMarketProfile,
  type Bar,
} from "../market-data/MarketDataProvider";

/** Align actual session dates; never forward-fill a suspended stock or pair by array position. */
export function relativeStrength(
  stock: readonly Bar[],
  benchmark: readonly Bar[],
  symbol: string,
  benchmarkSymbol: string,
  sessions: number,
) {
  const byDay = (bars: readonly Bar[], ticker: string) => {
    const profile = getMarketProfile(ticker);
    return new Map(
      bars
        .filter(
          (bar) =>
            Number.isFinite(bar.time) &&
            Number.isFinite(bar.close) &&
            bar.close > 0,
        )
        .map((bar) => [sessionDate(bar.time, profile), bar.close]),
    );
  };
  const own = byDay(stock, symbol),
    bench = byDay(benchmark, benchmarkSymbol);
  const dates = [...own.keys()]
    .filter((day) => bench.has(day))
    .sort()
    .slice(-(Math.max(1, sessions) + 1));
  if (dates.length < 2) return null;
  const start = dates[0];
  const points = dates.map((time) => ({
    time,
    stock: (own.get(time)! / own.get(start)! - 1) * 100,
    benchmark: (bench.get(time)! / bench.get(start)! - 1) * 100,
  }));
  const last = points.at(-1)!;
  return {
    start,
    end: last.time,
    sessions: dates.length - 1,
    points,
    stockReturn: last.stock,
    benchmarkReturn: last.benchmark,
    excess: last.stock - last.benchmark,
  };
}
