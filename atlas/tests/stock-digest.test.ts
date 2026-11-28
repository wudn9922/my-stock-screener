import { describe, expect, it } from 'vitest';
import { digestText, stockDigestSchema, visibleDigest } from '../src/report/stockDigest';

describe('stock digest', () => {
  it('validates schema and filters by stock and 30-day inclusive age', () => {
    const data = stockDigestSchema.parse({ version: 1, generatedAt: '2026-11-28T00:00:00Z', stocks: {
      AAPL: [
        { kind: 'gross-margin', date: '2026-10-29', period: '2026Q3', before: 42.1, after: 38.6 },
        { kind: 'sec-restatement', date: '2026-10-28', period: '2025-12-31' },
        { kind: 'sec-restatement', date: '2026-11-29', period: '2025-12-31' },
      ],
      '2330.TW': [{ kind: 'trust-reversal', date: '2026-11-27', direction: 'sell' }],
    } });
    const visible = visibleDigest(data, 'AAPL', '2026-11-28');
    expect(visible).toHaveLength(1);
    expect(digestText(visible[0]!)).toBe('2026Q3 毛利率 42.1% → 38.6%（-3.5pp）');
    expect(visibleDigest(data, 'MSFT', '2026-11-28')).toEqual([]);
    expect(digestText(visibleDigest(data, '2330.TW', '2026-11-28', ['2026-11-27'])[0]!)).toContain('投信');
    expect(stockDigestSchema.safeParse({ ...data, version: 2 }).success).toBe(false);
    expect(stockDigestSchema.safeParse({ ...data, stocks: { AAPL: [{ kind: 'sec-restatement', date: '2026-02-30', period: '2025-12-31' }] } }).success).toBe(false);
  });
  it('keeps exactly five TW sessions across a weekend; missing calendar hides only flows', () => {
    const dates = ['2026-11-20', '2026-11-23', '2026-11-24', '2026-11-25', '2026-11-26', '2026-11-27', '2026-11-30'];
    const data = stockDigestSchema.parse({ version: 1, generatedAt: '2026-11-28T00:00:00Z', stocks: {
      '2330.TW': [
        ...dates.map(date => ({ kind: 'foreign-reversal', date, direction: 'buy' })),
        { kind: 'trust-reversal', date: '2026-11-23', direction: 'sell' },
        { kind: 'trust-reversal', date: '2026-11-20', direction: 'sell' },
        { kind: 'gross-margin', date: '2026-10-29', period: '2026Q3', before: 42, after: 39 },
      ],
    } });
    const rows = visibleDigest(data, '2330.TW', '2026-11-28', dates);
    expect(rows.filter(row => row.kind === 'foreign-reversal').map(row => row.date)).toEqual(dates.slice(1, 6));
    expect(rows.filter(row => row.kind === 'trust-reversal').map(row => row.date)).toEqual(['2026-11-23']);
    expect(visibleDigest(data, '2330.TW', '2026-11-28').map(row => row.kind)).toEqual(['gross-margin']);
  });
});
