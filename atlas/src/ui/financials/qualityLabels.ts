import type { QualityLevel } from '../../fundamentals/FinancialHistory';

export const LEVEL_TEXT: Record<QualityLevel, string> = {
  good: '資料完整',
  partial: '部分缺漏',
  limited: '資料有限',
};
