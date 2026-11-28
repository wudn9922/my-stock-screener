/** Build-time Taiwan EPS enrichment. Financial history is optional, never a market-wide dependency. */
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { parseFinancials, RAW_FIELDS, type FinancialHistory, type FinancialSeries } from '../src/fundamentals/FinancialHistory';
import { latestDueQuarter, reportedTtmEps, restatedAnnualEps, restatedTtmEps, type RestatedEpsFigure } from '../src/fundamentals/restatedPe';
import { restatedEpsLabel } from '../src/fundamentals/epsBasis';
import type { ValuationRecord } from './valuation';

export function taipeiBuildDate(ms: number): string {
  return new Date(ms + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
}

/** Ignore future periods, known future filing dates, and not-yet-due periods without filing evidence. */
function availableSeries(series: FinancialSeries, today: string): FinancialSeries {
  const due = latestDueQuarter(today);
  const indices = series.end.flatMap((end, i) => {
    const quality = series.epsQuality?.[i];
    const dates = [quality?.epsBasicFiled, quality?.epsDilutedFiled].filter((date): date is string => !!date);
    return end <= today && dates.every(date => date <= today) && (end <= due || dates.length > 0) ? [i] : [];
  });
  const select = <T>(values: T[]): T[] => indices.map(i => values[i]!);
  return {
    end: select(series.end), fy: select(series.fy), fq: select(series.fq), src: select(series.src),
    values: Object.fromEntries(RAW_FIELDS.map(field => [field, select(series.values[field])])) as FinancialSeries['values'],
    ...(series.secEnd ? { secEnd: select(series.secEnd) } : {}),
    ...(series.fieldSrc ? { fieldSrc: select(series.fieldSrc) } : {}),
    ...(series.epsQuality ? { epsQuality: select(series.epsQuality) } : {}),
  };
}

function availableHistory(history: FinancialHistory, today: string): FinancialHistory {
  return {
    ...history,
    annual: availableSeries(history.annual, today),
    quarterly: availableSeries(history.quarterly, today),
    stockDividends: history.stockDividends?.filter(event => event.exDate <= today) ?? null,
    capitalReductions: history.capitalReductions?.filter(event => event.date <= today) ?? null,
    twSplits: history.twSplits?.filter(event => event.date <= today) ?? null,
    split: { ...history.split, events: history.split.events.filter(event => event.date <= today) },
  };
}

export interface TaiwanEpsMismatch {
  symbol: string;
  period: 'ttm' | 'annual';
  market: number;
  reported: number;
  restated: number;
}

type MismatchReporter = (mismatch: TaiwanEpsMismatch) => void;

/** Market EPS can already be on the current share basis (especially Yahoo annual EPS).
 * Allow max(NT$0.05, 3% of |market EPS|) for exchange P/E rounding and its implied EPS.
 * Prefer the reported match, then the restated match; never override an unexplained discrepancy.
 */
function marketBasis(market: number | null, figure: RestatedEpsFigure, symbol: string,
  period: TaiwanEpsMismatch['period'], onMismatch?: MismatchReporter): 'reported' | 'restated' | 'unknown' {
  if (market === null || !Number.isFinite(market)) return 'unknown';
  const tolerance = Math.max(0.05, 0.03 * Math.abs(market));
  if (Math.abs(figure.reported - market) <= tolerance) return 'reported';
  if (Math.abs(figure.restated - market) <= tolerance) return 'restated';
  onMismatch?.({ symbol, period, market, reported: figure.reported, restated: figure.restated });
  return 'unknown';
}

export function applyTaiwanFinancials(record: ValuationRecord, history: FinancialHistory, today: string,
  onMismatch?: MismatchReporter): ValuationRecord {
  const next = { ...record };
  const available = availableHistory(history, today);
  const ttm = restatedTtmEps(available, today);
  const annual = record.fiscalYear === null ? null : restatedAnnualEps(available, record.fiscalYear);
  const notes: string[] = [];
  const adjustedNotes: string[] = [];
  const exchangeMissing = record.exchangePeTtm == null || record.exchangePeTtm <= 0;
  const ttmBasis = ttm && !exchangeMissing ? marketBasis(record.epsTtm, ttm, history.symbol, 'ttm', onMismatch) : null;
  if (ttm && (exchangeMissing || (ttmBasis === 'reported' && Math.abs(ttm.restated - ttm.reported) >= 0.005))) {
    next.epsTtmCalculated = ttm.restated;
    next.epsTtmCalculation = 'restated';
    if (exchangeMissing) notes.push('近四季 EPS 由財報計算');
    if (Math.abs(ttm.restated - ttm.reported) >= 0.005) adjustedNotes.push(`近四季 EPS 公告 ${ttm.reported.toFixed(2)} → 還原 ${ttm.restated.toFixed(2)}`);
  } else if (!ttm && exchangeMissing && available.stockDividends === null
      && !available.split.events.length && !available.twSplits?.length && !available.capitalReductions?.length) {
    // Four reported quarters still provide evidence of earnings; explicitly disclose that share-basis coverage is unavailable.
    const reported = reportedTtmEps(available, today);
    if (reported !== null) {
      next.epsTtmCalculated = reported;
      next.epsTtmCalculation = 'reported';
      notes.push('近四季 EPS 由財報公告值計算（配股資料不足，未還原）');
    }
  }
  const annualBasis = annual ? marketBasis(record.epsAnnual, annual, history.symbol, 'annual', onMismatch) : null;
  if (annual && annualBasis === 'reported' && Math.abs(annual.restated - annual.reported) >= 0.005) {
    next.epsAnnualRestated = annual.restated;
    adjustedNotes.push(`${record.fiscalYear} 年 EPS 公告 ${annual.reported.toFixed(2)} → 還原 ${annual.restated.toFixed(2)}`);
  }
  if (adjustedNotes.length) notes.push(`本益比以目前股數的${restatedEpsLabel(available)}計算：${adjustedNotes.join('；')}`);
  if (notes.length) next.epsNote = notes.join('；');
  return next;
}

export async function enrichTaiwanValuation(items: Record<string, ValuationRecord>, directory: string, today: string,
  onMismatch?: MismatchReporter): Promise<Record<string, ValuationRecord>> {
  const output: Record<string, ValuationRecord> = {};
  for (const [symbol, record] of Object.entries(items)) {
    output[symbol] = record;
    try {
      const raw: unknown = JSON.parse(await readFile(join(directory, `${symbol}.json`), 'utf8'));
      const parsed = parseFinancials(raw);
      if (parsed.status === 'ok' && parsed.data.market === 'TW' && parsed.data.symbol === symbol)
        output[symbol] = applyTaiwanFinancials(record, parsed.data, today, onMismatch);
    } catch {
      // Missing or malformed one-symbol input must not prevent publishing other stocks.
    }
  }
  return output;
}
