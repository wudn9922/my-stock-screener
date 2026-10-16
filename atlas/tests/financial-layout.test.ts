import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { parseFinancials, type FinancialHistory } from '../src/fundamentals/FinancialHistory';
import { METRIC_GROUPS, METRICS } from '../src/fundamentals/financialMetrics';
import {
  buildView,
  cardSummaryLine,
  keyMetricsSummary,
  quarterStrip,
  summaryChipsLine,
} from '../src/ui/financials/financialsView';
import { StatementTable } from '../src/ui/financials/StatementTable';

type Row = { end: string; [key: string]: string | number | null | undefined };

/** Builds a history through the real parser (synthetic numbers; quarter ends given explicitly). */
function history(annual: Row[], quarterly: Row[] = []): FinancialHistory {
  const block = (rows: Row[]) => {
    const values: Record<string, (number | null)[]> = {};
    for (const row of rows)
      for (const key of Object.keys(row)) if (key !== 'end') values[key] = rows.map((r) => (r[key] ?? null) as number | null);
    return { end: rows.map((row) => row.end), values };
  };
  const result = parseFinancials({
    version: 1,
    symbol: 'SYN',
    market: 'US',
    currency: 'USD',
    annual: block(annual),
    quarterly: block(quarterly),
  });
  if (result.status !== 'ok') throw new Error(result.message);
  return result.data;
}

/** Quarterly rows for 2021–2022 with revenue, net income, EPS and gross profit; `skip` leaves quarters out. */
function quarters(skip: string[] = [], eps: (end: string) => number | null = () => 1.5): Row[] {
  const ends = ['2021-03-31', '2021-06-30', '2021-09-30', '2021-12-31', '2022-03-31', '2022-06-30', '2022-09-30', '2022-12-31'];
  // Values follow the calendar position (not the row), so a skipped quarter leaves the others unchanged.
  return ends
    .map((end, i) => ({ end, i }))
    .filter(({ end }) => !skip.includes(end))
    .map(({ end, i }) => ({
      end,
      revenue: 1000 + i * 100,
      netIncome: 100 + i * 10,
      grossProfit: 400 + i * 40,
      epsDiluted: eps(end),
    }));
}

describe('card order on the financials page', () => {
  it('growth leads with EPS, then revenue, then net income', () => {
    expect(METRIC_GROUPS.find((group) => group.id === 'growth')!.metrics).toEqual(['eps', 'revenue', 'netIncome']);
  });

  it('profitability lists the ratios before the amounts', () => {
    const metrics = METRIC_GROUPS.find((group) => group.id === 'profitability')!.metrics;
    expect(metrics.slice(0, 3)).toEqual(['grossMargin', 'operatingMargin', 'netMargin']);
    for (const id of metrics.slice(3)) expect(METRICS[id].unit).not.toBe('pct');
    expect(metrics.slice(0, 3).every((id) => METRICS[id].unit === 'pct')).toBe(true);
  });
});

describe('当季财报 strip', () => {
  it('shows the newest quarter with its EPS, revenue, margins and same-quarter changes, in that order', () => {
    const data = history([], quarters());
    const strip = quarterStrip(buildView(data, 'quarterly'), data.currency);
    expect(strip.cells.map((cell) => cell.id)).toEqual(['eps', 'revenue', 'grossMargin', 'netMargin']);
    expect(strip.period).toBe(buildView(data, 'quarterly').labels[7]!.long);
    const revenue = strip.cells.find((cell) => cell.id === 'revenue')!;
    // Q4 2022 (1700) against Q4 2021 (1300): +30.8%, the same fiscal quarter a year earlier.
    expect(revenue.change.text).toBe('年增 +30.8%');
    expect(revenue.change.tone).toBe('up');
    expect(strip.cells.find((cell) => cell.id === 'eps')!.value).toBe('1.50');
  });

  it('a quarter without EPS keeps a dash and its reason; no expectation or estimate fills the gap', () => {
    const data = history([], quarters([], (end) => (end === '2022-12-31' ? null : 1.5)));
    const strip = quarterStrip(buildView(data, 'quarterly'), data.currency);
    const eps = strip.cells.find((cell) => cell.id === 'eps')!;
    expect(strip.period).toBe(buildView(data, 'quarterly').labels[7]!.long);
    expect(eps.value).toBe('—');
    expect(eps.unit).toBe('');
    expect(eps.reason).toEqual(expect.any(String));
    expect(eps.change.text).toBe('年增 —');
  });

  it('without quarterly data the strip is empty and says so', () => {
    const data = history([{ end: '2023-12-31', revenue: 100, netIncome: 10 }]);
    const strip = quarterStrip(buildView(data, 'quarterly'), data.currency);
    expect(strip.index).toBeNull();
    expect(strip.period).toBeNull();
    expect(strip.cells.every((cell) => cell.value === '—')).toBe(true);
    expect(strip.cells[0]!.reason).toBe('這檔股票沒有季度資料');
  });
});

describe('same-quarter YoY when a quarter is missing', () => {
  it('pairs each quarter with the same fiscal quarter a year earlier, never with the array neighbour', () => {
    // 2021 Q2 is missing: 2022 Q2 has no year-earlier base, 2022 Q3 must still compare with 2021 Q3 (index 2 → 3 in the array)
    const data = history([], quarters(['2021-06-30']));
    const view = buildView(data, 'quarterly');
    const revenue = view.metrics.revenue.changes;
    const ends = data.quarterly.end;
    const index = (end: string) => ends.indexOf(end);
    expect(revenue[index('2022-06-30')]).toMatchObject({ value: null, baseIndex: null });
    expect(revenue[index('2022-09-30')]!.baseIndex).toBe(index('2021-09-30'));
    expect(revenue[index('2022-09-30')]!.value).toBeCloseTo(((1600 - 1200) / 1200) * 100, 6);
    expect(revenue[index('2022-12-31')]!.baseIndex).toBe(index('2021-12-31'));
    // The quarter after a gap is paired with the quarter a year before it, not with the previous row.
    expect(revenue[index('2022-03-31')]!.baseIndex).toBe(index('2021-03-31'));
    expect(revenue[index('2022-03-31')]!.baseIndex).not.toBe(index('2021-12-31'));
  });

  it('the EPS series keeps its YoY continuous once a Q4 EPS is filled (NFLX-like: the Q4 2021 base is 0.50)', () => {
    const data = history([], quarters([], (end) => (end === '2021-12-31' ? 0.5 : 1.5)));
    const eps = buildView(data, 'quarterly').metrics.eps.changes;
    const ends = data.quarterly.end;
    for (const end of ['2022-03-31', '2022-06-30', '2022-09-30', '2022-12-31']) {
      expect(eps[ends.indexOf(end)]!.value).not.toBeNull();
    }
    expect(eps[ends.indexOf('2022-12-31')]!.value).toBeCloseTo(((1.5 - 0.5) / 0.5) * 100, 6);
    expect(eps[ends.indexOf('2022-12-31')]!.baseIndex).toBe(ends.indexOf('2021-12-31'));
  });
});

describe('folded section summaries', () => {
  const data = history(
    [
      { end: '2020-12-31', revenue: 100, netIncome: 10, epsDiluted: 1, equity: 200, cash: 20 },
      { end: '2021-12-31', revenue: 120, netIncome: 12, epsDiluted: 1.2, equity: 210, cash: 25 },
      { end: '2022-12-31', revenue: 144, netIncome: 15, epsDiluted: 1.5, equity: 220, cash: 30 },
    ],
    quarters(),
  );
  it('key metrics line: the newest period with revenue, net income and EPS', () => {
    const line = keyMetricsSummary(buildView(data, 'quarterly'), 'USD');
    expect(line).toMatch(/^FY2022 Q4：EPS 1\.50美元、營收 .+$/);
  });

  it('summary chips line uses the annual window', () => {
    expect(summaryChipsLine(buildView(data, 'annual'))).toMatch(/^營收 CAGR \+\d+\.\d%/);
  });

  it('a card line names the first two metrics that have a value, or says there is none', () => {
    const view = buildView(data, 'annual');
    expect(cardSummaryLine(view, ['eps', 'revenue', 'netIncome'], 'USD')).toMatch(/^EPS 1\.50美元、營收 .+（FY2022）$/);
    const empty = buildView(history([{ end: '2022-12-31', revenue: 1 }]), 'annual');
    expect(cardSummaryLine(empty, ['eps', 'netIncome'], 'USD')).toBe('沒有可用的數字');
  });
});

describe('statement table cells', () => {
  it('each cell carries its value and the year-over-year change under it; there is no per-row switch', () => {
    const data = history(
      [
        { end: '2021-12-31', revenue: 100, netIncome: 10 },
        { end: '2022-12-31', revenue: 120, netIncome: 12 },
      ],
      [],
    );
    const html = renderToStaticMarkup(createElement(StatementTable, { history: data, view: buildView(data, 'annual') }));
    expect(html).toContain('class="fin-cell-value"');
    expect(html).toContain('class="fin-cell-yoy up"');
    expect(html).toContain('+20.0%');
    expect(html).not.toContain('fin-row-toggle');
    expect(html).toContain('顯示年增率');
  });
});
