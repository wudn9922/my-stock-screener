import { describe, it, expect } from 'vitest';
import { portfolioTotals } from '../src/holdings/portfolioTotals';
import { retainQuotes, retainRates } from '../src/holdings/quoteRetention';
import { valueHolding, type Holding, type FxRate } from '../src/holdings/model';
import type { MexcFutures } from '../../supabase/functions/user-settings/mexcFuturesContract';
const h = (market: Holding['market'], symbol = 'TEST'): Holding => ({
  id: crypto.randomUUID(),
  accountId: crypto.randomUUID(),
  symbol,
  market,
  quantity: '10',
  averageCost: '5',
  costCurrency: market === 'TW' ? 'TWD' : market === 'US' ? 'USD' : 'USDT',
  source: 'manual',
  updatedAt: '2026-11-08T00:00:00Z',
});
const quote = { price: '10', previous: null, source: 'fixture', asOf: '2026-11-08T00:00:00Z' };
const row = (market: Holding['market']) => {
  const holding = h(market);
  return { holding, result: valueHolding(holding, quote) };
};
const futures: MexcFutures = {
  status: 'ok',
  account: { equity: '110', available: '70', positionMargin: '30', unrealized: '10' },
  positions: [],
  skippedPositions: 0,
};
const rates: FxRate[] = [
  { currency: 'USD', twd: '32', asOf: '2026-11-08' },
  { currency: 'USDT', twd: '31.68', asOf: '2026-11-07' },
];
describe('retained quote snapshots', () => {
  it('keeps BB when a single successful batch omits it, with its actual old time, and replaces it on recovery', () => {
    const previous = { 'BB/USDT': quote, 'BTC/USDT': quote };
    const partial = retainQuotes(previous, { 'BTC/USDT': { ...quote, price: '11' } });
    expect(partial.quotes['BB/USDT']).toEqual(quote);
    expect(partial.retained).toEqual(['BB/USDT']);
    const recovered = retainQuotes(partial.quotes, {
      'BB/USDT': { ...quote, price: '12', asOf: '2026-11-08T00:01:00Z' },
    });
    expect(recovered.quotes['BB/USDT']!.price).toBe('12');
    expect(recovered.retained).not.toContain('BB/USDT');
    expect(previous['BB/USDT'].price).toBe('10');
  });
  it('rejects an older price and keeps FX dates when this refresh has no rate', () => {
    expect(
      retainQuotes({ BB: quote }, { BB: { ...quote, price: '1', asOf: '2026-11-07' } }).quotes.BB,
    ).toEqual(quote);
    expect(retainRates(rates, [])).toEqual(rates);
  });
});
describe('separate Taiwan and US/crypto totals', () => {
  it('keeps TWD separate and converts USDT with the same two dated TWD rates, counting equity exactly once', () => {
    const result = portfolioTotals(
      [row('TW'), row('US'), row('CRYPTO')],
      [{ id: 'mexc', data: futures }],
      rates,
    );
    expect(result.tw).toMatchObject({ value: '100', cost: '50', profit: '50', percent: '100' });
    expect(result.factor).toBe('0.99');
    expect(result.foreign).toMatchObject({ value: '307.9', cost: '198.5', profit: '109.4' });
    expect(result.usdt).toMatchObject({ value: '210', cost: '150', profit: '60' });
    expect(result.foreign.percent).toBe('55.113350125944584383');
  });
  it('never assumes USDT=USD when FX is absent and preserves the complete native subtotal', () => {
    const result = portfolioTotals(
      [row('US'), row('CRYPTO')],
      [{ id: 'mexc', data: futures }],
      rates.slice(0, 1),
    );
    expect(result.factor).toBeNull();
    expect(result.foreign.value).toBe('100');
    expect(result.usdt.value).toBe('210');
  });
  it.each([undefined, { status: 'unavailable' as const }, { status: 'no-permission' as const }])(
    'marks unread contracts excluded: %j',
    (data) => {
      const result = portfolioTotals([], [{ id: 'mexc', data }], rates);
      expect(result.excludedFutures).toBe(1);
      expect(result.usdt.value).toBe('0');
    },
  );
  it('excludes failed retained futures from totals while the position view can retain them; no position-level double counting', () => {
    expect(portfolioTotals([], [{ id: 'mexc', data: futures, failed: true }], rates).usdt.value).toBe('0');
    const extra = {
      ...futures,
      positions: Array.from({ length: 6 }, () => ({ unrealized: '9999' })),
    } as MexcFutures;
    expect(
      portfolioTotals(
        [],
        [
          { id: 'a', data: extra },
          { id: 'b', data: futures },
        ],
        rates,
      ).usdt.value,
    ).toBe('220');
  });
  it('marks missing quotes and zero cost returns unknown, instead of treating them as losses', () => {
    const holding = h('US');
    const result = portfolioTotals([{ holding, result: valueHolding(holding, undefined) }], [], []);
    expect(result.foreign.missing).toBe(1);
    expect(result.foreign.cost).toBe('50');
    expect(result.foreign.percent).toBeNull();
  });
});
