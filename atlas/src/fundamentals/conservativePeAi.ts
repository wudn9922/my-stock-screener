import { z } from 'zod';
import { financialsKey, type FinancialHistory } from './FinancialHistory';
import { assessEligibility, conservativeEps, conservativePePriceIssue } from './conservativePe';
import { reportUrl } from '../report/loadReport';

const date = z.iso.date();
const daysBetween = (later: string, earlier: string) => (Date.parse(later) - Date.parse(earlier)) / 86_400_000;
const https = z.string().max(4096).refine(value => {
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password; } catch { return false; }
});
const baselineSchema = z.object({
  eps: z.number().positive(), basis: z.enum(['ttm', 'annual']), end: date, annualEnd: date,
  currency: z.enum(['USD', 'TWD']), shareBasis: z.enum(['tw-restated', 'us-reported', 'adr']),
  adrRatio: z.number().positive().nullable(),
}).strict().refine(value => (value.shareBasis === 'adr') === (value.adrRatio !== null)
  && (value.shareBasis === 'tw-restated') === (value.currency === 'TWD')
  && daysBetween(value.end, value.annualEnd) >= -7
  && daysBetween(value.end, value.annualEnd) <= (value.basis === 'annual' ? 7 : 395));

export type ScenarioBaseline = z.infer<typeof baselineSchema>;

/** Both the report builder and the card must agree on W6 eligibility, period and EPS basis. */
export function conservativePeScenarioBaseline(history: FinancialHistory): ScenarioBaseline | null {
  const annual = conservativeEps(history, 'annual');
  const eligibility = assessEligibility(annual);
  if (!eligibility.applicable || conservativePePriceIssue(history)) return null;
  const quarters = conservativeEps(history, 'quarterly').slice(-4);
  const ttm = quarters.length === 4 && quarters.every((row, i) => {
    if (row.eps === null || !Number.isFinite(row.eps) || row.period === null) return false;
    if (i === 0) return true;
    const previous = quarters[i - 1]!;
    const days = daysBetween(row.end, previous.end);
    return row.period === previous.period! + 1 && days >= 65 && days <= 115;
  });
  const total = ttm ? quarters.reduce((sum, row) => sum + row.eps!, 0) : 0;
  const year = annual.at(-1)!;
  const period = ttm && total > 0 && quarters.at(-1)!.end >= year.end
    ? { eps: total, end: quarters.at(-1)!.end, basis: 'ttm' as const }
    : { eps: year.eps!, end: year.end, basis: 'annual' as const };
  const baseline = baselineSchema.safeParse({ ...period, annualEnd: history.annual.secEnd?.at(-1) ?? year.end, currency: history.currency,
    shareBasis: history.market === 'TW' ? 'tw-restated' : history.adrRatio ? 'adr' : 'us-reported',
    adrRatio: history.adrRatio?.epsFactor ?? null,
  });
  return baseline.success ? baseline.data : null;
}

export const scenarioSchema = z.object({
  version: z.literal(1),
  symbol: z.string(),
  generatedAt: z.iso.datetime({ offset: true }),
  baseline: baselineSchema,
  scenarioEps: z.number().nonnegative().nullable(),
  affectedEarningsRatio: z.number().min(0).max(1).nullable(),
  concern: z.string().min(1).max(2000),
  evidence: z.string().min(1).max(2000),
  confidence: z.enum(['low', 'medium', 'high']),
  // Legacy provider ids remain readable; new ids are reviewed in the backend config.
  provider: z.string().regex(/^[a-z][a-z0-9-]{0,39}$/),
  model: z.string().min(1),
  news: z.array(z.object({ url: https, date, title: z.string().min(1).max(300) })).min(1).max(20),
  segment: z.object({ url: https, date, periodEnd: date, metric: z.literal('operating-income'),
    excerpt: z.string().min(1).max(2000), maxRatio: z.number().positive().max(1) }).nullable(),
}).strict().refine(value => {
  const generatedDay = new Date(value.generatedAt).toISOString().slice(0, 10);
  if (value.baseline.end > generatedDay || value.baseline.annualEnd > generatedDay
      || value.news.some(item => daysBetween(generatedDay, item.date) > 30 || item.date > generatedDay)) return false;
  if (value.segment && (value.segment.date > generatedDay || value.segment.date < value.segment.periodEnd
      || Math.abs(daysBetween(value.baseline.annualEnd, value.segment.periodEnd)) > 7
      || daysBetween(generatedDay, value.segment.periodEnd) > 540)) return false;
  if (value.scenarioEps === null || value.affectedEarningsRatio === null)
    return value.scenarioEps === null && value.affectedEarningsRatio === null && value.confidence === 'low';
  return value.segment !== null && value.confidence !== 'high' && value.affectedEarningsRatio <= value.segment.maxRatio
    && Math.abs(value.scenarioEps - value.baseline.eps * (1 - value.affectedEarningsRatio)) <= 1e-8;
});

export type Scenario = z.infer<typeof scenarioSchema>;
export function scenarioIsCurrent(scenario: Scenario, now = Date.now()): boolean {
  const age = now - Date.parse(scenario.generatedAt);
  // Backend REFRESH_DAYS = 14, plus 7 days of scheduling grace.
  return age >= 0 && age < 21 * 86400000;
}
export function sameScenarioBaseline(a: ScenarioBaseline, b: ScenarioBaseline): boolean {
  return a.eps === b.eps && a.basis === b.basis && a.end === b.end && a.annualEnd === b.annualEnd && a.currency === b.currency
    && a.shareBasis === b.shareBasis && a.adrRatio === b.adrRatio;
}
export function scenarioPe(price: { value: number; currency: string } | null, scenario: Scenario,
  baseline: ScenarioBaseline, now = Date.now()): number | null {
  return price !== null && Number.isFinite(price.value) && price.value > 0 && price.currency === baseline.currency
    && sameScenarioBaseline(scenario.baseline, baseline) && scenarioIsCurrent(scenario, now)
    && scenario.scenarioEps !== null && scenario.scenarioEps > 0 && Number.isFinite(price.value / scenario.scenarioEps)
    ? price.value / scenario.scenarioEps : null;
}
export async function fetchScenario(symbol: string, signal: AbortSignal): Promise<Scenario | null> {
  const key = financialsKey(symbol);
  if (!key) return null;
  const url = new URL('conservative-pe/' + encodeURIComponent(key) + '.json', reportUrl()).href;
  const response = await fetch(url, { signal, cache: 'no-cache' });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error('Scenario unavailable');
  const value = scenarioSchema.parse(await response.json());
  if (value.symbol !== key) throw new Error('Symbol mismatch');
  return value;
}
