import type { FinancialHistory, PeriodKind, RawField } from './FinancialHistory';
import {
  GROSS_NOT_APPLICABLE,
  GROSS_NOT_SEPARATE,
  contextOf,
  costOfRevenue,
  eps,
  freeCashFlow,
  grossProfit,
  netIncome,
  nonOperatingIncome,
  operatingCashFlow,
  operatingIncome,
  raw,
  revenue,
  yoy,
  capex,
  cash,
  type ChangeResult,
  type MetricContext,
  type MetricResult,
  type MetricUnit,
} from './financialMetrics';

/**
 * The three statements shown as tables on the financials page. Every row is either a metric from
 * financialMetrics.ts (same formula and basis labels) or a raw statement line. Rows the data source does
 * not carry at all are listed with `compute: null` and render as「此資料源未提供」; nothing is estimated.
 */

export type StatementId = 'income' | 'balance' | 'cashflow';
export const STATEMENT_TABS: readonly { id: StatementId; label: string }[] = [
  { id: 'income', label: '損益表' },
  { id: 'balance', label: '資產負債' },
  { id: 'cashflow', label: '現金流' },
];

export const NOT_PROVIDED = '此資料源未提供';

interface StatementRowDef {
  id: string;
  label: string;
  unit: MetricUnit;
  /** null: the data source has no such line. */
  compute: ((ctx: MetricContext, index: number) => MetricResult) | null;
}

const rawRow = (id: RawField, label: string, unit: MetricUnit = 'money'): StatementRowDef => ({ id, label, unit, compute: raw(id) });

export const STATEMENT_ROWS: Record<StatementId, StatementRowDef[]> = {
  income: [
    { id: 'revenue', label: '營收', unit: 'money', compute: revenue },
    { id: 'costOfRevenue', label: '營業成本', unit: 'money', compute: costOfRevenue },
    { id: 'grossProfit', label: '毛利', unit: 'money', compute: grossProfit },
    { id: 'operatingIncome', label: '營業利益', unit: 'money', compute: operatingIncome },
    { id: 'nonOperatingIncome', label: '業外損益', unit: 'money', compute: nonOperatingIncome },
    rawRow('pretaxIncome', '稅前淨利'),
    rawRow('taxProvision', '所得稅'),
    { id: 'netIncome', label: '淨利', unit: 'money', compute: netIncome },
    { id: 'eps', label: 'EPS', unit: 'eps', compute: eps },
  ],
  balance: [
    rawRow('totalAssets', '總資產'),
    rawRow('equity', '權益'),
    { id: 'totalLiabilities', label: '總負債', unit: 'money', compute: null },
    { id: 'cash', label: '現金', unit: 'money', compute: cash },
  ],
  cashflow: [
    { id: 'operatingCashFlow', label: '營業現金流', unit: 'money', compute: operatingCashFlow },
    { id: 'capex', label: '資本支出', unit: 'money', compute: capex },
    { id: 'freeCashFlow', label: '自由現金流', unit: 'money', compute: freeCashFlow },
    rawRow('dividendsPaid', '股利'),
    rawRow('buybacks', '庫藏股'),
  ],
};

export interface StatementRow {
  id: string;
  label: string;
  unit: MetricUnit;
  /** One value per period of the file, oldest first (display units are applied by the caller). */
  values: (number | null)[];
  results: MetricResult[];
  /** Year over year per period, same period alignment as the metric cards. */
  changes: ChangeResult[];
  /** True when the file has no value at all for this line (or no such line exists). */
  absent: boolean;
  /** Row-level explanation when the whole line is empty: 「此資料源未提供」 or the structural reason (金融業不計毛利…). */
  note: string | null;
  /** Some period is derived from other lines (e.g. gross profit = revenue − cost of revenue). */
  derived: boolean;
}

const STRUCTURAL_REASONS = [GROSS_NOT_APPLICABLE, GROSS_NOT_SEPARATE];

/** Rows of one statement for every period of the given kind (the table shows all of them). */
export function statementRows(history: FinancialHistory, kind: PeriodKind, statement: StatementId): StatementRow[] {
  const ctx = contextOf(history, kind);
  const ends = ctx.series.end;
  return STATEMENT_ROWS[statement].map((def): StatementRow => {
    const results = ends.map((_, index): MetricResult => {
      if (def.compute) return def.compute(ctx, index);
      return { value: null, basis: 'reported', formula: def.label, inputs: [], notes: [], reason: NOT_PROVIDED };
    });
    const values = results.map((result) => result.value);
    const absent = values.every((value) => value === null);
    // An empty line says why only when the reason is structural; otherwise the file simply lacks the line.
    const structural = results.map((result) => result.reason).find((reason): reason is string => STRUCTURAL_REASONS.includes(reason ?? ''));
    return {
      id: def.id,
      label: def.label,
      unit: def.unit,
      values,
      results,
      changes: values.map((_, index) => yoy(values, ends, index, def.unit)),
      absent: def.compute === null || absent,
      note: def.compute === null ? NOT_PROVIDED : absent ? (structural ?? NOT_PROVIDED) : null,
      derived: results.some((result) => result.basis === 'derived' && result.value !== null),
    };
  });
}

