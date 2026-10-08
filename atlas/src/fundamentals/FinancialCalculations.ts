import type { CompanyFundamentals, FinancialMetric } from './FundamentalsProvider';
export function margin(value: number | null, revenue: number | null): number | null {
  if (value === null || revenue === null || revenue === 0) return null;
  const result = (value / revenue) * 100;
  return Number.isFinite(result) ? result : null;
}
export function margins(r: CompanyFundamentals) {
  return {
    gross: margin(r.grossProfit, r.revenue),
    operating: margin(r.operatingIncome, r.revenue),
    net: margin(r.netIncome, r.revenue),
  };
}
export function growth(current: number | null, previous: number | null): number | null {
  if (current === null || previous === null || previous === 0) return null;
  const result = ((current - previous) / Math.abs(previous)) * 100;
  return Number.isFinite(result) ? result : null;
}
export function financialGrowth(
  records: CompanyFundamentals[],
  record: CompanyFundamentals,
  metric: FinancialMetric,
) {
  const yoy = records.find(
    (r) =>
      r.period === record.period &&
      r.fiscalYear === record.fiscalYear - 1 &&
      r.fiscalQuarter === record.fiscalQuarter,
  );
  const previousQuarter =
    record.fiscalQuarter === 1
      ? 4
      : record.fiscalQuarter !== null
        ? record.fiscalQuarter - 1
        : null;
  const previousYear = record.fiscalYear - (record.fiscalQuarter === 1 ? 1 : 0);
  const qoq =
    record.period === 'quarterly'
      ? records.find(
          (r) =>
            r.period === 'quarterly' &&
            r.fiscalYear === previousYear &&
            r.fiscalQuarter === previousQuarter,
        )
      : null;
  return {
    yoy: growth(record[metric], yoy?.[metric] ?? null),
    qoq: growth(record[metric], qoq?.[metric] ?? null),
  };
}
