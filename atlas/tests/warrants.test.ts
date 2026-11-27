import { describe, expect, it } from 'vitest';
import { filterWarrants, sortWarrants, spreadPercent, warrantFileSchema, type Warrant, type WarrantQuote } from '../src/report/warrants';
const row = (code: string, type: 'call' | 'put' = 'call'): Warrant => ({ code, symbol: code + '.TW', name: code, type, strikePrice: 100, exerciseRatio: 0.01, expiryDate: '2027-01-10', lastTradingDate: '2027-01-08', issuer: 'Test', market: 'TWSE' });
const quote = (bid: number | null, ask: number | null, status: WarrantQuote['status'] = 'available'): WarrantQuote => ({ status, bid, ask, quoteTime: 1, bidSize: 1, askSize: 1 });
describe('warrant report and spread', () => {
  it('validates official per-underlying files and preserves leading zeros', () => {
    const file = { version: 1, underlying: '2330.TW', asOf: { TWSE: '2026-11-26', TPEx: '2026-11-26' }, warrants: [row('066186')] };
    expect(warrantFileSchema.parse(file).warrants[0]?.code).toBe('066186');
    expect(warrantFileSchema.safeParse({ ...file, warrants: [row('066186'), row('066186')] }).success).toBe(false);
  });
  it('sorts two-sided midpoint spreads and puts missing or stale at the end', () => {
    const rows = [row('000001'), row('000002'), row('000003'), row('000004')];
    expect(spreadPercent(quote(0.68, 0.69))).toBeCloseTo(1.45985);
    expect(spreadPercent(quote(1, null))).toBeNull();
    expect(spreadPercent(quote(2, 1))).toBeNull();
    expect(spreadPercent(quote(1, 1.1, 'stale'))).toBeNull();
    expect(sortWarrants(rows, { '000001.TW': quote(1,null), '000002.TW': quote(0.45,0.7), '000003.TW': quote(0.68,0.69) }).map(row => row.code)).toEqual(['000003','000002','000001','000004']);
  });
  it('filters by expiry, type and last trading date', () => {
    const rows = [row('000001'), row('000002','put'), { ...row('000003'), lastTradingDate: '2026-11-26' }];
    expect(filterWarrants(rows, 'call','2027-01-01','2027-01-31','2026-11-27').map(row => row.code)).toEqual(['000001']);
  });
});
