import { describe, it, expect } from 'vitest';
import { ALL_ACCOUNTS, resolveView, validateViewPreference, defaultViewPreference } from '../src/holdings/viewPreference';
import { holdingChartTarget } from '../src/holdings/chartTarget';
import { portfolioTotals } from '../src/holdings/portfolioTotals';
import { valueHolding, type Holding, type FxRate } from '../src/holdings/model';
import type { MexcFutures } from '../../supabase/functions/user-settings/mexcFuturesContract';

const A = '11111111-1111-4111-8111-111111111111';
const B = '22222222-2222-4222-8222-222222222222';

describe('holdings default view', () => {
  it('defaults to all accounts for users who never chose', () => {
    expect(defaultViewPreference().view).toBe(ALL_ACCOUNTS);
    expect(resolveView([A, B], defaultViewPreference().view)).toBe(ALL_ACCOUNTS);
    // A lone account is shown directly (its tools stay available) unless 全部帳號 was picked explicitly.
    expect(resolveView([A], ALL_ACCOUNTS)).toBe(A);
    expect(resolveView([A], ALL_ACCOUNTS, ALL_ACCOUNTS)).toBe(ALL_ACCOUNTS);
  });
  it('opens the remembered account, a session choice wins, and a missing account falls back to all', () => {
    expect(resolveView([A, B], B)).toBe(B);
    expect(resolveView([A, B], B, A)).toBe(A);
    expect(resolveView([A, B], B, ALL_ACCOUNTS)).toBe(ALL_ACCOUNTS);
    expect(resolveView([A], B)).toBe(ALL_ACCOUNTS);
    expect(resolveView([], A)).toBe(ALL_ACCOUNTS);
    expect(resolveView([A], A, B)).toBe(ALL_ACCOUNTS);
  });
  it('validates stored preferences', () => {
    expect(validateViewPreference({ version: 1, view: A })).toEqual({ version: 1, view: A });
    expect(validateViewPreference({ version: 1, view: 'all' }).view).toBe('all');
    expect(() => validateViewPreference({ version: 1, view: 'x' })).toThrow();
    expect(() => validateViewPreference({ version: 2, view: 'all' })).toThrow();
    expect(() => validateViewPreference(null)).toThrow();
  });
});

describe('holding symbol to chart', () => {
  it('maps TW and US symbols as Atlas expects and leaves unchartable crypto non-clickable', () => {
    expect(holdingChartTarget({ market: 'TW', symbol: '2330.TW' })).toEqual({ kind: 'chart', symbol: '2330.TW' });
    expect(holdingChartTarget({ market: 'TW', symbol: '6488.TWO' })).toEqual({ kind: 'chart', symbol: '6488.TWO' });
    expect(holdingChartTarget({ market: 'US', symbol: 'AAPL' })).toEqual({ kind: 'chart', symbol: 'AAPL' });
    expect(holdingChartTarget({ market: 'US', symbol: 'BRK-B' })).toEqual({ kind: 'chart', symbol: 'BRK-B' });
    expect(holdingChartTarget({ market: 'CRYPTO', symbol: 'BTC/USDT' })).toEqual({ kind: 'world' });
    expect(holdingChartTarget({ market: 'CRYPTO', symbol: 'ETH/USDT' })).toBeNull();
  });
});

describe('all-accounts totals', () => {
  const mk = (accountId: string, market: Holding['market'], symbol: string): Holding => ({
    id: crypto.randomUUID(),
    accountId,
    symbol,
    market,
    quantity: '10',
    averageCost: '5',
    costCurrency: market === 'TW' ? 'TWD' : market === 'US' ? 'USD' : 'USDT',
    source: 'manual',
    updatedAt: '2026-11-08T00:00:00Z',
  });
  const quote = { price: '10', previous: null, source: 'fixture', asOf: '2026-11-08T00:00:00Z' };
  const rows = [mk(A, 'TW', '2330.TW'), mk(B, 'US', 'AAPL'), mk(B, 'CRYPTO', 'BTC/USDT')].map((holding) => ({
    holding,
    result: valueHolding(holding, quote),
  }));
  const futures: MexcFutures = {
    status: 'ok',
    account: { equity: '110', available: '70', positionMargin: '30', unrealized: '10' },
    positions: [],
    skippedPositions: 0,
  };
  const rates: FxRate[] = [
    { currency: 'USD', twd: '30', asOf: '2026-11-08' },
    { currency: 'USDT', twd: '30', asOf: '2026-11-08' },
  ];
  it('sums every account once, with MEXC futures equity counted a single time', () => {
    const total = portfolioTotals(rows, [{ id: 'm', data: futures }], rates);
    expect(total.tw.value).toBe('100');
    // AAPL 100 + BTC 100 + futures equity 110 (not margin/notional again)
    expect(total.foreign.value).toBe('310');
    expect(total.foreign.cost).toBe('200'); // 50 + 50 + (110 - 10)
  });
});
