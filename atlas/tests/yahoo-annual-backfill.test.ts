import { describe, expect, it } from 'vitest';
import { parseFinancials } from '../src/fundamentals/FinancialHistory';
import { provenanceText, periodSourceText } from '../src/ui/financials/financialsView';

const raw = (source: 'sec' | 'finmind') => ({ symbol: source === 'sec' ? 'TEST' : '2330.TW', annual: {
  end: ['2025-12-31', 'bad', '2024-12-31'], fy: [2025, 1900, 2024], src: [source, source, source],
  fieldSrc: [{ cash: 'yahoo' }, { revenue: 'yahoo' }, { interestExpense: 'yahoo', revenue: 'invented' }],
  values: { revenue: [100, 1, 90], cash: [5, null, null], interestExpense: [null, null, 3] },
} });

describe('annual field provenance', () => {
  for (const source of ['sec', 'finmind'] as const) {
    it(`sorts and filters field sources with values, and explains ${source} + Yahoo fields`, () => {
      const result = parseFinancials(raw(source));
      if (result.status !== 'ok') throw Error(result.message);
      const series = result.data.annual;
      expect(series.end).toEqual(['2024-12-31', '2025-12-31']);
      expect(series.fieldSrc).toEqual([{ interestExpense: 'yahoo' }, { cash: 'yahoo' }]);
      expect(series.src).toEqual([source, source]);
      expect(periodSourceText(series, 0)).toBe((source === 'sec' ? 'SEC' : 'FinMind') + ' ＋ Yahoo 欄位回補');
      const text = provenanceText(result.data, 'annual');
      expect(text).toContain(source === 'sec' ? 'SEC EDGAR' : 'FinMind');
      expect(text).toContain('Yahoo Finance 年度欄位回補');
      expect(text).toContain('FY2024');
      expect(text).toContain('利息費用');
      expect(text).toContain('FY2025');
      expect(text).toContain('現金');
      expect(text).toContain('其餘欄位依原列來源');
    });
  }
  it('does not attribute an absent field to Yahoo', () => {
    const data = raw('sec');
    data.annual.values.cash[0] = null as unknown as number;
    const result = parseFinancials(data);
    if (result.status !== 'ok') throw Error(result.message);
    expect(provenanceText(result.data, 'annual')).not.toContain('FY2025：');
  });
});
