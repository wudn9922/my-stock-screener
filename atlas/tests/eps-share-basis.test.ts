import { describe, expect, it } from 'vitest';
import { parseFinancials, type FinancialHistory } from '../src/fundamentals/FinancialHistory';
import { epsAdjustments, hasEpsRestatement, restatedEpsLabel, MIXED_Q4_REASON } from '../src/fundamentals/epsBasis';
import { contextOf, eps } from '../src/fundamentals/financialMetrics';
import { statementRows } from '../src/fundamentals/financialStatements';
import { peerFigures } from '../src/fundamentals/peerComparison';
import { buildEpsPeriods, epsAt, dateToSec } from '../src/fundamentals/peRiver';
import { latestDueQuarter, restatePeFigures, restatedAnnualEps, restatedTtmEps } from '../src/fundamentals/restatedPe';
import type { PeFigures } from '../src/fundamentals/peFigures';
import { buildView } from '../src/ui/financials/financialsView';

function ok(data: unknown): FinancialHistory {
  const result = parseFinancials(data);
  if (result.status !== 'ok') throw new Error(result.status);
  return result.data;
}

// 揚秦 2755.TWO as in docs/report/financials (FinMind): reported basic EPS and net income.
const Q_END = ['2023-12-31', '2024-03-31', '2024-06-30', '2024-09-30', '2024-12-31', '2025-03-31', '2025-06-30', '2025-09-30', '2025-12-31', '2026-03-31', '2026-06-30', '2026-09-30'];
const Q_EPS = [1.13, 1.16, 1.47, 1.66, 1.55, 1.67, 1.78, 1.8, 1.77, 1.65, 1.58, 1.42];
const Q_NI = [33886000, 35022000, 44172000, 49991000, 46912000, 50294000, 53747000, 65840000, 65148000, 61309000, 73724000, 66807000];
const DIVIDENDS = [
  { exDate: '2022-08-22', perShare: 1.0 },
  { exDate: '2023-07-04', perShare: 3.7 },
  { exDate: '2025-09-04', perShare: 1.973802 },
  { exDate: '2026-07-16', perShare: 2.464591 },
];
const yangChin = (dividends: unknown = { events: DIVIDENDS }) =>
  ok({
    version: 1,
    symbol: '2755.TWO',
    market: 'TW',
    currency: 'TWD',
    fyEndMonth: 12,
    annual: {
      end: ['2023-12-31', '2024-12-31', '2025-12-31'],
      fy: [2023, 2024, 2025],
      fq: [null, null, null],
      src: ['finmind', 'finmind', 'finmind'],
      values: { epsBasic: [4.4, 5.84, 6.44], netIncome: [132466000, 176097000, 235029000] },
    },
    quarterly: {
      end: Q_END,
      fy: Q_END.map((end) => Number(end.slice(0, 4))),
      fq: [4, 1, 2, 3, 4, 1, 2, 3, 4, 1, 2, 3],
      src: Q_END.map(() => 'finmind'),
      values: { epsBasic: Q_EPS, netIncome: Q_NI },
    },
    split: { adjusted: false, events: [] },
    stockDividends: dividends,
    quality: { level: 'good', issues: ['q4-derived', 'debt-approx'] },
  });

const at = (end: string) => Q_END.indexOf(end);

describe('2755.TWO on today\'s share basis', () => {
  const history = yangChin();
  const view = buildView(history, 'quarterly');

  it('EPS cards and charts show the restated quarters, reported kept for tooltips', () => {
    const values = view.metrics.eps.values;
    expect(values[at('2024-06-30')]).toBeCloseTo(0.985, 3); // 1.47 ÷ 1.4925
    expect(values[at('2025-03-31')]).toBeCloseTo(1.119, 3);
    expect(values[at('2025-06-30')]).toBeCloseTo(1.193, 3);
    expect(values[at('2025-09-30')]).toBeCloseTo(1.444, 3); // 1.80 ÷ 1.2465
    expect(values[at('2025-12-31')]).toBeCloseTo(1.42, 3);
    expect(values[at('2026-03-31')]).toBeCloseTo(1.324, 3);
    expect(values[at('2026-06-30')]).toBe(1.58); // due 08-29, ex 07-16 already in the report
    expect(values[at('2026-09-30')]).toBe(1.42);
    const q = view.metrics.eps.results[at('2024-06-30')]!;
    expect(q.basis).toBe('restated');
    expect(q.reported).toBe(1.47);
    expect(q.notes.join()).toContain('配股 2.464591 元（除權 2026-07-16）');
    expect(view.metrics.eps.title).toBe('還原 EPS（依配股追溯調整）');
    expect(view.metrics.eps.results[at('2026-09-30')]!.basis).not.toBe('restated');
  });

  it('year over year compares restated EPS (Q3: 1.42 vs 1.444, not vs 1.80)', () => {
    expect(view.metrics.eps.changes[at('2026-09-30')]!.value).toBeCloseTo((1.42 / (1.8 / 1.246459) - 1) * 100, 1);
    // peers use the same comparison
    const peer = peerFigures(history, 'quarterly').figures[1]!;
    expect(peer.value).toBeCloseTo(-1.67, 1);
  });

  it('annual EPS is restated with the 90-day annual due date', () => {
    const annual = buildView(history, 'annual').metrics.eps.values;
    expect(annual[2]).toBeCloseTo(6.44 / 1.246459, 3); // FY2025 due 2026-03-31, ex 2026-07-16 after it
    expect(annual[1]).toBeCloseTo(5.84 / (1.197380 * 1.246459), 3);
    expect(restatedAnnualEps(history, 2025)).toEqual({ restated: 5.1666, reported: 6.44, periodEnd: '2025-12-31' });
  });

  it('TTM EPS 5.744 instead of the reported 6.42', () => {
    expect(latestDueQuarter('2026-11-22')).toBe('2026-09-30');
    expect(restatedTtmEps(history, '2026-11-22')).toEqual({ restated: 5.7437, reported: 6.42, periodEnd: '2026-09-30' });
    // the P/E river's current point uses the same basis
    const point = epsAt(dateToSec('2026-11-20'), buildEpsPeriods(history))!;
    expect(point.eps).toBeCloseTo(5.7437, 3);
  });

  it('header P/E is recomputed on the restated EPS, with the reported figures in the note', () => {
    const price = 100;
    const figures: PeFigures = {
      pe: Math.round((price / 6.44) * 100) / 100,
      peTtm: Math.round((price / 6.42) * 100) / 100, // exchange P/E on the reported EPS
      peLoss: false,
      peTtmLoss: false,
      reason: null,
      fiscalYear: 2025,
      source: 'TPEx',
      asOf: '2026-11-20',
    };
    const restated = restatePeFigures(figures, history, price, '2026-11-22');
    expect(restated.peTtm).toBe(17.41); // 100 ÷ 5.7437, was 15.58
    expect(restated.pe).toBe(19.36); // 100 ÷ 5.1666, was 15.53
    expect(restated.epsNote).toContain('近四季 EPS 公告 6.42 → 還原 5.74');
    expect(restated.epsNote).toContain('2025 年 EPS 公告 6.44 → 還原 5.17');
    // the file is behind the newest due quarter → the market file's figures stay
    expect(restatePeFigures(figures, history, price, '2027-05-20').peTtm).toBe(figures.peTtm);
    expect(restatePeFigures(figures, null, price, '2026-11-22')).toBe(figures);
  });

  it('公告／還原 toggle: reported values on screen, same YoY, table rows swap', () => {
    expect(hasEpsRestatement(history)).toBe(true);
    const reported = buildView(history, 'quarterly', undefined, 'reported');
    expect(reported.metrics.eps.values[at('2024-06-30')]).toBe(1.47);
    expect(reported.metrics.eps.title).toBe('EPS');
    expect(reported.metrics.eps.changes.map((change) => change.value)).toEqual(view.metrics.eps.changes.map((change) => change.value));
    const labels = (basis: 'restated' | 'reported') =>
      statementRows(history, 'quarterly', 'income', basis).filter((row) => row.unit === 'eps').map((row) => [row.label, row.values[at('2025-09-30')]]);
    expect(labels('restated')).toEqual([['還原 EPS（依配股追溯調整）', 1.4441], ['公告 EPS', 1.8]]);
    expect(labels('reported')).toEqual([['公告 EPS', 1.8], ['還原 EPS（依配股追溯調整）', 1.4441]]);
  });

  it('without dividend data nothing is restated or guessed', () => {
    const plain = yangChin(null);
    expect(hasEpsRestatement(plain)).toBe(false);
    expect(buildView(plain, 'quarterly').metrics.eps.values[at('2024-06-30')]).toBe(1.47);
    expect(eps(contextOf(plain, 'quarterly'), at('2024-06-30')).comparable).toBeNull();
    expect(statementRows(plain, 'quarterly', 'income').filter((row) => row.unit === 'eps')).toHaveLength(1);
  });
});

describe('splits', () => {
  // 可寧衛 8422.TW: 1 拆 10 (par value change) on 2025-11-06. The FinMind file keeps the pre-split EPS (implied shares
  // ~113M) next to the post-split ones (~1,340M); its Q4 2025 (annual − Q1..Q3) mixes the two bases.
  const kingNet = ok({
    version: 1,
    symbol: '8422.TW',
    market: 'TW',
    annual: {
      end: ['2024-12-31', '2025-12-31'], fy: [2024, 2025], fq: [null, null], src: ['finmind', 'finmind'],
      values: { epsBasic: [10.7, 1.29], netIncome: [1172133000, 1458858000] },
    },
    quarterly: {
      end: ['2025-03-31', '2025-06-30', '2025-09-30', '2025-12-31', '2026-03-31', '2026-06-30', '2026-09-30'],
      fy: [2025, 2025, 2025, 2025, 2026, 2026, 2026], fq: [1, 2, 3, 4, 1, 2, 3], src: Array(7).fill('finmind'),
      values: {
        epsBasic: [3.97, 4.03, 2.48, -9.18, 0.21, 0.34, 0.42],
        netIncome: [444700000, 452900000, 281700000, 279500000, 280900000, 459000000, 568600000],
      },
    },
    split: { adjusted: true, events: [['2025-11-06', 10]] },
    stockDividends: { events: [] },
    quality: { level: 'good', issues: ['split-adjusted', 'q4-derived'] },
  });

  it('a Taiwan par-value split is applied where the implied share count is still pre-split', () => {
    const values = buildView(kingNet, 'quarterly').metrics.eps.values;
    expect(values.slice(0, 3)).toEqual([0.397, 0.403, 0.248]);
    expect(values.slice(4)).toEqual([0.21, 0.34, 0.42]);
    expect(buildView(kingNet, 'annual').metrics.eps.values).toEqual([1.07, 1.29]);
    expect(restatedEpsLabel(kingNet)).toBe('還原 EPS（依分割追溯調整）');
    const q = eps(contextOf(kingNet, 'quarterly'), 2);
    expect(q.reported).toBe(2.48);
    expect(q.notes.join()).toContain('股票分割 1 拆 10（2025-11-06）');
  });

  it('a derived Q4 across the split is not shown (no fabricated basis)', () => {
    const q4 = eps(contextOf(kingNet, 'quarterly'), 3);
    expect(q4.value).toBeNull();
    expect(q4.reason).toBe(MIXED_Q4_REASON);
    expect(eps(contextOf(kingNet, 'quarterly', 'reported'), 3).value).toBe(-9.18);
    // P/E river: pre-split quarters divided by 10, the mixed Q4 unusable
    const periods = buildEpsPeriods(kingNet);
    expect(periods.quarters.map((period) => period.eps)).toEqual([0.397, 0.403, 0.248, null, 0.21, 0.34, 0.42]);
  });

  it('SEC EPS left on the pre-split basis is restated; an adjusted file is untouched', () => {
    const file = (eps: number[], adjusted: boolean) =>
      ok({
        version: 1, symbol: 'ABCD', market: 'US',
        annual: { end: [], fy: [], fq: [], src: [], values: {} },
        quarterly: {
          end: ['2024-03-31', '2024-06-30', '2024-09-30', '2024-12-31'], fy: [2024, 2024, 2024, 2024], fq: [1, 2, 3, 4], src: ['sec', 'sec', 'sec', 'sec'],
          values: { epsDiluted: eps, netIncome: [200, 210, 230, 240] },
        },
        split: { adjusted, events: [['2024-08-15', 2]] },
        quality: { level: 'good', issues: [] },
      });
    // 100 shares before the 2-for-1 split, 200 after: Q1/Q2 still printed on 100 shares
    const mixed = file([2.0, 2.1, 1.15, 1.2], false);
    expect(buildView(mixed, 'quarterly').metrics.eps.values).toEqual([1, 1.05, 1.15, 1.2]);
    expect(epsAdjustments(mixed, 'quarterly').map((item) => item?.factor)).toEqual([2, 2, 1, 1]);
    expect(restatedEpsLabel(mixed)).toBe('還原 EPS（依分割追溯調整）');
    const adjusted = file([1.0, 1.05, 1.15, 1.2], true);
    expect(hasEpsRestatement(adjusted)).toBe(false);
    expect(buildView(adjusted, 'quarterly').metrics.eps.values).toEqual([1, 1.05, 1.15, 1.2]);
  });

  it('a Taiwan split dated on a 配股 ex-rights date is that dividend, not a second event', () => {
    const doubled = ok({
      version: 1, symbol: '1234.TW', market: 'TW',
      annual: { end: [], fy: [], fq: [], src: [], values: {} },
      quarterly: {
        end: ['2025-03-31', '2025-06-30', '2025-09-30', '2025-12-31'], fy: [2025, 2025, 2025, 2025], fq: [1, 2, 3, 4], src: Array(4).fill('finmind'),
        values: { epsBasic: [3, 2, 2, 2], netIncome: [300, 300, 300, 300] },
      },
      split: { adjusted: false, events: [['2025-08-01', 1.5]] },
      stockDividends: { events: [{ exDate: '2025-08-01', perShare: 5 }] },
      quality: { level: 'good', issues: [] },
    });
    expect(epsAdjustments(doubled, 'quarterly').map((item) => item?.factor)).toEqual([1.5, 1, 1, 1]);
  });
});
