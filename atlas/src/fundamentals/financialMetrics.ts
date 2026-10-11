import { growth, margin } from './FinancialCalculations';
import type { FinancialHistory, FinancialSeries, PeriodKind, RawField } from './FinancialHistory';

/**
 * Derived statement metrics for the financials page. This is the only place the formulas live
 * (design B-3): every value carries its basis, the formula text and the inputs it was computed from,
 * so the UI can show exactly what was substituted. Percentages are in percent (12.3 = 12.3%), money in
 * the statement currency, multiples in times.
 */

export type MetricUnit = 'money' | 'eps' | 'pct' | 'times';
/**
 * reported: straight from the statements · derived: rebuilt from other lines (gross profit = revenue −
 * cost) · basic: basic EPS because diluted is missing · approx: an average had only one end point ·
 * statutory-tax: NOPAT used the statutory rate · ttm: sum of the latest four consecutive quarters.
 */
export type Basis = 'reported' | 'derived' | 'basic' | 'approx' | 'statutory-tax' | 'ttm';

export interface MetricInput {
  label: string;
  value: number | null;
  unit: MetricUnit;
}
export interface MetricResult {
  value: number | null;
  basis: Basis;
  /** Formula in words, e.g. 「毛利 ÷ 營收」. */
  formula: string;
  inputs: MetricInput[];
  /** Caveats in plain Chinese (approximation, statutory tax, abnormal rate…). */
  notes: string[];
  /** Why the value is null, when it is. */
  reason?: string;
  /** Effective tax rate below 0% or above 60%: shown as is but flagged. */
  abnormal?: boolean;
  /** Which operating income line an OI-based metric used. */
  oiField?: 'operatingIncomeReported' | 'operatingIncome';
}

export interface MetricContext {
  kind: PeriodKind;
  series: FinancialSeries;
  market: 'US' | 'TW';
}
export const contextOf = (history: FinancialHistory, kind: PeriodKind): MetricContext => ({
  kind,
  series: history[kind],
  market: history.market,
});

export const FIELD_LABELS: Record<RawField, string> = {
  revenue: '營收',
  costOfRevenue: '營業成本',
  grossProfit: '毛利',
  operatingIncome: '營業利益（標準化）',
  operatingIncomeReported: '營業利益（財報原始）',
  pretaxIncome: '稅前淨利',
  taxProvision: '所得稅費用',
  netIncome: '淨利（歸屬普通股）',
  epsDiluted: '稀釋 EPS',
  epsBasic: '基本 EPS',
  dilutedShares: '稀釋加權股數',
  interestExpense: '利息費用',
  operatingCashFlow: '營業現金流',
  capex: '資本支出',
  fcfYahoo: 'Yahoo 自由現金流',
  equity: '股東權益',
  totalDebt: '有息負債',
  cash: '現金及短期投資',
  totalAssets: '總資產',
  dividendsPaid: '現金股利',
  buybacks: '買回庫藏股',
};

// ------------------------------------------------------------------ dates and fiscal labels

const DAY_MS = 86_400_000;
const dayNumber = (end: string) => Date.parse(`${end}T00:00:00Z`) / DAY_MS;
/** Whole days from `a` to `b` (positive when `b` is later). */
export const daysBetween = (a: string, b: string) => Math.round(dayNumber(b) - dayNumber(a));

const YOY_DAYS = 365;
const YOY_TOLERANCE_DAYS = 25;

/**
 * Index of the period that ended 365 ± 25 days before `ends[index]`, closest to 365 (ties keep the
 * earlier one) — the same rule as year_ago_index in build_fundamentals_us.py, used for annual and
 * quarterly periods alike.
 */
export function yearAgoIndex(ends: readonly string[], index: number): number | null {
  const ended = ends[index];
  if (ended === undefined) return null;
  let best: { distance: number; index: number } | null = null;
  for (let other = 0; other < index; other++) {
    const distance = Math.abs(daysBetween(ends[other]!, ended) - YOY_DAYS);
    if (distance <= YOY_TOLERANCE_DAYS && (best === null || distance < best.distance)) best = { distance, index: other };
  }
  return best?.index ?? null;
}

/**
 * 52/53-week calendars end on the first days of a month (2023-12-30, then 2025-01-04): an end on the
 * 1st–7th belongs to the previous month. For the fiscal year this is the design rule「期末日落1月1–7日
 * FY = 年 − 1」; applying it to every month also keeps fyEndMonth and quarter numbers stable.
 */
export function effectiveMonth(end: string): { year: number; month: number } {
  const [year, month, day] = end.split('-').map(Number) as [number, number, number];
  if (day <= 7) return month === 1 ? { year: year - 1, month: 12 } : { year, month: month - 1 };
  return { year, month };
}

/** Fiscal year-end month: the most frequent (effective) month of the annual period ends; ties → latest. */
export function inferFyEndMonth(annualEnds: readonly string[]): number | null {
  const counts = new Map<number, number>();
  for (const end of annualEnds) {
    const { month } = effectiveMonth(end);
    counts.set(month, (counts.get(month) ?? 0) + 1);
  }
  let best: number | null = null;
  let bestCount = 0;
  for (let index = annualEnds.length - 1; index >= 0; index--) {
    const month = effectiveMonth(annualEnds[index]!).month;
    const count = counts.get(month)!;
    if (count > bestCount) {
      best = month;
      bestCount = count;
    }
  }
  return best;
}

/** The file's fyEndMonth, else inferred from the annual ends, else December. */
export function fyEndMonthOf(history: FinancialHistory): number {
  return history.fyEndMonth ?? inferFyEndMonth(history.annual.end) ?? 12;
}

/**
 * Fiscal year of a period. Annual: the (effective) calendar year of the end date. Quarterly: the fiscal
 * year the quarter belongs to, i.e. the year of the next fiscal year end (Apple's December quarter is
 * FY+1 Q1).
 */
export function fiscalYearOf(end: string, fyEndMonth: number, kind: PeriodKind = 'annual'): number {
  const { year, month } = effectiveMonth(end);
  if (kind === 'annual') return year;
  return month <= fyEndMonth ? year : year + 1;
}

/** m = (end month − fyEndMonth + 12) % 12; round(m / 3): 0 → Q4, 1 → Q1, 2 → Q2, 3 → Q3 (4 → Q4). */
export function fiscalQuarterOf(end: string, fyEndMonth: number): 1 | 2 | 3 | 4 {
  const m = (effectiveMonth(end).month - fyEndMonth + 12) % 12;
  const k = Math.round(m / 3);
  return k === 1 ? 1 : k === 2 ? 2 : k === 3 ? 3 : 4;
}

export interface PeriodLabel {
  end: string;
  fy: number;
  fq: 1 | 2 | 3 | 4 | null;
  /** Axis text: `2024` (annual) or `Q1` (quarterly). */
  short: string;
  /** `FY2024` or `FY2025 Q1`. */
  long: string;
}

export function periodLabels(history: FinancialHistory, kind: PeriodKind): PeriodLabel[] {
  const month = fyEndMonthOf(history);
  return history[kind].end.map((end) => {
    const fy = fiscalYearOf(end, month, kind);
    if (kind === 'annual') return { end, fy, fq: null, short: String(fy), long: `FY${fy}` };
    const fq = fiscalQuarterOf(end, month);
    return { end, fy, fq, short: `Q${fq}`, long: `FY${fy} Q${fq}` };
  });
}

// ------------------------------------------------------------------ trailing twelve months

const QUARTER_GAP = { min: 75, max: 105 };

/** True when quarters `index − count + 1 … index` exist and follow each other without a gap. */
export function consecutiveQuarters(ends: readonly string[], index: number, count = 4): boolean {
  if (index - count + 1 < 0 || index >= ends.length) return false;
  for (let i = index - count + 2; i <= index; i++) {
    const gap = daysBetween(ends[i - 1]!, ends[i]!);
    if (gap < QUARTER_GAP.min || gap > QUARTER_GAP.max) return false;
  }
  return true;
}

/** Sum of four consecutive quarters ending at `index`; null when one is missing or not consecutive. */
export function ttm(
  ends: readonly string[],
  index: number,
  pick: (quarter: number) => number | null,
): { value: number; parts: number[] } | null {
  if (!consecutiveQuarters(ends, index, 4)) return null;
  const parts: number[] = [];
  for (let i = index - 3; i <= index; i++) {
    const value = pick(i);
    if (value === null) return null;
    parts.push(value);
  }
  return { value: parts.reduce((sum, value) => sum + value, 0), parts };
}

// ------------------------------------------------------------------ helpers

const at = (ctx: MetricContext, field: RawField, index: number): number | null => ctx.series.values[field][index] ?? null;
const input = (label: string, value: number | null, unit: MetricUnit = 'money'): MetricInput => ({ label, value, unit });
const fieldInput = (ctx: MetricContext, field: RawField, index: number, unit: MetricUnit = 'money') =>
  input(FIELD_LABELS[field], at(ctx, field, index), unit);
const pct = (value: number) => value * 100;

function result(
  value: number | null,
  formula: string,
  inputs: MetricInput[],
  extra: Partial<Omit<MetricResult, 'value' | 'formula' | 'inputs'>> = {},
): MetricResult {
  return {
    value: value !== null && Number.isFinite(value) ? value : null,
    basis: extra.basis ?? 'reported',
    formula,
    inputs,
    notes: extra.notes ?? [],
    ...(extra.reason ? { reason: extra.reason } : {}),
    ...(extra.abnormal ? { abnormal: true } : {}),
    ...(extra.oiField ? { oiField: extra.oiField } : {}),
  };
}
const missing = (formula: string, inputs: MetricInput[], reason: string, extra: Partial<MetricResult> = {}) =>
  result(null, formula, inputs, { ...extra, reason });

/** Operating income: the as-reported line first, Yahoo's normalized OperatingIncome otherwise. */
export function operatingIncomeAt(
  ctx: MetricContext,
  index: number,
): { value: number; field: 'operatingIncomeReported' | 'operatingIncome' } | null {
  const reported = at(ctx, 'operatingIncomeReported', index);
  if (reported !== null) return { value: reported, field: 'operatingIncomeReported' };
  const normalized = at(ctx, 'operatingIncome', index);
  return normalized !== null ? { value: normalized, field: 'operatingIncome' } : null;
}
const oiNotes = (field: 'operatingIncomeReported' | 'operatingIncome' | undefined) =>
  field === 'operatingIncome' ? ['財報原始營業利益缺漏，改用 Yahoo 標準化營業利益'] : [];
const oiLabel = (field: 'operatingIncomeReported' | 'operatingIncome' | undefined) =>
  field === 'operatingIncome' ? FIELD_LABELS.operatingIncome : FIELD_LABELS.operatingIncomeReported;

/** Statutory corporate rate used when the effective rate is unusable: US 21%, Taiwan 20%. */
export const statutoryTaxRate = (market: 'US' | 'TW') => (market === 'TW' ? 0.2 : 0.21);
const TAX_CLAMP = { min: 0, max: 0.35 };
const ABNORMAL_TAX = { min: 0, max: 60 };

// ------------------------------------------------------------------ plain lines

const raw = (field: RawField, unit: MetricUnit = 'money') => (ctx: MetricContext, index: number) => {
  const value = at(ctx, field, index);
  return value === null
    ? missing(FIELD_LABELS[field], [], `缺${FIELD_LABELS[field]}`)
    : result(value, FIELD_LABELS[field], [input(FIELD_LABELS[field], value, unit)]);
};
export const revenue = raw('revenue');
export const netIncome = raw('netIncome');
export const operatingCashFlow = raw('operatingCashFlow');
export const capex = raw('capex');
export const cash = raw('cash');

/** EPS: diluted, else basic (already split-adjusted by the builder). */
export function eps(ctx: MetricContext, index: number): MetricResult {
  const diluted = at(ctx, 'epsDiluted', index);
  if (diluted !== null) return result(diluted, '稀釋 EPS', [input(FIELD_LABELS.epsDiluted, diluted, 'eps')]);
  const basic = at(ctx, 'epsBasic', index);
  if (basic !== null)
    return result(basic, '基本 EPS', [input(FIELD_LABELS.epsBasic, basic, 'eps')], {
      basis: 'basic',
      notes: ['沒有稀釋 EPS，改用基本 EPS'],
    });
  return missing('稀釋 EPS', [], '缺 EPS');
}

// ------------------------------------------------------------------ margins

/** Gross profit ÷ revenue; without gross profit, (revenue − cost of revenue) ÷ revenue. Revenue ≤ 0 → null. */
export function grossMargin(ctx: MetricContext, index: number): MetricResult {
  const sales = at(ctx, 'revenue', index);
  const gross = at(ctx, 'grossProfit', index);
  const cost = at(ctx, 'costOfRevenue', index);
  const formula = '毛利 ÷ 營收';
  if (sales === null) return missing(formula, [], '缺營收');
  if (sales <= 0) return missing(formula, [input('營收', sales)], '營收 ≤ 0，不計算');
  if (gross !== null) return result(margin(gross, sales), formula, [input('毛利', gross), input('營收', sales)]);
  if (cost !== null)
    return result(margin(sales - cost, sales), '(營收 − 營業成本) ÷ 營收', [input('營收', sales), input('營業成本', cost)], {
      basis: 'derived',
      notes: ['財報沒有毛利欄位，以營收減營業成本推算'],
    });
  return missing(formula, [input('營收', sales)], '沒有毛利與營業成本（金融業格式常見）');
}

/** Operating income ÷ revenue, preferring the as-reported operating income (the result says which). */
export function operatingMargin(ctx: MetricContext, index: number): MetricResult {
  const sales = at(ctx, 'revenue', index);
  const oi = operatingIncomeAt(ctx, index);
  const formula = `${oiLabel(oi?.field)} ÷ 營收`;
  if (sales === null) return missing(formula, [], '缺營收');
  if (sales <= 0) return missing(formula, [input('營收', sales)], '營收 ≤ 0，不計算');
  if (!oi) return missing(formula, [input('營收', sales)], '沒有營業利益（金融業格式常見）');
  return result(margin(oi.value, sales), formula, [input(oiLabel(oi.field), oi.value), input('營收', sales)], {
    oiField: oi.field,
    notes: oiNotes(oi.field),
  });
}

export function netMargin(ctx: MetricContext, index: number): MetricResult {
  const sales = at(ctx, 'revenue', index);
  const profit = at(ctx, 'netIncome', index);
  const formula = '淨利 ÷ 營收';
  if (sales === null || profit === null) return missing(formula, [fieldInput(ctx, 'netIncome', index), fieldInput(ctx, 'revenue', index)], sales === null ? '缺營收' : '缺淨利');
  if (sales <= 0) return missing(formula, [input('營收', sales)], '營收 ≤ 0，不計算');
  return result(margin(profit, sales), formula, [input('淨利', profit), input('營收', sales)]);
}

// ------------------------------------------------------------------ non-operating items and tax

/** Pretax income − operating income (may be negative). */
export function nonOperatingIncome(ctx: MetricContext, index: number): MetricResult {
  const pretax = at(ctx, 'pretaxIncome', index);
  const oi = operatingIncomeAt(ctx, index);
  const formula = `稅前淨利 − ${oiLabel(oi?.field)}`;
  const inputs = [input('稅前淨利', pretax), input(oiLabel(oi?.field), oi?.value ?? null)];
  if (pretax === null) return missing(formula, inputs, '缺稅前淨利');
  if (!oi) return missing(formula, inputs, '沒有營業利益（金融業格式常見）');
  return result(pretax - oi.value, formula, inputs, { oiField: oi.field, notes: oiNotes(oi.field) });
}

/** Non-operating income ÷ pretax income, only when pretax income > 0. */
export function nonOperatingShare(ctx: MetricContext, index: number): MetricResult {
  const base = nonOperatingIncome(ctx, index);
  const pretax = at(ctx, 'pretaxIncome', index);
  const formula = '業外損益 ÷ 稅前淨利';
  const inputs = [input('業外損益', base.value), input('稅前淨利', pretax)];
  if (base.value === null) return missing(formula, inputs, base.reason ?? '缺業外損益');
  if (pretax === null || pretax <= 0) return missing(formula, inputs, '稅前淨利 ≤ 0，不計算占比');
  return result(pct(base.value / pretax), formula, inputs, { oiField: base.oiField, notes: base.notes });
}

/** Tax provision ÷ pretax income (pretax > 0). Below 0% or above 60% is shown but flagged. */
export function effectiveTaxRate(ctx: MetricContext, index: number): MetricResult {
  const pretax = at(ctx, 'pretaxIncome', index);
  const tax = at(ctx, 'taxProvision', index);
  const formula = '所得稅費用 ÷ 稅前淨利';
  const inputs = [input('所得稅費用', tax), input('稅前淨利', pretax)];
  if (pretax === null || tax === null) return missing(formula, inputs, pretax === null ? '缺稅前淨利' : '缺所得稅費用');
  if (pretax <= 0) return missing(formula, inputs, '稅前淨利 ≤ 0，有效稅率無意義');
  const rate = pct(tax / pretax);
  const abnormal = rate < ABNORMAL_TAX.min || rate > ABNORMAL_TAX.max;
  return result(rate, formula, inputs, abnormal ? { abnormal, notes: ['異常（一次性稅務項目）'] } : {});
}

// ------------------------------------------------------------------ cash flow

/** Operating cash flow − capex (capex stored as a positive outflow). */
export function freeCashFlow(ctx: MetricContext, index: number): MetricResult {
  const ocf = at(ctx, 'operatingCashFlow', index);
  const spend = at(ctx, 'capex', index);
  const yahoo = at(ctx, 'fcfYahoo', index);
  const formula = '營業現金流 − 資本支出';
  const inputs = [input('營業現金流', ocf), input('資本支出', spend)];
  if (ocf === null || spend === null) return missing(formula, inputs, ocf === null ? '缺營業現金流' : '缺資本支出');
  const value = ocf - spend;
  const notes: string[] = [];
  if (yahoo !== null) {
    inputs.push(input(FIELD_LABELS.fcfYahoo, yahoo));
    const scale = Math.max(Math.abs(value), Math.abs(yahoo));
    if (scale > 0 && Math.abs(value - yahoo) / scale > 0.05) notes.push('與 Yahoo 自由現金流相差超過 5%（僅供交叉檢查）');
  }
  return result(value, formula, inputs, { notes });
}

export function fcfMargin(ctx: MetricContext, index: number): MetricResult {
  const fcf = freeCashFlow(ctx, index);
  const sales = at(ctx, 'revenue', index);
  const formula = '自由現金流 ÷ 營收';
  const inputs = [input('自由現金流', fcf.value), input('營收', sales)];
  if (fcf.value === null) return missing(formula, inputs, fcf.reason ?? '缺自由現金流');
  if (sales === null || sales <= 0) return missing(formula, inputs, sales === null ? '缺營收' : '營收 ≤ 0，不計算');
  return result(margin(fcf.value, sales), formula, inputs);
}

/** Free cash flow ÷ net income, only while net income > 0. */
export function fcfToNetIncome(ctx: MetricContext, index: number): MetricResult {
  const fcf = freeCashFlow(ctx, index);
  const profit = at(ctx, 'netIncome', index);
  const formula = '自由現金流 ÷ 淨利';
  const inputs = [input('自由現金流', fcf.value), input('淨利', profit)];
  if (fcf.value === null) return missing(formula, inputs, fcf.reason ?? '缺自由現金流');
  if (profit === null || profit <= 0) return missing(formula, inputs, profit === null ? '缺淨利' : '淨利 ≤ 0，不計算');
  return result(pct(fcf.value / profit), formula, inputs);
}

// ------------------------------------------------------------------ returns

const NEGATIVE_EQUITY = '股東權益 ≤ 0（負權益），不計算';

/**
 * Annual: net income ÷ average equity, average = (this end + previous year end) ÷ 2; without the previous
 * year only this end (approx). Quarterly: TTM net income (four consecutive quarters) ÷ (this quarter end
 * + the quarter a year earlier) ÷ 2. Any equity ≤ 0 → null.
 */
export function roe(ctx: MetricContext, index: number): MetricResult {
  const ends = ctx.series.end;
  const quarterly = ctx.kind === 'quarterly';
  const formula = quarterly ? '近 4 季淨利 ÷ 平均股東權益' : '淨利 ÷ 平均股東權益';
  let profit: number | null;
  const inputs: MetricInput[] = [];
  if (quarterly) {
    const sum = ttm(ends, index, (i) => at(ctx, 'netIncome', i));
    if (!sum) return missing(formula, [], '需要連續 4 季淨利（TTM）');
    profit = sum.value;
    inputs.push(input('近 4 季淨利', profit));
  } else {
    profit = at(ctx, 'netIncome', index);
    inputs.push(input('淨利', profit));
  }
  const equity = at(ctx, 'equity', index);
  const previousIndex = yearAgoIndex(ends, index);
  const previous = previousIndex === null ? null : at(ctx, 'equity', previousIndex);
  inputs.push(input('期末權益', equity), input(quarterly ? '一年前權益' : '上期末權益', previous));
  if (profit === null) return missing(formula, inputs, '缺淨利');
  if (equity === null) return missing(formula, inputs, '缺股東權益');
  if (equity <= 0 || (previous !== null && previous <= 0)) return missing(formula, inputs, NEGATIVE_EQUITY);
  const average = previous === null ? equity : (equity + previous) / 2;
  inputs.push(input('平均權益', average));
  const notes = quarterly ? ['淨利為近 4 季合計（TTM）'] : [];
  if (previous === null) notes.push('沒有一年前的權益，只用期末權益（近似）');
  return result(pct(profit / average), formula, inputs, {
    basis: previous === null ? 'approx' : quarterly ? 'ttm' : 'reported',
    notes,
  });
}

/** Invested capital = total debt + equity − cash. Missing debt or cash counts as 0 (approx). */
export function investedCapital(
  ctx: MetricContext,
  index: number,
): { value: number; approx: boolean; debt: number | null; equity: number; cash: number | null } | null {
  const equity = at(ctx, 'equity', index);
  if (equity === null) return null;
  const debt = at(ctx, 'totalDebt', index);
  const cashValue = at(ctx, 'cash', index);
  return { value: (debt ?? 0) + equity - (cashValue ?? 0), approx: debt === null || cashValue === null, debt, equity, cash: cashValue };
}

/**
 * NOPAT ÷ average invested capital. NOPAT = operating income × (1 − τ); τ = effective tax rate clamped to
 * [0, 35%], or the statutory rate (US 21%, TW 20%) when pretax ≤ 0 or tax is missing. Quarterly figures
 * use TTM operating income, tax and pretax. Invested capital ≤ 0 → null.
 */
export function roic(ctx: MetricContext, index: number): MetricResult {
  const ends = ctx.series.end;
  const quarterly = ctx.kind === 'quarterly';
  const formula = '營業利益 × (1 − 稅率) ÷ 平均投入資本';
  const notes: string[] = [];
  let oi: number;
  let field: 'operatingIncomeReported' | 'operatingIncome' | undefined;
  let pretax: number | null;
  let tax: number | null;
  if (quarterly) {
    const fields: ('operatingIncomeReported' | 'operatingIncome')[] = [];
    const sum = ttm(ends, index, (i) => {
      const found = operatingIncomeAt(ctx, i);
      if (found) fields.push(found.field);
      return found?.value ?? null;
    });
    if (!sum) return missing(formula, [], '需要連續 4 季營業利益（TTM）');
    oi = sum.value;
    field = fields.includes('operatingIncome') ? 'operatingIncome' : 'operatingIncomeReported';
    pretax = ttm(ends, index, (i) => at(ctx, 'pretaxIncome', i))?.value ?? null;
    tax = ttm(ends, index, (i) => at(ctx, 'taxProvision', i))?.value ?? null;
    notes.push('營業利益與稅率為近 4 季合計（TTM）');
  } else {
    const found = operatingIncomeAt(ctx, index);
    if (!found) return missing(formula, [], '沒有營業利益（金融業格式常見）');
    oi = found.value;
    field = found.field;
    pretax = at(ctx, 'pretaxIncome', index);
    tax = at(ctx, 'taxProvision', index);
  }
  notes.push(...oiNotes(field));
  let rate: number;
  let statutory = false;
  if (pretax !== null && pretax > 0 && tax !== null) {
    rate = Math.min(TAX_CLAMP.max, Math.max(TAX_CLAMP.min, tax / pretax));
    if (rate !== tax / pretax) notes.push('有效稅率超出 0–35%，已夾在範圍內');
  } else {
    rate = statutoryTaxRate(ctx.market);
    statutory = true;
    notes.push(`有效稅率不可用，改用法定稅率 ${Math.round(rate * 100)}%`);
  }
  const nopat = oi * (1 - rate);
  const current = investedCapital(ctx, index);
  const previousIndex = yearAgoIndex(ends, index);
  const previous = previousIndex === null ? null : investedCapital(ctx, previousIndex);
  const inputs = [
    input(quarterly ? '近 4 季營業利益' : oiLabel(field), oi),
    input('稅率', pct(rate), 'pct'),
    input('稅後營業利益 NOPAT', nopat),
    input('有息負債', current?.debt ?? null),
    input('股東權益', current?.equity ?? null),
    input('現金及短期投資', current?.cash ?? null),
    input('期末投入資本', current?.value ?? null),
    input('一年前投入資本', previous?.value ?? null),
  ];
  notes.push('有息負債取自 Yahoo TotalDebt，可能含租賃負債');
  if (!current) return missing(formula, inputs, '缺股東權益，無法計算投入資本', { notes });
  if (current.value <= 0 || (previous && previous.value <= 0))
    return missing(formula, inputs, '投入資本（有息負債＋權益−現金）≤ 0：現金比投入的資本還多，ROIC 無意義', { notes });
  if (current.approx || previous?.approx) notes.push('缺有息負債或現金，缺的項目以 0 計（近似）');
  const average = previous ? (current.value + previous.value) / 2 : current.value;
  if (!previous) notes.push('沒有一年前的投入資本，只用期末值（近似）');
  inputs.push(input('平均投入資本', average));
  const approx = !previous || current.approx || !!previous?.approx;
  return result(pct(nopat / average), formula, inputs, {
    basis: statutory ? 'statutory-tax' : approx ? 'approx' : quarterly ? 'ttm' : 'reported',
    oiField: field,
    notes,
  });
}

// ------------------------------------------------------------------ balance sheet strength

export function debtToEquity(ctx: MetricContext, index: number): MetricResult {
  const debt = at(ctx, 'totalDebt', index);
  const equity = at(ctx, 'equity', index);
  const formula = '有息負債 ÷ 股東權益';
  const inputs = [input('有息負債', debt), input('股東權益', equity)];
  const notes = ['有息負債取自 Yahoo TotalDebt，可能含租賃負債'];
  if (debt === null || equity === null) return missing(formula, inputs, debt === null ? '缺有息負債' : '缺股東權益', { notes });
  if (equity <= 0) return missing(formula, inputs, NEGATIVE_EQUITY, { notes });
  return result(debt / equity, formula, inputs, { notes });
}

/** Operating income ÷ interest expense, only when interest expense > 0. */
export function interestCoverage(ctx: MetricContext, index: number): MetricResult {
  const oi = operatingIncomeAt(ctx, index);
  const interest = at(ctx, 'interestExpense', index);
  const formula = `${oiLabel(oi?.field)} ÷ 利息費用`;
  const inputs = [input(oiLabel(oi?.field), oi?.value ?? null), input('利息費用', interest)];
  if (!oi) return missing(formula, inputs, '沒有營業利益（金融業格式常見）');
  if (interest === null || interest <= 0) return missing(formula, inputs, interest === null ? '缺利息費用' : '利息費用 ≤ 0，不計算');
  return result(oi.value / interest, formula, inputs, { oiField: oi.field, notes: oiNotes(oi.field) });
}

// ------------------------------------------------------------------ change over time

export interface ChangeResult {
  value: number | null;
  /** growth: percent change; diff: difference in the metric's own unit (pp for percentages). */
  mode: 'growth' | 'diff';
  /** Sign changes: base ≤ 0 → 轉盈 / 續虧 (value null); base > 0 and now negative → 轉虧. */
  label: '轉盈' | '轉虧' | '續虧' | null;
  baseIndex: number | null;
  reason?: string;
}

/**
 * Year over year against the period that ended 365 ± 25 days earlier. Money and EPS change in percent
 * (base ≤ 0 → null with 轉盈 / 續虧); percentages and multiples change by their difference.
 */
export function yoy(values: readonly (number | null)[], ends: readonly string[], index: number, unit: MetricUnit): ChangeResult {
  const mode = unit === 'pct' || unit === 'times' ? 'diff' : 'growth';
  const baseIndex = yearAgoIndex(ends, index);
  if (baseIndex === null) return { value: null, mode, label: null, baseIndex, reason: '沒有一年前的同期資料' };
  const current = values[index] ?? null;
  const base = values[baseIndex] ?? null;
  if (current === null || base === null) return { value: null, mode, label: null, baseIndex, reason: '本期或去年同期缺值' };
  if (mode === 'diff') return { value: current - base, mode, label: null, baseIndex };
  if (base <= 0)
    return { value: null, mode, label: current > 0 ? '轉盈' : '續虧', baseIndex, reason: '去年同期 ≤ 0，成長率無意義' };
  return { value: growth(current, base), mode, label: current < 0 ? '轉虧' : null, baseIndex };
}

export interface CagrResult {
  value: number | null;
  /** Actual years between the two period ends (days ÷ 365.25). */
  years: number | null;
  from: string | null;
  to: string | null;
  reason?: string;
}

const MIN_CAGR_YEARS = 0.9;

/** (last ÷ first)^(1 ÷ years) − 1 with years = days between the ends ÷ 365.25; first or last ≤ 0 → null. */
export function cagr(first: { value: number | null; end: string }, last: { value: number | null; end: string }): CagrResult {
  const years = daysBetween(first.end, last.end) / 365.25;
  const base = { years, from: first.end, to: last.end };
  if (first.value === null || last.value === null) return { ...base, value: null, reason: '缺值' };
  if (years < MIN_CAGR_YEARS) return { ...base, value: null, reason: '期間不到一年' };
  if (first.value <= 0 || last.value <= 0) return { ...base, value: null, reason: '期間有虧損，無法計算' };
  const value = (Math.pow(last.value / first.value, 1 / years) - 1) * 100;
  return { ...base, value: Number.isFinite(value) ? value : null };
}

/**
 * CAGR over a window of period indices (ascending): latest non-null value against the earliest non-null
 * one. With `quarters`, the start must be the same fiscal quarter as the end (no seasonal distortion).
 */
export function windowCagr(
  values: readonly (number | null)[],
  ends: readonly string[],
  indices: readonly number[],
  quarters?: readonly (number | null)[],
): CagrResult {
  const present = indices.filter((index) => values[index] !== null && values[index] !== undefined);
  const lastIndex = present[present.length - 1];
  if (lastIndex === undefined) return { value: null, years: null, from: null, to: null, reason: '缺值' };
  const firstIndex = present.find((index) => !quarters || quarters[index] === quarters[lastIndex]);
  if (firstIndex === undefined || firstIndex === lastIndex)
    return { value: null, years: null, from: null, to: ends[lastIndex] ?? null, reason: '資料不足兩期' };
  return cagr({ value: values[firstIndex]!, end: ends[firstIndex]! }, { value: values[lastIndex]!, end: ends[lastIndex]! });
}

/** Mean of the non-null values in the window (for percentages and multiples, where CAGR means nothing). */
export function windowAverage(values: readonly (number | null)[], indices: readonly number[]): { value: number | null; count: number } {
  const present = indices.map((index) => values[index]).filter((value): value is number => value !== null && value !== undefined);
  return { value: present.length ? present.reduce((sum, value) => sum + value, 0) / present.length : null, count: present.length };
}

// ------------------------------------------------------------------ registry for the page

export type MetricId =
  | 'revenue'
  | 'eps'
  | 'netIncome'
  | 'grossMargin'
  | 'operatingMargin'
  | 'netMargin'
  | 'nonOperatingIncome'
  | 'nonOperatingShare'
  | 'effectiveTaxRate'
  | 'operatingCashFlow'
  | 'capex'
  | 'freeCashFlow'
  | 'fcfMargin'
  | 'fcfToNetIncome'
  | 'roe'
  | 'roic'
  | 'debtToEquity'
  | 'cash'
  | 'interestCoverage';

export interface MetricDef {
  id: MetricId;
  label: string;
  unit: MetricUnit;
  compute: (ctx: MetricContext, index: number) => MetricResult;
}

export const METRICS: Record<MetricId, MetricDef> = {
  revenue: { id: 'revenue', label: '營收', unit: 'money', compute: revenue },
  eps: { id: 'eps', label: 'EPS', unit: 'eps', compute: eps },
  netIncome: { id: 'netIncome', label: '淨利', unit: 'money', compute: netIncome },
  grossMargin: { id: 'grossMargin', label: '毛利率', unit: 'pct', compute: grossMargin },
  operatingMargin: { id: 'operatingMargin', label: '營業利益率', unit: 'pct', compute: operatingMargin },
  netMargin: { id: 'netMargin', label: '淨利率', unit: 'pct', compute: netMargin },
  nonOperatingIncome: { id: 'nonOperatingIncome', label: '業外損益', unit: 'money', compute: nonOperatingIncome },
  nonOperatingShare: { id: 'nonOperatingShare', label: '業外占稅前', unit: 'pct', compute: nonOperatingShare },
  effectiveTaxRate: { id: 'effectiveTaxRate', label: '有效稅率', unit: 'pct', compute: effectiveTaxRate },
  operatingCashFlow: { id: 'operatingCashFlow', label: '營業現金流', unit: 'money', compute: operatingCashFlow },
  capex: { id: 'capex', label: '資本支出', unit: 'money', compute: capex },
  freeCashFlow: { id: 'freeCashFlow', label: '自由現金流', unit: 'money', compute: freeCashFlow },
  fcfMargin: { id: 'fcfMargin', label: 'FCF 利潤率', unit: 'pct', compute: fcfMargin },
  fcfToNetIncome: { id: 'fcfToNetIncome', label: 'FCF ÷ 淨利', unit: 'pct', compute: fcfToNetIncome },
  roe: { id: 'roe', label: 'ROE', unit: 'pct', compute: roe },
  roic: { id: 'roic', label: 'ROIC', unit: 'pct', compute: roic },
  debtToEquity: { id: 'debtToEquity', label: '負債權益比', unit: 'times', compute: debtToEquity },
  cash: { id: 'cash', label: '現金', unit: 'money', compute: cash },
  interestCoverage: { id: 'interestCoverage', label: '利息保障倍數', unit: 'times', compute: interestCoverage },
};

export type MetricGroupId = 'growth' | 'profitability' | 'nonOperating' | 'cashFlow' | 'returns' | 'strength';
export const METRIC_GROUPS: { id: MetricGroupId; title: string; metrics: MetricId[] }[] = [
  { id: 'growth', title: '成長', metrics: ['revenue', 'eps', 'netIncome'] },
  { id: 'profitability', title: '獲利能力', metrics: ['grossMargin', 'operatingMargin', 'netMargin'] },
  { id: 'nonOperating', title: '業外與稅', metrics: ['nonOperatingIncome', 'nonOperatingShare', 'effectiveTaxRate'] },
  { id: 'cashFlow', title: '現金流', metrics: ['operatingCashFlow', 'capex', 'freeCashFlow', 'fcfMargin', 'fcfToNetIncome'] },
  { id: 'returns', title: '報酬率', metrics: ['roe', 'roic'] },
  { id: 'strength', title: '財務體質', metrics: ['debtToEquity', 'cash', 'interestCoverage'] },
];

/** One metric over every period of a block (index-aligned with `series.end`). */
export function metricSeries(ctx: MetricContext, id: MetricId): MetricResult[] {
  return ctx.series.end.map((_, index) => METRICS[id].compute(ctx, index));
}
