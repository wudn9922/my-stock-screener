import {
  MIN_EPS_PERIODS,
  MIN_VALID_POINTS,
  PE_MAX,
  PUBLISH_DELAY_DAYS,
  SHORT_COVERAGE_YEARS,
  SHORT_EPS_PERIODS,
  formatMultiple,
  type CurrentPe,
  type PeRiverModel,
  type PeRiverUnavailableReason,
} from '../../fundamentals/peRiver';

/** Which EPS the river is built on, in words. */
export function epsBasisText(model: PeRiverModel): string {
  const { ttm, annual } = model.basisCounts;
  const type = model.usedBasic ? '稀釋 EPS（缺值期別以基本 EPS 代替）' : '稀釋 EPS';
  if (annual === 0) return `近四季單季${type}合計（TTM）`;
  if (ttm === 0) return `年度${type}（季度資料不足連續 4 季，無法合計 TTM）`;
  return `近四季單季${type}合計（TTM）${ttm} 週＋年度 EPS ${annual} 週（季度不足連續 4 季時以最新已公布的年度 EPS 代替）`;
}

export const years = (value: number) => `${value.toFixed(1)} 年`;

export function coverageText(model: PeRiverModel): string {
  return `有效樣本 ${model.validCount} 週（共 ${model.epsPeriodCount} 組不同 EPS）· 涵蓋 ${years(model.coverageYears)}（股價資料 ${years(model.priceYears)}）`;
}

/** Weeks left out of the statistics, by reason; empty when none. */
export function excludedText(model: PeRiverModel): string {
  const parts: string[] = [];
  if (model.excluded.loss > 0) parts.push(`EPS ≤ 0（虧損）${model.excluded.loss} 週`);
  if (model.excluded.outlier > 0) parts.push(`本益比 > ${PE_MAX} 倍 ${model.excluded.outlier} 週`);
  if (model.excluded['no-eps'] > 0) parts.push(`尚無已公布 EPS ${model.excluded['no-eps']} 週`);
  return parts.length ? `不計入：${parts.join('、')}` : '';
}

export function delayText(market: 'US' | 'TW'): string {
  if (market === 'TW')
    return `為避免前視偏誤，EPS 在期末後 ${PUBLISH_DELAY_DAYS.TW.quarter} 天（第二季 ${PUBLISH_DELAY_DAYS.TW.secondQuarter} 天、第四季與年度 ${PUBLISH_DELAY_DAYS.TW.yearEnd} 天，皆為法定公布期限）才納入。`;
  return `為避免前視偏誤，EPS 在期末後 ${PUBLISH_DELAY_DAYS.US.quarter} 天（第四季與年度 ${PUBLISH_DELAY_DAYS.US.yearEnd} 天）才納入。`;
}

export function shortWarning(model: PeRiverModel): string | null {
  if (!model.short) return null;
  const reasons: string[] = [];
  if (model.coverageYears < SHORT_COVERAGE_YEARS) reasons.push(`涵蓋僅 ${years(model.coverageYears)}`);
  if (model.epsPeriodCount < SHORT_EPS_PERIODS) reasons.push(`EPS 僅 ${model.epsPeriodCount} 組`);
  return `歷史偏短（${reasons.join('、')}），百分位與河流倍數僅供參考。`;
}

/** 「溢價 12.3%」 / 「折價 8.1%」 against the stock's own median. */
export function premiumText(current: CurrentPe): string {
  const abs = Math.abs(current.premiumPct).toFixed(1);
  if (Math.abs(current.premiumPct) < 0.05) return `與歷史中位數（${formatMultiple(current.median)}）相當`;
  return `相對歷史中位數（${formatMultiple(current.median)}）${current.premiumPct > 0 ? '溢價' : '折價'} ${abs}%`;
}

export function percentileText(current: CurrentPe): string {
  const rank = Math.min(100, Math.max(0, Math.round(current.percentile)));
  return `歷史第 ${rank} 百分位`;
}

/** Why there is no river, in the voice of the card. */
export function unavailableDetail(reason: PeRiverUnavailableReason, extra: { validCount?: number; epsPeriodCount?: number; coverageYears?: number; priceYears?: number }): string | null {
  if (reason !== 'insufficient') return null;
  const parts: string[] = [];
  if (extra.validCount !== undefined) parts.push(`有效本益比 ${extra.validCount} 週（至少需要 ${MIN_VALID_POINTS} 週）`);
  if (extra.epsPeriodCount !== undefined) parts.push(`不同 EPS ${extra.epsPeriodCount} 組（至少需要 ${MIN_EPS_PERIODS} 組）`);
  if (extra.coverageYears !== undefined && extra.coverageYears > 0) parts.push(`可用歷史 ${years(extra.coverageYears)}`);
  return parts.join(' · ') || null;
}
