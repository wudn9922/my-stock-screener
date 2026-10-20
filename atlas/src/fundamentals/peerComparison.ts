import type { GroupItem } from '../report/schema';
import type { FinancialHistory, PeriodKind } from './FinancialHistory';
import { METRICS, contextOf, metricSeries, yoy, type MetricId } from './financialMetrics';

/** An industry match is required; a shared broad sector alone is not a peer match. */
export function industryPeers(symbol: string, items: readonly GroupItem[]): GroupItem[] {
  const current = items.find((item) => item.symbol === symbol);
  const industry = current?.profile?.industry;
  if (!industry) return [];
  return items
    .filter((item) => item.symbol !== symbol && item.profile?.industry === industry)
    .sort((a, b) => (b.profile?.marketCap ?? 0) - (a.profile?.marketCap ?? 0))
    .slice(0, 4);
}

export const PEER_COLUMNS = [
  { id: 'revenue', label: '營收年增', growth: true },
  { id: 'eps', label: 'EPS 年增', growth: true },
  { id: 'grossMargin', label: '毛利率', growth: false },
  { id: 'operatingMargin', label: '營業利益率', growth: false },
  { id: 'roe', label: 'ROE', growth: false },
  { id: 'fcfMargin', label: 'FCF 利潤率', growth: false },
] satisfies { id: MetricId; label: string; growth: boolean }[];

export function peerFigures(history: FinancialHistory, kind: PeriodKind) {
  const ctx = contextOf(history, kind);
  const index = ctx.series.end.length - 1;
  return {
    date: ctx.series.end[index] ?? null,
    figures: PEER_COLUMNS.map(({ id, growth }) => {
      if (index < 0) return { value: null, reason: `沒有${kind === 'quarterly' ? '季度' : '年度'}資料` };
      if (!growth) return METRICS[id].compute(ctx, index);
      const values = metricSeries(ctx, id).map((entry) => entry.value);
      const result = yoy(values, ctx.series.end, index, METRICS[id].unit);
      return { value: result.value, reason: result.label ?? result.reason };
    }),
  };
}
