import { describe, expect, it } from 'vitest';
import { parseFinancials, type FinancialHistory, type PeriodKind } from '../src/fundamentals/FinancialHistory';
import { GROSS_NOT_APPLICABLE, GROSS_NOT_SEPARATE, grossProfit, contextOf, isFinancialSector, metricSeries } from '../src/fundamentals/financialMetrics';
import { STATEMENT_ROWS, statementRows } from '../src/fundamentals/financialStatements';
import {
  CHART_PERIODS,
  KEY_METRIC_IDS,
  SPAN_OPTIONS,
  buildView,
  changeCellText,
  keyChangeText,
  keySummary,
  spanOptions,
  windowIndices,
} from '../src/ui/financials/financialsView';
import { linePath, overlayScale } from '../src/ui/financials/barGeometry';

type Row = { end: string; [key: string]: string | number | null | undefined };

/** Builds a history through the real parser. Quarter ends are given explicitly so the YoY alignment is visible. */
function history(annual: Row[], quarterly: Row[] = [], extra: Record<string, unknown> = {}): FinancialHistory {
  const block = (rows: Row[]) => {
    const values: Record<string, (number | null)[]> = {};
    for (const row of rows)
      for (const key of Object.keys(row)) if (key !== 'end') values[key] = rows.map((r) => (r[key] ?? null) as number | null);
    return { end: rows.map((row) => row.end), values };
  };
  const result = parseFinancials({ version: 1, symbol: 'TEST', market: 'US', currency: 'USD', annual: block(annual), quarterly: block(quarterly), ...extra });
  if (result.status !== 'ok') throw new Error(result.message);
  return result.data;
}

/** n annual periods, one per year from 2010, with revenue rising 10% a year. */
const annualRows = (n: number, extra: Partial<Record<string, (i: number) => number | null>> = {}): Row[] =>
  Array.from({ length: n }, (_, i) => {
    const row: Row = { end: `${2010 + i}-12-31`, revenue: 100 * 1.1 ** i, netIncome: 10 + i, costOfRevenue: 60 + i, operatingIncome: 20 + i, pretaxIncome: 25 + i, taxProvision: 5, equity: 200 + i * 5, cash: 50, totalAssets: 400 + i };
    for (const [key, fn] of Object.entries(extra)) row[key] = fn!(i);
    return row;
  });

/** Quarter ends: Q1..Q4 of each year (Mar/Jun/Sep/Dec), fiscal = calendar year. */
const quarterRows = (years: number, revenue: (i: number) => number | null = (i) => 100 + i): Row[] => {
  const ends = ['03-31', '06-30', '09-30', '12-31'];
  return Array.from({ length: years * 4 }, (_, i) => ({
    end: `${2020 + Math.floor(i / 4)}-${ends[i % 4]}`,
    revenue: revenue(i),
    netIncome: 10 + i,
  }));
};

const fixtureText = {
  bank: {
    version: 1,
    symbol: 'BANK',
    market: 'US',
    currency: 'USD',
    annual: { end: ['2023-12-31', '2024-12-31'], values: { revenue: [500, 520], netIncome: [100, 110] } },
    quarterly: { end: [], values: {} },
    quality: { issues: ['financial-sector-format'] },
  },
};

describe('YoY alignment and sign rules', () => {
  it('annual YoY compares each year with the year before, and the first year has none', () => {
    const view = buildView(history(annualRows(4)), 'annual');
    const changes = view.metrics.revenue.changes;
    expect(changes[0]).toMatchObject({ value: null, reason: '沒有一年前的同期資料' });
    expect(changes[1]!.value).toBeCloseTo(10, 6);
    expect(changes[3]!.value).toBeCloseTo(10, 6);
  });

  it('quarterly YoY compares a quarter with the same quarter a year earlier, never the previous quarter', () => {
    // Revenue 100,101,… by quarter index: Q1 2021 (index 4) vs Q1 2020 (index 0) = +4%.
    const view = buildView(history([], quarterRows(3)), 'quarterly');
    const changes = view.metrics.revenue.changes;
    expect(changes[4]!.value).toBeCloseTo(((104 - 100) / 100) * 100, 6);
    expect(changes[4]!.baseIndex).toBe(0);
    expect(changes[5]!.baseIndex).toBe(1);
    expect(changes[3]).toMatchObject({ baseIndex: null });
  });

  it('a base of zero or below gives no growth point: 轉盈 or 續虧; a base above zero that turns negative is 轉虧 with a value', () => {
    const view = buildView(history(annualRows(4, { netIncome: (i) => [-5, -2, 8, -3][i]! })), 'annual');
    const net = view.metrics.netIncome.changes;
    expect(net[1]).toMatchObject({ value: null, label: '續虧' });
    expect(net[2]).toMatchObject({ value: null, label: '轉盈' });
    expect(keyChangeText(net[2]!)).toEqual({ text: '轉盈', tone: 'up' });
    expect(keyChangeText(net[1]!)).toEqual({ text: '續虧', tone: 'down' });
    // 8 → -3 is a real growth figure (−137.5%), labelled 轉虧 and still drawn.
    expect(net[3]!.value).toBeCloseTo(((-3 - 8) / 8) * 100, 6);
    expect(net[3]!.label).toBe('轉虧');
    expect(keyChangeText(net[3]!).text).toBe('轉虧 -137.5%');
  });

  it('a missing value on either side leaves the point out of the overlay line', () => {
    const rows = annualRows(4, { revenue: (i) => (i === 2 ? null : 100 + i) });
    const view = buildView(history(rows), 'annual');
    expect(view.metrics.revenue.changes[2]!.value).toBeNull();
    expect(view.metrics.revenue.changes[3]!.value).toBeNull();
    expect(view.metrics.revenue.changes[1]!.value).not.toBeNull();
  });
});

describe('chart range', () => {
  it('defaults to 10 years and five years of quarters', () => {
    expect(CHART_PERIODS).toEqual({ annual: 10, quarterly: 20 });
    const view = buildView(history(annualRows(19)), 'annual');
    expect(view.metrics.revenue.window).toHaveLength(10);
  });

  it('crops a 19-year annual history to the 5, 10 or 15 year window, keeping the full series in values', () => {
    const view = buildView(history(annualRows(19)), 'annual', 15);
    expect(view.metrics.revenue.window).toHaveLength(15);
    expect(view.metrics.revenue.window[0]).toBe(4);
    expect(view.metrics.revenue.window.at(-1)).toBe(18);
    expect(view.metrics.revenue.values).toHaveLength(19);
    expect(buildView(history(annualRows(19)), 'annual', 5).metrics.revenue.window).toEqual([14, 15, 16, 17, 18]);
  });

  it('quarterly windows crop 60 quarters to 40 and keep the same-quarter CAGR start', () => {
    const view = buildView(history([], quarterRows(15)), 'quarterly', 40);
    expect(view.metrics.revenue.window).toHaveLength(40);
    expect(view.metrics.revenue.window[0]).toBe(20);
    // Quarterly CAGR starts on a Q4 (the same fiscal quarter as the last period) inside the window.
    expect(view.metrics.revenue.cagr?.from).toBe('2025-12-31');
    expect(view.metrics.revenue.cagr?.to).toBe('2034-12-31');
  });

  it('a span longer than the file shows every period', () => {
    expect(windowIndices(9, 10)).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8]);
    expect(windowIndices(0, 10)).toEqual([]);
  });

  it('options are enabled only when the file has at least that many periods', () => {
    expect(spanOptions('annual', 19)).toEqual([
      { span: 5, enabled: true },
      { span: 10, enabled: true },
      { span: 15, enabled: true },
    ]);
    expect(spanOptions('annual', 4).map((o) => o.enabled)).toEqual([false, false, false]);
    expect(spanOptions('quarterly', 15).map((o) => o.enabled)).toEqual([true, false, false, false]);
    expect(SPAN_OPTIONS.quarterly).toEqual([12, 20, 40, 60]);
  });
});

describe('key metrics', () => {
  it('shows seven tiles at one shared period: revenue, gross profit, net income, EPS and the three margins', () => {
    const view = buildView(history(annualRows(5)), 'annual');
    const summary = keySummary(view);
    expect(summary.tiles.map((tile) => tile.id)).toEqual(KEY_METRIC_IDS);
    expect(KEY_METRIC_IDS).toHaveLength(7);
    expect(new Set(summary.tiles.map((tile) => tile.id)).size).toBe(7);
    expect(summary.index).toBe(4);
    for (const tile of summary.tiles) expect(tile.spark.at(-1)?.index).toBe(4);
  });

  it('uses growth for money and EPS, and percentage points for margins', () => {
    const summary = keySummary(buildView(history(annualRows(3)), 'annual'));
    const revenue = summary.tiles.find((tile) => tile.id === 'revenue')!;
    const margin = summary.tiles.find((tile) => tile.id === 'netMargin')!;
    expect(keyChangeText(revenue.change)).toEqual({ text: '年增 +10.0%', tone: 'up' });
    expect(margin.change?.mode).toBe('diff');
    expect(keyChangeText(margin.change).text).toMatch(/^年增 [+-]?\d+\.\d 百分點$/);
  });

  it('a missing value at the latest period shows the reason, never NaN', () => {
    const rows = annualRows(3, { grossProfit: () => null, costOfRevenue: () => null });
    const summary = keySummary(buildView(history(rows), 'annual'));
    const gross = summary.tiles.find((tile) => tile.id === 'grossProfit')!;
    expect(gross.value).toBeNull();
    expect(gross.reason).toBe(GROSS_NOT_SEPARATE);
    for (const tile of summary.tiles) expect(String(tile.value)).not.toBe('NaN');
  });

  it('the newest period with any data sets the shared index even when one metric lags', () => {
    const rows = annualRows(4, { netIncome: (i) => (i === 3 ? null : 10 + i) });
    const summary = keySummary(buildView(history(rows), 'annual'));
    expect(summary.index).toBe(3);
    expect(summary.tiles.find((tile) => tile.id === 'netIncome')!.reason).toMatch(/^缺淨利/);
  });

  it('keeps eight mini-bar periods with gaps as null, oldest first', () => {
    const rows = annualRows(12, { netIncome: (i) => (i === 9 ? null : 10 + i) });
    const tile = keySummary(buildView(history(rows), 'annual')).tiles.find((item) => item.id === 'netIncome')!;
    expect(tile.spark.map((point) => point.index)).toEqual([4, 5, 6, 7, 8, 9, 10, 11]);
    expect(tile.spark[5]!.value).toBeNull();
  });

  it('a file whose periods carry no values gives an empty summary, not a crash', () => {
    const summary = keySummary(buildView(history([{ end: '2023-12-31' }]), 'annual'));
    expect(summary.index).toBeNull();
    expect(summary.tiles.every((tile) => tile.value === null)).toBe(true);
  });
});

describe('gross profit', () => {
  it('uses the reported line first', () => {
    const data = history(annualRows(2, { grossProfit: () => 42 }));
    expect(grossProfit(contextOf(data, 'annual'), 1)).toMatchObject({ value: 42, basis: 'reported' });
  });

  it('derives revenue minus cost of revenue and marks it as derived', () => {
    const data = history(annualRows(2));
    const result = grossProfit(contextOf(data, 'annual'), 1);
    expect(result.value).toBeCloseTo(110 - 61, 6);
    expect(result.basis).toBe('derived');
    expect(result.formula).toBe('營收 − 營業成本');
  });

  it('does not adopt a derived gross profit below zero, like the cost fallback', () => {
    const data = history(annualRows(2, { revenue: () => 50, costOfRevenue: () => 80 }));
    const result = grossProfit(contextOf(data, 'annual'), 0);
    expect(result).toMatchObject({ value: null, reason: '推算毛利小於 0，不採用' });
  });

  it('explains a missing line for a non-financial company', () => {
    const data = history(annualRows(2, { costOfRevenue: () => null }));
    expect(grossProfit(contextOf(data, 'annual'), 0)).toMatchObject({ value: null, reason: GROSS_NOT_SEPARATE });
  });

  it('marks banks as not applicable by the builder flag; a file without the flag is never called a bank', () => {
    const bank = parseFinancials(fixtureText.bank);
    if (bank.status !== 'ok') throw new Error('bank fixture');
    expect(isFinancialSector(bank.data)).toBe(true);
    expect(grossProfit(contextOf(bank.data, 'annual'), 0)).toMatchObject({ value: null, reason: GROSS_NOT_APPLICABLE });
    // An ordinary company whose cost line is merely missing says so, rather than being called a bank.
    const missing = history(annualRows(2, { costOfRevenue: () => null }));
    expect(isFinancialSector(missing)).toBe(false);
    expect(grossProfit(contextOf(missing, 'annual'), 0)).toMatchObject({ reason: GROSS_NOT_SEPARATE });
  });

  it('is part of the profitability metrics, with the margin unchanged', () => {
    const view = buildView(history(annualRows(3)), 'annual');
    expect(view.metrics.grossProfit.values[2]).toBeCloseTo(121 - 62, 6);
    expect(metricSeries(contextOf(history(annualRows(3)), 'annual'), 'grossMargin')[2]!.value).toBeCloseTo((59 / 121) * 100, 6);
  });
});

describe('statement tables', () => {
  it('lists the three statements with the requested lines', () => {
    expect(STATEMENT_ROWS.income.map((row) => row.label)).toEqual(['營收', '營業成本', '毛利', '營業利益', '業外損益', '稅前淨利', '所得稅', '淨利', 'EPS']);
    expect(STATEMENT_ROWS.balance.map((row) => row.label)).toEqual(['總資產', '權益', '總負債', '現金']);
    expect(STATEMENT_ROWS.cashflow.map((row) => row.label)).toEqual(['營業現金流', '資本支出', '自由現金流', '股利', '庫藏股']);
  });

  it('total liabilities is not in the source, so the whole line says so', () => {
    const rows = statementRows(history(annualRows(3)), 'annual', 'balance');
    const liabilities = rows.find((row) => row.id === 'totalLiabilities')!;
    expect(liabilities.absent).toBe(true);
    expect(liabilities.note).toBe('此資料源未提供');
    expect(liabilities.values).toEqual([null, null, null]);
    expect(liabilities.results[0]!.reason).toBe('此資料源未提供');
  });

  it('a line with no value anywhere is absent; a structural reason is kept as the note', () => {
    const rows = statementRows(history(annualRows(3, { dividendsPaid: () => null })), 'annual', 'cashflow');
    const dividends = rows.find((row) => row.id === 'dividendsPaid')!;
    expect(dividends.absent).toBe(true);
    expect(dividends.note).toBe('此資料源未提供');
    const gross = statementRows(history(annualRows(2, { costOfRevenue: () => null })), 'annual', 'income').find((row) => row.id === 'grossProfit')!;
    expect(gross.absent).toBe(true);
    expect(gross.note).toBe(GROSS_NOT_SEPARATE);
  });

  it('marks a derived gross profit row and keeps its changes aligned to the same period', () => {
    const rows = statementRows(history(annualRows(3)), 'annual', 'income');
    const gross = rows.find((row) => row.id === 'grossProfit')!;
    expect(gross.derived).toBe(true);
    expect(gross.changes[2]!.baseIndex).toBe(1);
    expect(gross.changes[2]!.value).toBeCloseTo(((121 - 62 - (110 - 61)) / (110 - 61)) * 100, 6);
  });

  it('YoY cells read 轉盈 / 續虧 / a percentage / the dash', () => {
    expect(changeCellText(null)).toEqual({ text: '—', tone: 'flat' });
    expect(changeCellText({ value: null, mode: 'growth', label: '轉盈', baseIndex: 0 })).toEqual({ text: '轉盈', tone: 'up' });
    expect(changeCellText({ value: 12.34, mode: 'growth', label: null, baseIndex: 0 })).toEqual({ text: '+12.3%', tone: 'up' });
    expect(changeCellText({ value: -4, mode: 'growth', label: null, baseIndex: 0 })).toEqual({ text: '-4.0%', tone: 'down' });
  });

  it('quarterly statements use the quarterly series', () => {
    const rows = statementRows(history([], quarterRows(2)), 'quarterly' as PeriodKind, 'income');
    expect(rows.find((row) => row.id === 'revenue')!.values).toHaveLength(8);
  });
});

describe('YoY overlay geometry', () => {
  it('scales the % axis around zero and drops ticks that would overlap zero', () => {
    const scale = overlayScale([20, -5, null], 0, 120)!;
    expect(scale.hi).toBe(20);
    expect(scale.lo).toBe(-5);
    expect(scale.yOf(20)).toBe(0);
    expect(scale.yOf(-5)).toBe(120);
    expect(scale.ticks).toEqual([20, 0, -5]);
    // A tiny maximum next to zero is not labelled separately.
    expect(overlayScale([0.5, 100], 0, 120)!.ticks).toEqual([100, 0]);
  });

  it('returns nothing when every point is missing', () => {
    expect(overlayScale([null, null], 0, 120)).toBeNull();
  });

  it('breaks the line at missing points instead of bridging them', () => {
    expect(linePath([{ x: 1, y: 2 }, null, { x: 3, y: 4 }, { x: 5, y: 6 }])).toBe('M1,2M3,4L5,6');
    expect(linePath([])).toBe('');
  });
});
