import { getDailyBarResult, getHoldingsSymbolDirectory } from '../app/dataSources';
import { loadUniverse } from '../report/universe';
import { fetchResearch, researchUrl, worldAssetsSchema } from '../report/research';
import { researchBars } from '../report/researchCandles';
import type { BarResult } from '../market-data/MarketDataProvider';
import { calendarDate, getMarketProfile, marketDateTime, sessionDate, type MarketProfile } from '../market-data/MarketProfile';
import { marketCalendar } from '../market-data/MarketCalendar';
import { barEndTime } from '../market-data/MarketTiming';
import type { ImportSymbol } from './import';
import type { FxRate, HoldingQuote } from './model';

function closingTime(date: string, market: MarketProfile): number {
  const open = marketDateTime(date, market.sessionOpenMinutes, market);
  const session = marketCalendar.session(market, open * 1000);
  return session === undefined ? barEndTime(open, '1D', market) : session?.close ?? Infinity;
}

type ClosingData = Pick<BarResult, 'asOf' | 'source' | 'market' | 'quote' | 'dataState'> & {
  bars: Readonly<BarResult['bars']>;
};

/** The most recent two completed sessions, paired with the date convention of daily bars. */
export function holdingCloseQuote(symbol: string, data: ClosingData, now = Date.now(), requireClosingPrint = false): HoldingQuote | undefined {
  if (data.dataState === 'simulated') return undefined;
  const market = data.market ?? getMarketProfile(symbol);
  const observed = Math.min(now / 1000, data.asOf ?? now / 1000);
  if (!Number.isFinite(observed)) return undefined;
  const observedDate = sessionDate(observed, market);
  const needsConfirmedPrice = requireClosingPrint || data.source.includes(' · 今日開高低／量：');
  const source = data.source.split(/ · 今日(?:來源|開高低／量)：/, 1)[0]!;
  const candidate = data.quote;
  const quote = candidate?.symbol === symbol && Number.isFinite(candidate.asOf) && candidate.asOf <= observed
    && candidate.cacheStatus !== 'stale' && candidate.dataState !== 'simulated'
    && Number.isFinite(candidate.price) && candidate.price > 0 ? candidate : undefined;
  // Yahoo can fall back to a daily bucket timestamp. Only an observation during or after
  // the actual session can identify that day's partial or completed trading price.
  const marketDay = quote && sessionDate(quote.asOf, market);
  const close = marketDay ? closingTime(marketDay, market) : Infinity;
  const quoteDate = quote && marketDay && Number.isFinite(close) && quote.asOf <= close + 15 * 60
    && quote.asOf >= marketDateTime(marketDay, market.sessionOpenMinutes, market) ? marketDay : undefined;
  const printed = quote && quoteDate && quote.asOf >= close ? quote : undefined;
  const prices: { date: string; price: number; source: string; previous: number }[] = [];
  if (printed && quoteDate) prices.push({ date: quoteDate, price: printed.price, source: printed.source ?? source,
    previous: requireClosingPrint || printed.dataState === 'live' ? printed.price - printed.change : NaN });
  for (const bar of [...data.bars].sort((a, b) => b.time - a.time)) {
    if (!Number.isFinite(bar.time) || !Number.isFinite(bar.close) || bar.close <= 0) continue;
    const date = calendarDate(bar.time, market);
    if (date === quoteDate || date === observedDate && needsConfirmedPrice || closingTime(date, market) > observed) continue;
    if (prices.some(price => price.date === date)) continue;
    prices.push({ date, price: bar.close, source, previous: NaN });
    if (prices.length >= 3 || !printed && prices.length >= 2) break;
  }
  const [latest, previous] = prices.sort((a, b) => b.date.localeCompare(a.date));
  if (!latest) return undefined;
  return {
    price: String(latest.price), asOf: latest.date, source: latest.source.replace('證交所即時', '證交所 MIS'),
    previous: Number.isFinite(latest.previous) && latest.previous > 0 ? String(latest.previous) : previous ? String(previous.price) : null,
  };
}

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
          const close = holdingCloseQuote(symbol, result);
          if (!signal.aborted && close) quotes[symbol] = close;
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
