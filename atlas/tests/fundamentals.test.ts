import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { FinancialNormalizer } from '../src/fundamentals/FinancialNormalizer';
import { margin, margins, financialGrowth } from '../src/fundamentals/FinancialCalculations';
import { financialSchema } from '../src/fundamentals/financialSchema';
const normalizer = new FinancialNormalizer();
const accession = (n: number) => `0000000001-24-${String(n).padStart(6, '0')}`;
type Options = { fy?: number; fp?: string; filed?: string; form?: string; accn?: string };
function fact(start: string | null, end: string, val: number, options: Options = {}) {
  const month = Number(end.slice(5, 7)),
    fp = month === 12 ? 'FY' : `Q${Math.ceil(month / 3)}`;
  return {
    ...(start ? { start } : {}),
    end,
    val,
    fy: 2024,
    fp,
    form: fp === 'FY' ? '10-K' : '10-Q',
    filed: new Date(Date.parse(end) + 30 * 86400000).toISOString().slice(0, 10),
    accn: accession(month),
    ...options,
  };
}
const tag = (rows: unknown[], unit = 'USD') => ({ units: { [unit]: rows } });
const revenue = 'RevenueFromContractWithCustomerExcludingAssessedTax';
const ocf = 'NetCashProvidedByUsedInOperatingActivities',
  capex = 'PaymentsToAcquirePropertyPlantAndEquipment';
function fixture(extra: Record<string, ReturnType<typeof tag>> = {}) {
  const duration = (values: number[]) =>
    ['2024-03-31', '2024-06-30', '2024-09-30', '2024-12-31'].map((end, i) =>
      fact('2024-01-01', end, values[i]),
    );
  return {
    cik: 1,
    facts: {
      'us-gaap': {
        [revenue]: tag(duration([100, 300, 600, 1000])),
        [ocf]: tag(duration([10, 25, 50, 90])),
        [capex]: tag(duration([2, 7, 10, 20])),
        EarningsPerShareDiluted: tag(duration([1, 3, 5, 8]), 'USD/shares'),
        Assets: tag([fact(null, '2024-06-30', 500), fact(null, '2024-12-31', 1200)]),
        ...extra,
      },
    },
  };
}
describe('SEC period normalization', () => {
  it('derives standalone Q2/Q3/Q4 from aligned cumulative periods with full raw lineage', () => {
    const q = normalizer.normalize(fixture(), 'AAPL', 'quarterly');
    expect(q.map((r) => r.fiscalQuarter)).toEqual([1, 2, 3, 4]);
    expect(q.map((r) => r.revenue)).toEqual([100, 200, 300, 400]);
    expect(q.map((r) => r.operatingCashFlow)).toEqual([10, 15, 25, 40]);
    expect(q.map((r) => r.capex)).toEqual([2, 5, 3, 10]);
    expect(q.map((r) => r.freeCashFlow)).toEqual([8, 10, 22, 30]);
    expect(q[0].sourceConcepts.revenue!.derived).toBe(false);
    for (const r of q.slice(1)) {
      expect(r.sourceConcepts.revenue!.derived).toBe(true);
      expect(r.sourceConcepts.revenue!.inputs).toHaveLength(2);
      expect(r.sourceConcepts.revenue!.calculation).toContain('aligned fiscal start');
    }
    expect(q[1].periodStart).toBe('2024-04-01');
    expect(q[3].periodStart).toBe('2024-10-01');
    expect(q[1].sourceConcepts.freeCashFlow!.calculation).toContain('current cumulative');
  });
  it('uses annual values directly and never subtracts cumulative EPS', () => {
    const raw = fixture(),
      a = normalizer.normalize(raw, 'AAPL', 'annual'),
      q = normalizer.normalize(raw, 'AAPL', 'quarterly');
    expect(a).toHaveLength(1);
    expect(a[0].revenue).toBe(1000);
    expect(a[0].epsDiluted).toBe(8);
    expect(a[0].sourceConcepts.revenue!.derived).toBe(false);
    expect(q.map((r) => r.epsDiluted)).toEqual([1, null, null, null]);
    raw.facts['us-gaap'].EarningsPerShareDiluted.units['USD/shares'].push(
      fact('2024-04-01', '2024-06-30', 2.25),
    );
    expect(normalizer.normalize(raw, 'AAPL', 'quarterly')[1].epsDiluted).toBe(2.25);
  });
  it('prefers explicit standalone quarter over YTD derivation and explicit concept priority over fallback', () => {
    const raw = fixture({ Revenues: tag([fact('2024-04-01', '2024-06-30', 209)]) });
    raw.facts['us-gaap'][revenue].units.USD.push(fact('2024-04-01', '2024-06-30', 205));
    const q = normalizer.normalize(raw, 'AAPL', 'quarterly')[1];
    expect(q.revenue).toBe(205);
    expect(q.sourceConcepts.revenue!.derived).toBe(false);
    expect(q.sourceConcepts.revenue!.inputs[0].concept).toBe(`us-gaap:${revenue}`);
    const fallback = fixture({
      [revenue]: tag([]),
      Revenues: tag([fact('2024-01-01', '2024-12-31', 111)]),
    });
    expect(
      normalizer.normalize(fallback, 'NVDA', 'annual')[0].sourceConcepts.revenue!.inputs[0].concept,
    ).toBe('us-gaap:Revenues');
  });
  it('separates instant/duration and units; missing stays null and true zero survives', () => {
    const raw = fixture({
      CashAndCashEquivalentsAtCarryingValue: tag([fact(null, '2024-06-30', 0)]),
      Liabilities: tag([fact('2024-01-01', '2024-06-30', 123)]),
      GrossProfit: tag([fact('2024-01-01', '2024-06-30', 90)], 'EUR'),
    });
    const q = normalizer.normalize(raw, 'AAPL', 'quarterly')[1];
    expect(q.cash).toBe(0);
    expect(q.totalAssets).toBe(500);
    expect(q.totalLiabilities).toBeNull();
    expect(q.grossProfit).toBeNull();
    expect(q.sourceConcepts.cash!.inputs[0].kind).toBe('instant');
    expect(q.sourceConcepts.revenue!.inputs[0].kind).toBe('duration');
    expect(q.operatingExpenses).toBeNull();
    expect(financialSchema.safeParse(q).success).toBe(true);
  });
  it('will not derive across concepts, fiscal starts, missing prior cumulative or incompatible restatement dates', () => {
    const raw = fixture({
      [revenue]: tag([
        fact('2024-01-01', '2024-06-30', 300),
        fact('2024-01-01', '2024-12-31', 1000),
      ]),
      Revenues: tag([fact('2024-01-01', '2024-03-31', 100)]),
    });
    expect(normalizer.normalize(raw, 'AAPL', 'quarterly')[1].revenue).toBeNull();
    const absent = fixture({ [ocf]: tag([fact('2024-01-01', '2024-06-30', 25)]) });
    expect(normalizer.normalize(absent, 'AAPL', 'quarterly')[1].operatingCashFlow).toBeNull();
    const shifted = fixture({
      [ocf]: tag([fact('2024-01-02', '2024-03-31', 10), fact('2024-01-01', '2024-06-30', 25)]),
    });
    expect(normalizer.normalize(shifted, 'AAPL', 'quarterly')[1].operatingCashFlow).toBeNull();
    const future = fixture({
      [ocf]: tag([
        fact('2024-01-01', '2024-03-31', 11, { filed: '2025-05-01', accn: accession(20) }),
        fact('2024-01-01', '2024-06-30', 25),
      ]),
    });
    expect(normalizer.normalize(future, 'AAPL', 'quarterly')[1].operatingCashFlow).toBeNull();
  });
  it('deduplicates identical facts and picks newest amendments, but conflicting same-accession values remain N/A', () => {
    const duplicate = fact('2024-01-01', '2024-12-31', 1000);
    const raw = fixture({
      [revenue]: tag([
        duplicate,
        { ...duplicate },
        fact('2024-01-01', '2024-12-31', 1050, {
          filed: '2025-03-01',
          accn: accession(30),
          form: '10-K/A',
        }),
      ]),
    });
    let r = normalizer.normalize(raw, 'AAPL', 'annual')[0];
    expect(r.revenue).toBe(1050);
    expect(r.sourceConcepts.revenue!.inputs).toHaveLength(1);
    expect(r.sourceConcepts.revenue!.inputs[0].form).toBe('10-K/A');
    raw.facts['us-gaap'][revenue].units.USD.push(
      fact('2024-01-01', '2024-12-31', 1060, {
        filed: '2025-03-01',
        accn: accession(30),
        form: '10-K/A',
      }),
    );
    r = normalizer.normalize(raw, 'AAPL', 'annual')[0];
    expect(r.revenue).toBeNull();
    expect(r.warnings.join()).toContain('conflicting facts');
  });
  it('assigns comparative annual facts to their own fiscal year, not the filing focus year', () => {
    const raw = fixture({
      [revenue]: tag([
        fact('2024-01-01', '2024-12-31', 1001, {
          fy: 2025,
          filed: '2026-02-01',
          accn: accession(40),
        }),
        fact('2025-01-01', '2025-12-31', 1150, {
          fy: 2025,
          filed: '2026-02-01',
          accn: accession(40),
        }),
      ]),
    });
    const a = normalizer.normalize(raw, 'AAPL', 'annual');
    expect(a.map((r) => [r.fiscalYear, r.revenue])).toEqual([
      [2024, 1001],
      [2025, 1150],
    ]);
    expect(a[0].sourceConcepts.revenue!.inputs[0].fiscalYearFocus).toBe(2025);
  });
  it('normalizes signed CapEx explicitly; FCF requires both inputs; never interprets falling positive YTD as spending', () => {
    const signed = fixture({
      [capex]: tag([
        fact('2024-01-01', '2024-03-31', -2),
        fact('2024-01-01', '2024-06-30', -7),
        fact('2024-01-01', '2024-12-31', -20),
      ]),
    });
    const q = normalizer.normalize(signed, 'AAPL', 'quarterly');
    expect(q[0].capex).toBe(2);
    expect(q[1].capex).toBe(5);
    expect(q[0].sourceConcepts.capex!.derived).toBe(true);
    const missing = fixture({ [capex]: tag([]) });
    expect(normalizer.normalize(missing, 'AAPL', 'annual')[0].freeCashFlow).toBeNull();
    const falling = fixture({
      [capex]: tag([fact('2024-01-01', '2024-03-31', 7), fact('2024-01-01', '2024-06-30', 2)]),
    });
    expect(normalizer.normalize(falling, 'AAPL', 'quarterly')[1].capex).toBeNull();
  });
  it('derives gross profit only with both aligned statement inputs, and produces safe margins and fiscal growth', () => {
    const raw = fixture({
      CostOfRevenue: tag([fact('2024-01-01', '2024-12-31', 600)]),
      NetIncomeLoss: tag([fact('2024-01-01', '2024-12-31', 150)]),
    });
    const a = normalizer.normalize(raw, 'AAPL', 'annual')[0];
    expect(a.grossProfit).toBe(400);
    expect(a.sourceConcepts.grossProfit!.derived).toBe(true);
    expect(margins(a)).toEqual({ gross: 40, operating: null, net: 15 });
    expect(margin(0, 100)).toBe(0);
    expect(margin(1, 0)).toBeNull();
    expect(margin(null, 100)).toBeNull();
    const q = normalizer.normalize(fixture(), 'AAPL', 'quarterly'),
      prior = { ...q[1], fiscalYear: 2023, revenue: 100 };
    expect(financialGrowth([q[3], prior, q[1], q[0]], q[1], 'revenue')).toEqual({
      yoy: 100,
      qoq: 100,
    });
    expect(financialGrowth([q[3], q[1]], q[3], 'revenue').qoq).toBeNull();
    expect(financialGrowth([{ ...a, fiscalYear: 2023, revenue: 500 }, a], a, 'revenue')).toEqual({
      yoy: 100,
      qoq: null,
    });
    expect(
      financialGrowth([{ ...q[3], fiscalYear: 2023, revenue: 50 }, q[0]], q[0], 'revenue').qoq,
    ).toBe(100);
  });
});
describe('official SEC fixture company smoke: differing filing patterns', () => {
  for (const [symbol, fy, q, annualRevenue, start] of [
    ['AAPL', 2026, 3, 416161000000, '2025-09-28'],
    ['MSFT', 2026, 4, 331839000000, '2025-07-01'],
    ['NVDA', 2027, 2, 215938000000, '2026-01-26'],
    ['TSLA', 2026, 2, 94827000000, '2026-01-01'],
  ] as const)
    it(`${symbol}: fiscal year, standalone quarters, annuals, units and provenance`, () => {
      const raw = JSON.parse(readFileSync(`tests/fixtures/sec/${symbol}.json`, 'utf8'));
      const a = normalizer.normalize(raw, symbol, 'annual'),
        quarters = normalizer.normalize(raw, symbol, 'quarterly'),
        latest = quarters.at(-1)!;
      expect(a.at(-1)!.revenue).toBe(annualRevenue);
      expect(latest.fiscalYear).toBe(fy);
      expect(latest.fiscalQuarter).toBe(q);
      expect(quarters.find((r) => r.fiscalYear === fy && r.fiscalQuarter === 1)!.periodStart).toBe(
        start,
      );
      expect(latest.operatingCashFlow).not.toBeNull();
      expect(latest.freeCashFlow).toBe(latest.operatingCashFlow! - latest.capex!);
      expect(latest.sourceConcepts.freeCashFlow!.derived).toBe(true);
      expect(latest.sourceConcepts.operatingCashFlow!.derived).toBe(true);
      expect(a.at(-1)!.sourceConcepts.revenue!.inputs[0].accession).toMatch(/^\d{10}-\d{2}-\d{6}$/);
      for (const r of [...a, ...quarters]) {
        expect(financialSchema.safeParse(r).success).toBe(true);
        for (const provenance of Object.values(r.sourceConcepts))
          if (provenance)
            for (const source of provenance.inputs) {
              expect(source.secUrl).toContain('data.sec.gov');
              expect(source.unit).toMatch(/^USD(\/shares)?$/);
              expect(source.filed >= source.end).toBe(true);
            }
      }
    });
});

it('maps explicit fallback concepts while refusing misleading broad totals', () => {
  const raw = fixture({
    CostOfGoodsAndServicesSold: tag([fact('2024-01-01', '2024-12-31', 600)]),
    ProfitLoss: tag([fact('2024-01-01', '2024-12-31', 100)]),
    EarningsPerShareBasicAndDiluted: tag([fact('2024-01-01', '2024-12-31', 2.5)], 'USD/shares'),
    EarningsPerShareDiluted: tag([], 'USD/shares'),
    NetCashProvidedByUsedInOperatingActivities: tag([]),
    NetCashProvidedByUsedInOperatingActivitiesContinuingOperations: tag([
      fact('2024-01-01', '2024-12-31', 90),
    ]),
    PaymentsToAcquirePropertyPlantAndEquipment: tag([]),
    PaymentsToAcquireProductiveAssets: tag([fact('2024-01-01', '2024-12-31', 20)]),
    StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest: tag([
      fact(null, '2024-12-31', 700),
    ]),
    CostsAndExpenses: tag([fact('2024-01-01', '2024-12-31', 999)]),
    CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents: tag([
      fact(null, '2024-12-31', 500),
    ]),
  });
  const r = normalizer.normalize(raw, 'MSFT', 'annual')[0];
  expect(r.costOfRevenue).toBe(600);
  expect(r.netIncome).toBe(100);
  expect(r.epsBasic).toBe(2.5);
  expect(r.epsDiluted).toBe(2.5);
  expect(r.freeCashFlow).toBe(70);
  expect(r.equity).toBe(700);
  expect(r.operatingExpenses).toBeNull();
  expect(r.cash).toBeNull();
  expect(r.sourceConcepts.capex!.inputs[0].concept).toContain('PaymentsToAcquireProductiveAssets');
});
it('validates complete provenance and avoids non-finite calculated ratios', () => {
  const r = normalizer.normalize(fixture(), 'AAPL', 'annual')[0];
  delete r.sourceConcepts.revenue;
  expect(financialSchema.safeParse(r).success).toBe(false);
  expect(margin(1e308, 1e-308)).toBeNull();
});

it('conflicting raw gross profit is not hidden by a revenue/COGS-derived fallback', () => {
  const raw = fixture({
    CostOfRevenue: tag([fact('2024-01-01', '2024-12-31', 600)]),
    GrossProfit: tag([
      fact('2024-01-01', '2024-12-31', 400),
      fact('2024-01-01', '2024-12-31', 410),
    ]),
  });
  const r = normalizer.normalize(raw, 'AAPL', 'annual')[0];
  expect(r.grossProfit).toBeNull();
  expect(r.sourceConcepts.grossProfit).toBeUndefined();
  expect(r.warnings.join()).toContain('grossProfit: conflicting facts');
});
