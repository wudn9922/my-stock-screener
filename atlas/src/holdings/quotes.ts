import { getDailyBarResult, getHoldingsSymbolDirectory } from '../app/dataSources';
import { loadUniverse } from '../report/universe';
import { fetchResearch, researchUrl, worldAssetsSchema } from '../report/research';
import { researchBars } from '../report/researchCandles';
import { sessionDate, getMarketProfile } from '../market-data/MarketDataProvider';
import type { ImportSymbol } from './import';
import type { FxRate, HoldingQuote } from './model';

export async function loadHoldingsDirectory(): Promise<ImportSymbol[]> {
  const [entries, result] = await Promise.all([getHoldingsSymbolDirectory(), loadUniverse().catch(() => null)]);
  const map = new Map<string, ImportSymbol>(entries.map((entry) => [entry.symbol, entry]));
  if (result?.status === 'ok')
    for (const market of ['TW', 'US'] as const)
      for (const item of result.universe.markets[market]?.items ?? []) {
        map.set(item.symbol, { symbol: item.symbol, name: item.name, market, etf: map.get(item.symbol)?.etf ?? false });
      }
  return [...map.values()];
}
export async function loadHoldingQuotes(
  symbols: string[],
  signal: AbortSignal,
): Promise<{ quotes: Record<string, HoldingQuote>; rates: FxRate[] }> {
  const quotes: Record<string, HoldingQuote> = {},
    rates: FxRate[] = [];
  const stocks = [...new Set(symbols.filter((symbol) => !symbol.includes('/')))];
  // Bounded parallelism: a large portfolio must not flood the existing quote service.
  let cursor = 0;
  const world = fetchResearch('world-assets', worldAssetsSchema, signal).catch(() => null);
  await Promise.all(
    Array.from({ length: Math.min(4, stocks.length) }, async () => {
      while (cursor < stocks.length && !signal.aborted) {
        const symbol = stocks[cursor++]!;
        try {
          const result = await getDailyBarResult(symbol);
          if (signal.aborted || result.dataState === 'simulated') continue;
          const bars = result.bars
            .filter((bar) => Number.isFinite(bar.close) && bar.close > 0)
            .sort((a, b) => a.time - b.time);
          const latest = bars.at(-1);
          if (!latest) continue;
          const profile = result.market ?? getMarketProfile(symbol);
          const latestDate = sessionDate(latest.time, profile);
          const previous = [...bars].reverse().find((bar) => sessionDate(bar.time, profile) < latestDate);
          quotes[symbol] = {
            price: String(latest.close),
            previous: previous ? String(previous.close) : null,
            asOf: latestDate,
            source: result.source,
          };
        } catch {
          /* Quote failures never reveal private inputs or replace a missing quote with zero. */
        }
      }
    }),
  );
  const snapshot = await world;
  for (const asset of Object.values(snapshot?.series ?? {})) {
    if (!['ok', 'stale'].includes(asset.status) || !asset.latest || asset.latest <= 0 || !asset.asOf) continue;
    if (asset.baseCurrency === 'USD' && asset.quoteCurrency === 'TWD')
      rates.push({ currency: 'USD', twd: String(asset.latest), asOf: asset.asOf });
    if (asset.baseCurrency === 'USDT' && asset.quoteCurrency === 'TWD')
      rates.push({ currency: 'USDT', twd: String(asset.latest), asOf: asset.asOf });
    if (
      asset.quoteCurrency === 'USDT' &&
      asset.baseCurrency &&
      asset.instrumentType === 'crypto-spot' &&
      asset.symbol === `CRYPTO-${asset.baseCurrency}USDT` &&
      symbols.includes(`${asset.baseCurrency}/USDT`)
    ) {
      let previous =
        asset.history
          ?.filter((row) => row[0] < asset.asOf!)
          .sort((a, b) => a[0].localeCompare(b[0]))
          .at(-1)?.[1] ?? null;
      if (asset.candlePath)
        try {
          const response = await fetch(researchUrl(asset.candlePath.replace(/\.json$/, '')), {
            signal,
            cache: 'no-cache',
          });
          if (!response.ok) throw new Error();
          const body = await response.json(),
            bars = researchBars(body, asset.symbol);
          if (body.bars.at(-1)?.[0] !== asset.asOf || bars.at(-1)?.close !== asset.latest) throw new Error();
          previous =
            [...bars].reverse().find((bar) => new Date(bar.time * 1000).toISOString().slice(0, 10) < asset.asOf!)
              ?.close ?? null;
        } catch {
          /* Still show the validated snapshot price, with today's change unavailable. */
        }
      quotes[`${asset.baseCurrency}/USDT`] = {
        price: String(asset.latest),
        previous: previous === null ? null : String(previous),
        asOf: asset.asOf,
        source: asset.source,
      };
    }
  }
  return { quotes, rates };
}
