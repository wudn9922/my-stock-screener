export const financialMetrics = [
  'revenue',
  'costOfRevenue',
  'grossProfit',
  'operatingExpenses',
  'operatingIncome',
  'netIncome',
  'epsBasic',
  'epsDiluted',
  'operatingCashFlow',
  'capex',
  'freeCashFlow',
  'cash',
  'totalAssets',
  'totalLiabilities',
  'equity',
] as const;
export type FinancialMetric = (typeof financialMetrics)[number];
export type FinancialPeriod = 'quarterly' | 'annual';
export interface FinancialSource {
  concept: string;
  unit: string;
  value: number;
  accession: string;
  form: string;
  filed: string;
  secUrl: string;
  filingUrl: string;
  start: string | null;
  end: string;
  fiscalYearFocus: number;
  fiscalPeriodFocus: string;
  kind: 'instant' | 'duration';
}
export interface FinancialProvenance {
  derived: boolean;
  calculation: string | null;
  inputs: FinancialSource[];
}
export type CompanyFundamentals = {
  symbol: string;
  issuerName?: string;
  cik?: string;
  period: FinancialPeriod;
  fiscalYear: number;
  fiscalQuarter: 1 | 2 | 3 | 4 | null;
  periodStart: string;
  periodEnd: string;
  filingDate: string;
  form: string;
  accession: string;
  sourceConcepts: Partial<Record<FinancialMetric, FinancialProvenance>>;
  warnings: string[];
} & Record<FinancialMetric, number | null>;
export interface FundamentalsProvider {
  readonly id: string;
  getFinancials(
    symbol: string,
    period: FinancialPeriod,
    signal?: AbortSignal,
  ): Promise<CompanyFundamentals[]>;
}
