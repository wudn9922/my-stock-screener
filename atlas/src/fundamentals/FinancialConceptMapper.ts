import type { FinancialMetric } from './FundamentalsProvider';
export interface ConceptMapping {
  kind: 'instant' | 'duration';
  unit: 'USD' | 'USD/shares';
  concepts: readonly string[];
}
/** Priority is explicit; broad totals (CostsAndExpenses, restricted cash, debt) are not synonyms. */
export const conceptMappings: Record<Exclude<FinancialMetric, 'freeCashFlow'>, ConceptMapping> = {
  revenue: {
    kind: 'duration',
    unit: 'USD',
    concepts: [
      'RevenueFromContractWithCustomerExcludingAssessedTax',
      'RevenueFromContractWithCustomerIncludingAssessedTax',
      'Revenues',
      'SalesRevenueNet',
      'SalesRevenueGoodsNet',
      'RevenueFromSaleOfGoods',
      // Bank total revenue net of interest expense; never sum overlapping interest concepts.
      'RevenuesNetOfInterestExpense',
    ],
  },
  costOfRevenue: {
    kind: 'duration',
    unit: 'USD',
    concepts: ['CostOfRevenue', 'CostOfGoodsAndServicesSold', 'CostOfGoodsSold'],
  },
  grossProfit: { kind: 'duration', unit: 'USD', concepts: ['GrossProfit'] },
  operatingExpenses: { kind: 'duration', unit: 'USD', concepts: ['OperatingExpenses'] },
  operatingIncome: { kind: 'duration', unit: 'USD', concepts: ['OperatingIncomeLoss'] },
  netIncome: {
    kind: 'duration',
    unit: 'USD',
    concepts: ['NetIncomeLoss', 'ProfitLoss', 'NetIncomeLossAvailableToCommonStockholdersBasic'],
  },
  epsBasic: {
    kind: 'duration',
    unit: 'USD/shares',
    concepts: ['EarningsPerShareBasic', 'EarningsPerShareBasicAndDiluted'],
  },
  epsDiluted: {
    kind: 'duration',
    unit: 'USD/shares',
    concepts: ['EarningsPerShareDiluted', 'EarningsPerShareBasicAndDiluted'],
  },
  operatingCashFlow: {
    kind: 'duration',
    unit: 'USD',
    concepts: [
      'NetCashProvidedByUsedInOperatingActivities',
      'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations',
    ],
  },
  capex: {
    kind: 'duration',
    unit: 'USD',
    concepts: ['PaymentsToAcquirePropertyPlantAndEquipment', 'PaymentsToAcquireProductiveAssets'],
  },
  cash: {
    kind: 'instant',
    unit: 'USD',
    concepts: ['CashAndCashEquivalentsAtCarryingValue', 'Cash'],
  },
  totalAssets: { kind: 'instant', unit: 'USD', concepts: ['Assets'] },
  totalLiabilities: { kind: 'instant', unit: 'USD', concepts: ['Liabilities'] },
  equity: {
    kind: 'instant',
    unit: 'USD',
    concepts: [
      'StockholdersEquity',
      'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
      'PartnersCapital',
      'MembersEquity',
    ],
  },
};
export class FinancialConceptMapper {
  mappings = conceptMappings;
  concepts(metric: Exclude<FinancialMetric, 'freeCashFlow'>) {
    return this.mappings[metric].concepts;
  }
}
