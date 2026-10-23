import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { parseFinancials, type FinancialHistory } from '../src/fundamentals/FinancialHistory';
import { contextOf, eps, netMargin, periodLabels } from '../src/fundamentals/financialMetrics';
import { issueText } from '../src/ui/financials/financialsView';

function parsed(raw: unknown): FinancialHistory {
  const result = parseFinancials(raw);
  if (result.status !== 'ok') throw new Error(result.message);
  return result.data;
}
const tracked = (symbol: string) => parsed(JSON.parse(readFileSync(
  resolve(import.meta.dirname, `../../docs/report/financials/${symbol}.json`), 'utf8',
)));

describe('applied SEC comparatives', () => {
  it('keeps SEC ends aligned when invalid rows are dropped and periods sorted', () => {
    const history = parsed({ symbol: 'NVDA', fyEndMonth: 1, annual: {
      end: ['2026-01-31', 'bad', '2025-01-31', '2024-01-31'],
      secEnd: ['2026-01-25', '2020-01-01', '2025-01-26', '2024-02-30'],
      fy: [2026, 2000, 2025, 2024], values: { revenue: [215938, 0, 130497, 60922] },
    } });
    expect(history.annual.end).toEqual(['2024-01-31', '2025-01-31', '2026-01-31']);
    expect(history.annual.secEnd).toEqual([null, '2025-01-26', '2026-01-25']);
    expect(history.annual.values.revenue).toEqual([60922, 130497, 215938]);
    expect(periodLabels(history, 'annual').at(-1)?.long).toBe('FY2026');
  });

  it('exposes NVDA FY2026 actual SEC date while retaining its normalized alignment', () => {
    const history = tracked('NVDA');
    const index = history.annual.fy.indexOf(2026);
    expect(index).toBeGreaterThanOrEqual(0);
    expect(history.annual.end[index]).toBe('2026-01-31');
    expect(history.annual.secEnd?.[index]).toBe('2026-01-25');
  });

  it('recomputes AD profitability from the restated revenue and retained net income', () => {
    const history = tracked('AD');
    const index = history.annual.end.indexOf('2024-12-31');
    expect(history.annual.values.revenue[index]).toBe(102_933_000);
    expect(history.annual.values.netIncome[index]).toBe(-39_403_000);
    expect(netMargin(contextOf(history, 'annual'), index).value).toBeCloseTo(-38.2802405448, 8);
    expect(history.quality.issues).toContain('restated');
    expect(history.quality.issues).toContain('quarterly-as-reported');
  });

  it('keeps the two reviewed same-basis KVHI Q4 EPS values usable', () => {
    const history = tracked('KVHI');
    const index = history.quarterly.end.indexOf('2013-12-31');
    expect(history.quarterly.values.epsBasic[index]).toBe(0.36);
    expect(history.quarterly.values.epsDiluted[index]).toBe(0.36);
    expect(eps(contextOf(history, 'quarterly'), index).value).toBe(0.36);
  });

  it('explains the retained quarterly and mixed reporting bases', () => {
    expect(issueText('mixed-basis')).toContain('保留既有數值');
    expect(issueText('quarterly-as-reported')).toContain('Q4 EPS 保留原申報值');
  });
});
