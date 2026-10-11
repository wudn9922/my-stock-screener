import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { parseFinancials, type FinancialHistory, type RawField } from '../src/fundamentals/FinancialHistory';
import {
  METRICS,
  METRIC_GROUPS,
  cagr,
  consecutiveQuarters,
  contextOf,
  debtToEquity,
  effectiveTaxRate,
  eps,
  fcfMargin,
  fcfToNetIncome,
  fiscalQuarterOf,
  fiscalYearOf,
  freeCashFlow,
  grossMargin,
  inferFyEndMonth,
  interestCoverage,
  metricSeries,
  netMargin,
  nonOperatingIncome,
  nonOperatingShare,
  operatingMargin,
  periodLabels,
  roe,
  roic,
  ttm,
  windowAverage,
  windowCagr,
  yearAgoIndex,
  yoy,
  type MetricContext,
} from '../src/fundamentals/financialMetrics';

type Row = { end: string } & Partial<Record<RawField, number | null>>;

/** Small hand-checkable histories built through the real parser. */
function history(annual: Row[], quarterly: Row[] = [], extra: Record<string, unknown> = {}): FinancialHistory {
  const block = (rows: Row[]) => {
    const values: Record<string, (number | null)[]> = {};
    for (const row of rows)
      for (const key of Object.keys(row)) if (key !== 'end') values[key] = rows.map((r) => (r as unknown as Record<string, number | null>)[key] ?? null);
    return { end: rows.map((row) => row.end), values };
  };
  const result = parseFinancials({ version: 1, symbol: 'TEST', annual: block(annual), quarterly: block(quarterly), ...extra });
  if (result.status !== 'ok') throw new Error(result.message);
  return result.data;
}
const annualCtx = (rows: Row[], extra: Record<string, unknown> = {}): MetricContext =>
  contextOf(history(rows, [], extra), 'annual');
const quarterlyCtx = (rows: Row[], extra: Record<string, unknown> = {}): MetricContext =>
  contextOf(history([], rows, extra), 'quarterly');
const fixture = (name: string) => {
  const result = parseFinancials(JSON.parse(readFileSync(resolve(import.meta.dirname, 'fixtures/financials', name), 'utf8')));
  if (result.status !== 'ok') throw new Error(name);
  return result.data;
};

describe('margins', () => {
  it('gross margin: gross profit first, then revenue − cost, revenue ≤ 0 → null', () => {
    const ctx = annualCtx([
      { end: '2021-12-31', revenue: 100, grossProfit: 40, costOfRevenue: 99 },
      { end: '2022-12-31', revenue: 200, costOfRevenue: 140 },
      { end: '2023-12-31', revenue: 0, grossProfit: 10 },
      { end: '2024-12-31', revenue: -5, grossProfit: 1 },
      { end: '2025-12-31', revenue: 50 },
    ]);
    expect(grossMargin(ctx, 0)).toMatchObject({ value: 40, basis: 'reported', formula: '毛利 ÷ 營收' });
    const derived = grossMargin(ctx, 1);
    expect(derived.value).toBeCloseTo(30);
    expect(derived.basis).toBe('derived');
    expect(derived.inputs.map((i) => i.value)).toEqual([200, 140]);
    expect(grossMargin(ctx, 2)).toMatchObject({ value: null, reason: '營收 ≤ 0，不計算' });
    expect(grossMargin(ctx, 3).value).toBeNull();
    expect(grossMargin(ctx, 4).reason).toContain('金融業');
  });

  it('operating margin prefers the as-reported operating income and says which was used', () => {
    const ctx = annualCtx([
      { end: '2023-12-31', revenue: 200, operatingIncomeReported: 50, operatingIncome: 40 },
      { end: '2024-12-31', revenue: 200, operatingIncome: 40 },
    ]);
    expect(operatingMargin(ctx, 0)).toMatchObject({ value: 25, oiField: 'operatingIncomeReported', notes: [] });
    const fallback = operatingMargin(ctx, 1);
    expect(fallback).toMatchObject({ value: 20, oiField: 'operatingIncome' });
    expect(fallback.formula).toContain('標準化');
    expect(fallback.notes[0]).toContain('標準化');
  });

  it('net margin', () => {
    const ctx = annualCtx([{ end: '2024-12-31', revenue: 400, netIncome: -20 }]);
    expect(netMargin(ctx, 0).value).toBe(-5);
  });
});

describe('non-operating items and tax', () => {
  const ctx = annualCtx([
    { end: '2020-12-31', pretaxIncome: 30, operatingIncomeReported: 25, operatingIncome: 99, taxProvision: 6 },
    { end: '2021-12-31', pretaxIncome: 20, operatingIncome: 25, taxProvision: -2 },
    { end: '2022-12-31', pretaxIncome: -10, operatingIncome: -4, taxProvision: 1 },
    { end: '2023-12-31', pretaxIncome: 10, operatingIncome: 8, taxProvision: 6 },
    { end: '2024-12-31', pretaxIncome: 10, operatingIncome: 8, taxProvision: 7 },
    { end: '2025-12-31', pretaxIncome: 10 },
  ]);
  it('non-operating income = pretax − operating income, may be negative', () => {
    expect(nonOperatingIncome(ctx, 0)).toMatchObject({ value: 5, oiField: 'operatingIncomeReported' });
    expect(nonOperatingIncome(ctx, 1).value).toBe(-5);
    expect(nonOperatingIncome(ctx, 5).value).toBeNull();
  });
  it('share of pretax only when pretax > 0', () => {
    expect(nonOperatingShare(ctx, 0).value).toBeCloseTo(16.667, 2);
    expect(nonOperatingShare(ctx, 1).value).toBe(-25);
    expect(nonOperatingShare(ctx, 2)).toMatchObject({ value: null, reason: '稅前淨利 ≤ 0，不計算占比' });
  });
  it('effective tax rate: pretax > 0 only; below 0% or above 60% flagged abnormal', () => {
    expect(effectiveTaxRate(ctx, 0)).toMatchObject({ value: 20, notes: [] });
    expect(effectiveTaxRate(ctx, 0).abnormal).toBeUndefined();
    expect(effectiveTaxRate(ctx, 1)).toMatchObject({ value: -10, abnormal: true, notes: ['異常（一次性稅務項目）'] });
    expect(effectiveTaxRate(ctx, 2).value).toBeNull();
    expect(effectiveTaxRate(ctx, 3).value).toBe(60);
    expect(effectiveTaxRate(ctx, 3).abnormal).toBeUndefined();
    expect(effectiveTaxRate(ctx, 4)).toMatchObject({ value: 70, abnormal: true });
    expect(effectiveTaxRate(ctx, 5).reason).toBe('缺所得稅費用');
  });
});

describe('EPS and cash flow', () => {
  it('EPS uses diluted, else basic', () => {
    const ctx = annualCtx([
      { end: '2023-12-31', epsDiluted: 1.9, epsBasic: 2 },
      { end: '2024-12-31', epsBasic: 2.1 },
      { end: '2025-12-31' },
    ]);
    expect(eps(ctx, 0)).toMatchObject({ value: 1.9, basis: 'reported' });
    expect(eps(ctx, 1)).toMatchObject({ value: 2.1, basis: 'basic' });
    expect(eps(ctx, 2).value).toBeNull();
  });

  it('free cash flow, its margin and FCF ÷ net income (net income > 0 only)', () => {
    const ctx = annualCtx([
      { end: '2023-12-31', operatingCashFlow: 100, capex: 30, revenue: 350, netIncome: 50, fcfYahoo: 70 },
      { end: '2024-12-31', operatingCashFlow: 100, capex: 30, revenue: 350, netIncome: -10, fcfYahoo: 90 },
      { end: '2025-12-31', operatingCashFlow: 100, revenue: 350 },
    ]);
    expect(freeCashFlow(ctx, 0)).toMatchObject({ value: 70, notes: [] });
    expect(freeCashFlow(ctx, 0).inputs.map((i) => i.label)).toContain('Yahoo 自由現金流');
    expect(freeCashFlow(ctx, 1).notes[0]).toContain('5%');
    expect(fcfMargin(ctx, 0).value).toBe(20);
    expect(fcfToNetIncome(ctx, 0).value).toBe(140);
    expect(fcfToNetIncome(ctx, 1)).toMatchObject({ value: null, reason: '淨利 ≤ 0，不計算' });
    expect(freeCashFlow(ctx, 2)).toMatchObject({ value: null, reason: '缺資本支出' });
    expect(fcfMargin(ctx, 2).value).toBeNull();
  });
});

describe('ROE', () => {
  it('annual: average of this and the previous year-end equity; first year approximates', () => {
    const ctx = annualCtx([
      { end: '2022-12-31', netIncome: 10, equity: 60 },
      { end: '2023-12-31', netIncome: 20, equity: 100 },
      { end: '2025-12-31', netIncome: 20, equity: 100 },
    ]);
    expect(roe(ctx, 0)).toMatchObject({ value: (10 / 60) * 100, basis: 'approx' });
    expect(roe(ctx, 1)).toMatchObject({ value: 25, basis: 'reported' });
    // No period 365 ± 25 days earlier (two-year gap) → end equity only.
    expect(roe(ctx, 2)).toMatchObject({ value: 20, basis: 'approx' });
  });

  it('negative equity in either period → null', () => {
    const ctx = annualCtx([
      { end: '2022-12-31', netIncome: 10, equity: -5 },
      { end: '2023-12-31', netIncome: 10, equity: 50 },
      { end: '2024-12-31', netIncome: 10, equity: -1 },
    ]);
    expect(roe(ctx, 0).value).toBeNull();
    expect(roe(ctx, 1)).toMatchObject({ value: null, reason: '股東權益 ≤ 0（負權益），不計算' });
    expect(roe(ctx, 2).value).toBeNull();
    expect(debtToEquity(ctx, 2).value).toBeNull();
  });

  it('quarterly: TTM net income over the average of this and the year-ago quarter equity', () => {
    const ctx = quarterlyCtx([
      { end: '2023-12-31', netIncome: 1, equity: 80 },
      { end: '2024-03-31', netIncome: 2, equity: 90 },
      { end: '2024-06-30', netIncome: 3, equity: 95 },
      { end: '2024-09-30', netIncome: 4, equity: 110 },
      { end: '2024-12-31', netIncome: 5, equity: 120 },
    ]);
    expect(roe(ctx, 2)).toMatchObject({ value: null, reason: '需要連續 4 季淨利（TTM）' });
    // No quarter a year before 2024-09-30: TTM 10 over end equity 110 only.
    expect(roe(ctx, 3)).toMatchObject({ value: (10 / 110) * 100, basis: 'approx' });
    expect(roe(ctx, 4).basis).toBe('ttm');
    expect(roe(ctx, 4).value).toBeCloseTo(14, 9); // (2+3+4+5) ÷ ((120+80)/2)
  });

  it('quarterly TTM needs four consecutive quarters', () => {
    const ctx = quarterlyCtx([
      { end: '2023-12-31', netIncome: 1, equity: 80 },
      { end: '2024-03-31', netIncome: 2, equity: 90 },
      // 2024-06-30 missing
      { end: '2024-09-30', netIncome: 4, equity: 110 },
      { end: '2024-12-31', netIncome: 5, equity: 120 },
      { end: '2025-03-31', netIncome: null, equity: 120 },
    ]);
    expect(roe(ctx, 3).value).toBeNull();
    expect(roe(ctx, 4).value).toBeNull();
  });
});

describe('ROIC', () => {
  const rows: Row[] = [
    { end: '2023-12-31', operatingIncome: 40, pretaxIncome: 40, taxProvision: 8, totalDebt: 20, equity: 80, cash: 20 },
    { end: '2024-12-31', operatingIncome: 50, pretaxIncome: 50, taxProvision: 10, totalDebt: 30, equity: 100, cash: 30 },
  ];
  it('NOPAT with the effective rate over average invested capital', () => {
    const value = roic(annualCtx(rows), 1);
    // τ = 20%, NOPAT = 40, IC = 30 + 100 − 30 = 100, previous IC = 80 → average 90.
    expect(value.value).toBeCloseTo((40 / 90) * 100, 6);
    expect(value.basis).toBe('reported');
    expect(value.inputs.find((i) => i.label === '稅率')!.value).toBeCloseTo(20);
    expect(value.notes.join()).toContain('租賃負債');
    expect(roic(annualCtx(rows), 0)).toMatchObject({ basis: 'approx' });
  });

  it('clamps the effective rate to [0, 35%]', () => {
    const high = annualCtx([{ ...rows[1]!, taxProvision: 25 }]);
    expect(roic(high, 0).value).toBeCloseTo(32.5, 6); // 50 × (1 − 35%) ÷ 100
    const negative = annualCtx([{ ...rows[1]!, taxProvision: -5 }]);
    expect(roic(negative, 0).value).toBeCloseTo(50, 6);
  });

  it('falls back to the statutory rate: US 21%, Taiwan 20%', () => {
    const lossBeforeTax = { ...rows[1]!, pretaxIncome: -5 };
    const us = roic(annualCtx([lossBeforeTax]), 0);
    expect(us).toMatchObject({ basis: 'statutory-tax' });
    expect(us.value).toBeCloseTo(39.5, 6);
    const tw = roic(annualCtx([lossBeforeTax], { symbol: '2330.TW', market: 'TW' }), 0);
    expect(tw.value).toBeCloseTo(40, 6);
    const noTax = roic(annualCtx([{ ...rows[1]!, taxProvision: null }]), 0);
    expect(noTax.basis).toBe('statutory-tax');
  });

  it('invested capital ≤ 0 (this or previous period) → null with an explanation', () => {
    const cashRich = annualCtx([
      { ...rows[0]!, cash: 200 },
      { ...rows[1]!, cash: 10 },
    ]);
    expect(roic(cashRich, 0)).toMatchObject({ value: null });
    expect(roic(cashRich, 0).reason).toContain('投入資本');
    expect(roic(cashRich, 1).value).toBeNull();
  });

  it('missing debt or cash counts as zero and is marked approximate', () => {
    const value = roic(annualCtx([{ ...rows[0]! }, { ...rows[1]!, totalDebt: null }]), 1);
    // IC = 0 + 100 − 30 = 70, previous 80 → average 75.
    expect(value.value).toBeCloseTo((40 / 75) * 100, 6);
    expect(value.basis).toBe('approx');
  });

  it('quarterly ROIC uses TTM operating income and tax', () => {
    const quarters: Row[] = ['2023-12-31', '2024-03-31', '2024-06-30', '2024-09-30', '2024-12-31'].map((end) => ({
      end,
      operatingIncome: 10,
      pretaxIncome: 10,
      taxProvision: 2,
      totalDebt: 30,
      equity: 100,
      cash: 30,
    }));
    const value = roic(quarterlyCtx(quarters), 4);
    expect(value.value).toBeCloseTo(32, 6); // 40 × 0.8 ÷ 100
    expect(value.basis).toBe('ttm');
    expect(roic(quarterlyCtx(quarters), 2).value).toBeNull();
  });
});

describe('balance sheet strength', () => {
  it('debt ÷ equity and interest coverage (interest > 0 only)', () => {
    const ctx = annualCtx([
      { end: '2023-12-31', totalDebt: 50, equity: 100, operatingIncomeReported: 60, interestExpense: 5 },
      { end: '2024-12-31', totalDebt: 50, equity: 100, operatingIncome: 60, interestExpense: 0 },
    ]);
    expect(debtToEquity(ctx, 0).value).toBe(0.5);
    expect(interestCoverage(ctx, 0)).toMatchObject({ value: 12, oiField: 'operatingIncomeReported' });
    expect(interestCoverage(ctx, 1)).toMatchObject({ value: null, reason: '利息費用 ≤ 0，不計算' });
  });
});

describe('year over year', () => {
  it('matches the period 365 ± 25 days earlier, closest wins', () => {
    expect(yearAgoIndex(['2023-09-30', '2024-09-28'], 1)).toBe(0); // 52/53-week: 364 days
    expect(yearAgoIndex(['2022-12-31', '2024-12-31'], 1)).toBeNull();
    expect(yearAgoIndex(['2023-11-30', '2023-12-31', '2024-01-31', '2024-12-31'], 3)).toBe(1);
    expect(yearAgoIndex(['2023-12-07', '2024-12-31'], 1)).toBe(0); // 390 days (leap year)
    expect(yearAgoIndex(['2023-12-06', '2024-12-31'], 1)).toBeNull(); // 391 days
    expect(yearAgoIndex(['2024-01-26', '2024-12-31'], 1)).toBe(0); // 340 days
    expect(yearAgoIndex(['2024-01-27', '2024-12-31'], 1)).toBeNull(); // 339 days
  });

  it('growth in percent with 轉盈 / 轉虧 / 續虧 on sign changes', () => {
    const ends = ['2021-12-31', '2022-12-31', '2023-12-31', '2024-12-31', '2025-12-31'];
    const values = [-10, 20, -5, -8, 4];
    expect(yoy(values, ends, 0, 'money')).toMatchObject({ value: null, baseIndex: null });
    expect(yoy(values, ends, 1, 'money')).toMatchObject({ value: null, label: '轉盈', baseIndex: 0 });
    expect(yoy(values, ends, 2, 'money')).toMatchObject({ value: -125, label: '轉虧' });
    expect(yoy(values, ends, 3, 'eps')).toMatchObject({ value: null, label: '續虧' });
    expect(yoy([100, 120], ends, 1, 'money')).toMatchObject({ value: 20, label: null, mode: 'growth' });
  });

  it('percentages and multiples change by difference', () => {
    expect(yoy([40, 43.5], ['2023-12-31', '2024-12-31'], 1, 'pct')).toMatchObject({ value: 3.5, mode: 'diff' });
    expect(yoy([-2, 1], ['2023-12-31', '2024-12-31'], 1, 'times')).toMatchObject({ value: 3, label: null });
  });

  it('quarterly YoY skips adjacent quarters', () => {
    const ends = ['2024-03-31', '2024-06-30', '2024-09-30', '2024-12-31', '2025-03-31'];
    expect(yoy([10, 1, 1, 1, 15], ends, 4, 'money').value).toBe(50);
    expect(yoy([10, 1, 1, 1, 15], ends, 3, 'money').value).toBeNull();
  });
});

describe('CAGR', () => {
  it('uses the actual years between period ends', () => {
    const tenYears = cagr({ value: 100, end: '2014-12-31' }, { value: 200, end: '2024-12-31' });
    expect(tenYears.years).toBeCloseTo(3653 / 365.25, 9);
    expect(tenYears.value).toBeCloseTo((Math.pow(2, 365.25 / 3653) - 1) * 100, 9);
    const weeks = cagr({ value: 100, end: '2020-09-26' }, { value: 150, end: '2024-09-28' });
    expect(weeks.years).toBeCloseTo(1463 / 365.25, 9);
  });

  it('a loss at either end → null 「期間有虧損，無法計算」', () => {
    expect(cagr({ value: -5, end: '2020-12-31' }, { value: 10, end: '2024-12-31' })).toMatchObject({
      value: null,
      reason: '期間有虧損，無法計算',
    });
    expect(cagr({ value: 5, end: '2020-12-31' }, { value: -1, end: '2024-12-31' }).value).toBeNull();
    expect(cagr({ value: 0, end: '2020-12-31' }, { value: 1, end: '2024-12-31' }).value).toBeNull();
    // A loss in between does not matter: only the end points enter the formula.
    const values = [10, -3, 20];
    const ends = ['2022-12-31', '2023-12-31', '2024-12-31'];
    expect(windowCagr(values, ends, [0, 1, 2]).value).toBeCloseTo((Math.sqrt(2) - 1) * 100, 1);
    expect(cagr({ value: 1, end: '2024-03-31' }, { value: 2, end: '2024-09-30' }).reason).toBe('期間不到一年');
  });

  it('window CAGR skips missing ends and can require the same fiscal quarter', () => {
    const ends = ['2023-03-31', '2023-06-30', '2023-09-30', '2023-12-31', '2024-03-31', '2024-06-30'];
    const values = [null, 10, 12, 30, 11, 20];
    const fq = [1, 2, 3, 4, 1, 2];
    const result = windowCagr(values, ends, [0, 1, 2, 3, 4, 5], fq);
    expect(result).toMatchObject({ from: '2023-06-30', to: '2024-06-30' });
    expect(result.value).toBeCloseTo(100, 0);
    expect(windowCagr([null, 5], ends, [0, 1]).reason).toBe('資料不足兩期');
    expect(windowAverage([10, null, 20], [0, 1, 2])).toEqual({ value: 15, count: 2 });
  });
});

describe('fiscal labels', () => {
  it('FY is the year of the period end, but an end on January 1–7 belongs to the previous year', () => {
    expect(fiscalYearOf('2024-12-28', 12)).toBe(2024);
    expect(fiscalYearOf('2025-01-03', 12)).toBe(2024);
    expect(fiscalYearOf('2025-01-08', 1)).toBe(2025);
    expect(fiscalYearOf('2024-09-28', 9)).toBe(2024);
    expect(fiscalYearOf('2024-01-28', 1)).toBe(2024);
  });

  it('quarters belong to the fiscal year that ends next', () => {
    expect(fiscalYearOf('2024-12-28', 9, 'quarterly')).toBe(2025);
    expect(fiscalYearOf('2023-04-01', 9, 'quarterly')).toBe(2023);
    expect(fiscalYearOf('2024-04-28', 1, 'quarterly')).toBe(2025);
    expect(fiscalYearOf('2025-01-04', 12, 'quarterly')).toBe(2024);
  });

  it('fq from fyEndMonth: round(((month − fyEndMonth + 12) % 12) / 3), 0 → Q4', () => {
    expect([fiscalQuarterOf('2024-03-31', 12), fiscalQuarterOf('2024-06-30', 12), fiscalQuarterOf('2024-09-30', 12), fiscalQuarterOf('2024-12-31', 12)]).toEqual([1, 2, 3, 4]);
    // Apple-like 52/53-week September year: 2023-04-01 is a March quarter.
    expect(['2022-12-31', '2023-04-01', '2023-07-01', '2023-09-30'].map((end) => fiscalQuarterOf(end, 9))).toEqual([1, 2, 3, 4]);
    expect(['2024-04-28', '2024-07-28', '2024-10-27', '2025-01-26'].map((end) => fiscalQuarterOf(end, 1))).toEqual([1, 2, 3, 4]);
    expect(fiscalQuarterOf('2025-01-04', 12)).toBe(4);
    expect(fiscalQuarterOf('2024-11-30', 12)).toBe(4); // m = 11 → round(3.67) = 4 → Q4
  });

  it('a quarter drifting past the fiscal year end still belongs to that year (same as the builder)', () => {
    // Q4 (m = 1 → round(1/3) = 0) ending a month after a December year end is FY of the year before.
    expect(fiscalQuarterOf('2025-01-31', 12)).toBe(4);
    expect(fiscalYearOf('2025-01-31', 12, 'quarterly')).toBe(2024);
    expect(fiscalYearOf('2024-11-30', 12, 'quarterly')).toBe(2024);
  });

  it('fyEndMonth is the most frequent annual end month', () => {
    expect(inferFyEndMonth(['2021-01-02', '2021-12-31', '2022-12-30', '2023-12-29'])).toBe(12);
    expect(inferFyEndMonth(['2022-09-24', '2023-09-30', '2024-09-28'])).toBe(9);
    expect(inferFyEndMonth(['2022-06-30', '2023-12-31'])).toBe(12);
    expect(inferFyEndMonth([])).toBeNull();
  });

  it('labels from the files', () => {
    const us = fixture('us-full.json');
    expect(periodLabels(us, 'annual').at(-1)).toMatchObject({ long: 'FY2024', short: '2024' });
    const quarters = periodLabels(us, 'quarterly');
    expect(quarters[0]).toMatchObject({ end: '2021-12-25', long: 'FY2022 Q1' });
    expect(quarters.map((q) => q.fq)).toEqual(us.quarterly.fq);
    expect(quarters.map((q) => q.fy)).toEqual(us.quarterly.fy);
    const tsmc = fixture('tsmc-twd.json');
    expect(periodLabels(tsmc, 'quarterly').map((q) => q.long).slice(-2)).toEqual(['FY2025 Q1', 'FY2025 Q2']);
    const noMonth = history([{ end: '2023-12-30' }, { end: '2025-01-04' }], [], { fyEndMonth: null });
    expect(periodLabels(noMonth, 'annual').map((p) => p.fy)).toEqual([2023, 2024]);
  });
});

describe('reconciliation with the real builder samples', () => {
  it('computed fiscal year and quarter equal the fy / fq written by build_financials.py', () => {
    for (const name of ['backend-sample-AAPL.json', 'backend-sample-2330.TW.json', 'us-full.json', 'tsmc-twd.json']) {
      const data = fixture(name);
      for (const kind of ['annual', 'quarterly'] as const) {
        const labels = periodLabels(data, kind);
        expect(labels.map((label) => label.fy), `${name} ${kind}`).toEqual(data[kind].fy);
        if (kind === 'quarterly') expect(labels.map((label) => label.fq), name).toEqual(data.quarterly.fq);
      }
      expect(inferFyEndMonth(data.annual.end)).toBe(data.fyEndMonth);
    }
  });

  it('quarters without any annual data use calendar labels (fyEndMonth 12 is only a placeholder)', () => {
    const data = history([], [{ end: '2025-03-31', revenue: 1 }, { end: '2025-06-30', revenue: 2 }], { fyEndMonth: 12 });
    expect(periodLabels(data, 'quarterly').map((label) => label.long)).toEqual(['CY2025 Q1', 'CY2025 Q2']);
    const tw = history([], [{ end: '2025-03-31', revenue: 1 }], { symbol: '2330.TW', market: 'TW', fyEndMonth: 12 });
    expect(periodLabels(tw, 'quarterly')[0]!.long).toBe('FY2025 Q1');
  });

  it('every metric of the builder samples is finite or explained', () => {
    for (const name of ['backend-sample-AAPL.json', 'backend-sample-2330.TW.json']) {
      const data = fixture(name);
      for (const kind of ['annual', 'quarterly'] as const)
        for (const id of Object.keys(METRICS) as (keyof typeof METRICS)[])
          for (const item of metricSeries(contextOf(data, kind), id)) {
            if (item.value === null) expect(item.reason, `${name} ${kind} ${id}`).toBeTruthy();
            else expect(Number.isFinite(item.value)).toBe(true);
          }
      // Both samples have full statements: margins, ROE and ROIC are available for the latest year.
      const annual = contextOf(data, 'annual');
      for (const id of ['grossMargin', 'operatingMargin', 'roe', 'roic', 'freeCashFlow'] as const)
        expect(metricSeries(annual, id).at(-1)!.value, `${name} ${id}`).not.toBeNull();
    }
  });
});

describe('TTM', () => {
  it('needs four consecutive quarters', () => {
    const ends = ['2024-03-31', '2024-06-30', '2024-09-30', '2024-12-31', '2025-06-30'];
    expect(consecutiveQuarters(ends, 3)).toBe(true);
    expect(consecutiveQuarters(ends, 2)).toBe(false);
    expect(consecutiveQuarters(ends, 4)).toBe(false);
    expect(ttm(ends, 3, (i) => i + 1)).toEqual({ value: 10, parts: [1, 2, 3, 4] });
    expect(ttm(ends, 3, (i) => (i === 1 ? null : 1))).toBeNull();
    // 52/53-week quarters of 91 and 98 days are consecutive.
    expect(consecutiveQuarters(['2023-09-30', '2023-12-30', '2024-03-30', '2024-06-29', '2024-09-28'], 4)).toBe(true);
  });
});

describe('fixtures', () => {
  const every = (data: FinancialHistory) =>
    (['annual', 'quarterly'] as const).flatMap((kind) =>
      METRIC_GROUPS.flatMap((group) => group.metrics).flatMap((id) => metricSeries(contextOf(data, kind), id)),
    );

  it('never produces NaN and explains every null', () => {
    for (const name of ['us-full.json', 'tsmc-twd.json', 'bank.json', 'loss-maker.json', 'short-history.json']) {
      for (const item of every(fixture(name))) {
        if (item.value === null) expect(item.reason, `${name} ${item.formula}`).toBeTruthy();
        else expect(Number.isFinite(item.value)).toBe(true);
      }
    }
  });

  it('bank format: no gross / operating metrics, but ROE and tax still work', () => {
    const ctx = contextOf(fixture('bank.json'), 'annual');
    expect(metricSeries(ctx, 'grossMargin').every((m) => m.value === null && m.reason!.includes('金融業'))).toBe(true);
    expect(metricSeries(ctx, 'operatingMargin').every((m) => m.value === null)).toBe(true);
    expect(metricSeries(ctx, 'roic').every((m) => m.value === null)).toBe(true);
    expect(metricSeries(ctx, 'interestCoverage').every((m) => m.value === null)).toBe(true);
    expect(metricSeries(ctx, 'roe').at(-1)!.value).toBeGreaterThan(10);
    expect(metricSeries(ctx, 'effectiveTaxRate').at(-1)!.value).toBeCloseTo(22.1, 1);
    expect(metricSeries(ctx, 'freeCashFlow').every((m) => m.value === null)).toBe(true);
  });

  it('loss-maker: 轉盈, abnormal tax, negative equity and IC ≤ 0', () => {
    const data = fixture('loss-maker.json');
    const ctx = contextOf(data, 'annual');
    const income = metricSeries(ctx, 'netIncome').map((m) => m.value);
    expect(yoy(income, data.annual.end, 4, 'money').label).toBe('轉盈');
    expect(windowCagr(income, data.annual.end, [0, 1, 2, 3, 4]).reason).toBe('期間有虧損，無法計算');
    expect(windowCagr(metricSeries(ctx, 'revenue').map((m) => m.value), data.annual.end, [0, 1, 2, 3, 4]).value).toBeCloseTo(
      (Math.pow(5.1 / 0.8, 365.25 / 1461) - 1) * 100,
      6,
    );
    expect(metricSeries(ctx, 'effectiveTaxRate').at(-1)).toMatchObject({ value: 75, abnormal: true });
    expect(metricSeries(ctx, 'roe')[2]!.value).toBeNull();
    expect(metricSeries(ctx, 'roe')[3]!.value).toBeNull();
    expect(metricSeries(ctx, 'roic')[0]!.reason).toContain('投入資本');
    expect(metricSeries(ctx, 'roic')[4]!.value).toBeNull();
    expect(metricSeries(ctx, 'grossMargin')[0]!.value).toBe(-50);
  });

  it('mixed provenance: SEC years without as-reported operating income fall back and say so', () => {
    const ctx = contextOf(fixture('us-full.json'), 'annual');
    const margins = metricSeries(ctx, 'operatingMargin');
    expect(margins.slice(0, 6).every((m) => m.oiField === 'operatingIncome')).toBe(true);
    expect(margins.slice(6).every((m) => m.oiField === 'operatingIncomeReported')).toBe(true);
    expect(margins[0]!.value).toBeCloseTo(30, 6);
    const roes = metricSeries(ctx, 'roe');
    expect(roes[0]!.basis).toBe('approx');
    expect(roes.slice(1).every((m) => m.basis === 'reported')).toBe(true);
    expect(metricSeries(ctx, 'interestCoverage').at(-1)!.reason).toBe('缺利息費用');
    expect(Object.keys(METRICS)).toHaveLength(19);
  });

  it('TWD file: quarterly TTM ROE and ROIC from the fifth quarter on', () => {
    const ctx = contextOf(fixture('tsmc-twd.json'), 'quarterly');
    const roes = metricSeries(ctx, 'roe');
    expect(roes.slice(0, 3).every((m) => m.value === null)).toBe(true);
    expect(roes[3]!.basis).toBe('approx');
    expect(roes[4]!.basis).toBe('ttm');
    expect(metricSeries(ctx, 'roic')[4]!.value).toBeGreaterThan(20);
  });
});
