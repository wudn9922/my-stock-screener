import type { FinancialHistory } from './FinancialHistory';
import { describePe } from './ValuationProvider';
import type { PeFigures } from './peFigures';
import { epsAdjustments, reportedEpsAt, restatedEps, restatedEpsLabel } from './epsBasis';
import { consecutiveQuarters } from './financialMetrics';
import { reportDelayDays } from './stockDividendAdjust';

/**
 * Chart header P/E for Taiwan stocks on today's share basis. The market file's figures (exchange TTM P/E, annual EPS
 * from the exchange statement or Yahoo) divide today's price by EPS printed before later 配股 / splits: after a stock
 * dividend the P/E reads too low (揚秦 2755: reported TTM 6.42 vs 5.744 on today's shares, about 11%). When the
 * financials file shows that the latest four quarters (or the fiscal year) need a restatement, the P/E is recomputed
 * from the restated EPS; otherwise the market file's figures stay as they are.
 */

const DAY_MS = 86_400_000;
const parse = (day: string) => Date.parse(`${day}T00:00:00Z`);
const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10);

/** Newest Taiwan quarter end whose report was due by `today` (Q1/Q3 +45, Q2 +60, Q4 +90 days). */
export function latestDueQuarter(today: string): string {
  const [year, month] = today.split('-').map(Number) as [number, number];
  // Walk back from the quarter containing today.
  let y = year;
  let q = Math.ceil(month / 3);
  for (let step = 0; step < 8; step++) {
    const end = iso(Date.UTC(y, q * 3, 0));
    if (parse(end) + reportDelayDays(end) * DAY_MS <= parse(today)) return end;
    q -= 1;
    if (q === 0) {
      q = 4;
      y -= 1;
    }
  }
  return iso(Date.UTC(y, q * 3, 0));
}

export interface RestatedEpsFigure {
  restated: number;
  reported: number;
  /** End of the newest period in the figure. */
  periodEnd: string;
}

function latestTtmIndex(history: FinancialHistory, today: string): number | null {
  const series = history.quarterly;
  let index = series.end.length - 1;
  while (index >= 0 && (series.end[index]! > today || reportedEpsAt(series, index) === null)) index--;
  return index < 3 || series.end[index]! < latestDueQuarter(today) || !consecutiveQuarters(series.end, index, 4) ? null : index;
}

/** Reported EPS only, when corporate-action coverage is unavailable; never fill a missing quarter. */
export function reportedTtmEps(history: FinancialHistory, today: string): number | null {
  const index = latestTtmIndex(history, today);
  if (index === null) return null;
  let sum = 0;
  for (let i = index - 3; i <= index; i++) {
    const value = reportedEpsAt(history.quarterly, i);
    if (value === null) return null;
    sum += value;
  }
  return Math.round(sum * 1e4) / 1e4;
}

/** Latest four consecutive quarters, restated and as reported; null when the file is behind the newest due quarter or a quarter lacks a restated EPS. */
export function restatedTtmEps(history: FinancialHistory, today: string): RestatedEpsFigure | null {
  const series = history.quarterly;
  const index = latestTtmIndex(history, today);
  if (index === null) return null;
  const adjustments = epsAdjustments(history, 'quarterly');
  let restated = 0;
  let reported = 0;
  for (let i = index - 3; i <= index; i++) {
    const value = reportedEpsAt(series, i);
    const adjusted = restatedEps(value, adjustments[i]);
    if (value === null || adjusted === null) return null;
    reported += value;
    restated += adjusted;
  }
  return { restated: Math.round(restated * 1e4) / 1e4, reported: Math.round(reported * 1e4) / 1e4, periodEnd: series.end[index]! };
}

/** Annual EPS of `fiscalYear` (December year end), restated and as reported. */
export function restatedAnnualEps(history: FinancialHistory, fiscalYear: number): RestatedEpsFigure | null {
  const series = history.annual;
  const index = series.end.findIndex((end) => Number(end.slice(0, 4)) === fiscalYear && end.slice(5, 7) === '12');
  if (index < 0) return null;
  const reported = reportedEpsAt(series, index);
  const restated = restatedEps(reported, epsAdjustments(history, 'annual')[index]);
  return reported === null || restated === null ? null : { restated, reported, periodEnd: series.end[index]! };
}

const text = (value: number) => value.toFixed(2);

/** The header figures with the P/E on restated EPS where the file says the reported EPS are on an older share basis. */
export function restatePeFigures(figures: PeFigures, history: FinancialHistory | null, price: number | null | undefined, today: string): PeFigures {
  if (!history || history.market !== 'TW') return figures;
  const next: PeFigures = { ...figures };
  const notes: string[] = [];
  const ttm = restatedTtmEps(history, today);
  if (ttm && Math.abs(ttm.restated - ttm.reported) >= 0.005) {
    const result = describePe(price, ttm.restated);
    next.peTtm = result.pe;
    next.peTtmLoss = result.negativeEarnings;
    notes.push(`近四季 EPS 公告 ${text(ttm.reported)} → 還原 ${text(ttm.restated)}`);
  }
  const annual = figures.fiscalYear === null ? null : restatedAnnualEps(history, figures.fiscalYear);
  if (annual && Math.abs(annual.restated - annual.reported) >= 0.005) {
    const result = describePe(price, annual.restated);
    next.pe = result.pe;
    next.peLoss = result.negativeEarnings;
    notes.push(`${figures.fiscalYear} 年 EPS 公告 ${text(annual.reported)} → 還原 ${text(annual.restated)}`);
  }
  if (!notes.length) return figures;
  next.reason = next.peLoss || next.peTtmLoss ? '虧損，本益比不適用' : figures.reason;
  next.epsNote = `本益比以目前股數的${restatedEpsLabel(history)}計算：${notes.join('；')}`;
  return next;
}
