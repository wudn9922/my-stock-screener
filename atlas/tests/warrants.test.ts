import { describe, expect, it } from 'vitest';
import { currentWarrantQuotes, filterWarrants, sortWarrants, spreadPercent, warrantFileSchema, warrantQuotesSchema, type Warrant, type WarrantQuote } from '../src/report/warrants';
const row = (code: string, type: 'call' | 'put' = 'call'): Warrant => ({ code, symbol: code + '.TW', name: code, type, strikePrice: 100, exerciseRatio: 0.01, expiryDate: '2027-01-10', lastTradingDate: '2027-01-08', issuer: 'Test', market: 'TWSE' });
const quote = (bid: number | null, ask: number | null, status: WarrantQuote['status'] = 'available'): WarrantQuote => ({ status, bid, ask, quoteTime: 1, bidSize: 1, askSize: 1 });
describe('warrant report and spread', () => {
  it('validates official per-underlying files and preserves leading zeros', () => {
    const file = { version: 1, underlying: '2330.TW', asOf: { TWSE: '2026-11-26', TPEx: '2026-11-26' }, warrants: [row('066186')] };
    expect(warrantFileSchema.parse(file).warrants[0]?.code).toBe('066186');
    expect(warrantFileSchema.safeParse({ ...file, warrants: [row('066186'), row('066186')] }).success).toBe(false);
    expect(warrantFileSchema.parse({ ...file, underlying: 'IX0007' }).underlying).toBe('IX0007');
    expect(warrantFileSchema.safeParse({ ...file, warrants: [{ ...row('066186'), symbol: '066186.TWO' }] }).success).toBe(false);
  });
  it('sorts two-sided midpoint spreads and puts missing or stale at the end', () => {
    const rows = [row('000001'), row('000002'), row('000003'), row('000004')];
    expect(spreadPercent(quote(0.68, 0.69))).toBeCloseTo(1.45985);
    expect(spreadPercent(quote(1, null))).toBeNull();
    expect(spreadPercent(quote(null, 1))).toBeNull();
    expect(spreadPercent(quote(0, 1))).toBeNull();
    expect(spreadPercent(quote(2, 1))).toBeNull();
    expect(spreadPercent(quote(1, 1.1, 'stale'))).toBeNull();
    expect(sortWarrants(rows, { '000001.TW': quote(1,null), '000002.TW': quote(0.45,0.7), '000003.TW': quote(0.68,0.69) }).map(row => row.code)).toEqual(['000003','000002','000001','000004']);
  });
  it('filters by expiry, type and last trading date', () => {
    const rows = [row('000001'), row('000002','put'), { ...row('000003'), lastTradingDate: '2026-11-26' }];
    expect(filterWarrants(rows, 'call','2027-01-01','2027-01-31','2026-11-27').map(row => row.code)).toEqual(['000001']);
  });
  it('reads the W4 response with real Unix seconds, market state and missing books', () => {
    const response = warrantQuotesSchema.parse({
      asOf: 1795751062, source: 'twse-mis', session: 'regular',
      results: {
        '73285U.TWO': { status: 'available', bid: 1.31, bidSize: 499, ask: 1.39, askSize: 299, quoteTime: 1795751061 },
        '04917T.TW': { status: 'stale', bid: null, bidSize: null, ask: null, askSize: null, quoteTime: 1795748964 },
        '066186.TW': { status: 'available', bid: 0, bidSize: 8, ask: 0.64, askSize: 10, quoteTime: 1795751002 },
      },
    });
    expect(response.results['066186.TW']).toMatchObject({ bid: null, bidSize: null, ask: 0.64 });
    expect(spreadPercent(response.results['73285U.TWO'])).toBeCloseTo(5.9259);
    expect(spreadPercent(response.results['04917T.TW'])).toBeNull();
  });
  it('retires a previously available book after 60 seconds or after its local trading date', () => {
    const session = 'regular' as const;
    const time = Date.parse('2026-11-27T11:44:21+08:00') / 1000;
    const q = { ...quote(1.31, 1.39), quoteTime: time };
    const samples = { '73285U.TWO': { book: q, session, asOf: time + 1 } };
    expect(currentWarrantQuotes(samples, (time + 60) * 1000)['73285U.TWO']).toEqual(q);
    expect(currentWarrantQuotes(samples, (time + 61) * 1000)['73285U.TWO']).toMatchObject({ status: 'stale', bid: null, ask: null, quoteTime: time });
    const close = Date.parse('2026-11-27T13:30:00+08:00') / 1000;
    const closed = { '73285U.TWO': { book: { ...q, quoteTime: close }, session: 'closed' as const, asOf: close + 60 } };
    expect(currentWarrantQuotes(closed, (close + 7_200) * 1000)['73285U.TWO']?.status).toBe('available');
    expect(currentWarrantQuotes(closed, (close + 43_200) * 1000)['73285U.TWO']?.status).toBe('stale');
    closed['73285U.TWO'].book.quoteTime -= 60;
    expect(currentWarrantQuotes(closed, (close + 60) * 1000)['73285U.TWO']?.status).toBe('stale');
  });
  it('excludes impossible timestamps or completely absent sides from ranking', () => {
    const time = Date.parse('2026-11-27T11:44:21+08:00') / 1000;
    const samples = {
      '066186.TW': { book: { ...quote(0.68, 0.69), quoteTime: time + 10 }, session: 'regular' as const, asOf: time },
      '066187.TW': { book: { ...quote(0.68, 0.69), quoteTime: null }, session: 'regular' as const, asOf: time },
      '066188.TW': { book: { ...quote(null, null), quoteTime: time }, session: 'regular' as const, asOf: time },
    };
    expect(Object.values(currentWarrantQuotes(samples, time * 1000)).map(value => value.status)).toEqual(['no_quote', 'no_quote', 'no_quote']);
    const unavailable = { '066186.TW': { book: { ...quote(1, 1.2, 'source_unavailable'), quoteTime: null }, session: 'regular' as const, asOf: time } };
    expect(currentWarrantQuotes(unavailable, time * 1000)['066186.TW']).toMatchObject({ status: 'source_unavailable', bid: null, bidSize: null, ask: null, askSize: null });
  });
});
