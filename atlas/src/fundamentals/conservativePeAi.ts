import { z } from 'zod';
import { financialsKey } from './FinancialHistory';
import { reportUrl } from '../report/loadReport';

const date = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const https = z.string().max(4096).refine(value => {
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password; } catch { return false; }
});
export const scenarioSchema = z.object({
  version: z.literal(1),
  symbol: z.string(),
  generatedAt: z.string().refine(value => Number.isFinite(Date.parse(value))),
  baseline: z.object({ eps: z.number().positive(), basis: z.enum(['ttm', 'annual']), end: date }),
  scenarioEps: z.number().nonnegative(),
  affectedEarningsRatio: z.number().min(0).max(1),
  concern: z.string().min(1).max(2000),
  evidence: z.string().min(1).max(2000),
  confidence: z.enum(['low', 'medium', 'high']),
  provider: z.enum(['gemini', 'groq']),
  model: z.string().min(1),
  news: z.array(z.object({ url: https, date, title: z.string().min(1).max(300) })).min(1).max(20),
  segment: z.object({ url: https, date, excerpt: z.string().min(1).max(15000) }).nullable(),
}).strict().refine(value => Math.abs(value.scenarioEps - value.baseline.eps * (1 - value.affectedEarningsRatio)) <= 1e-8
  && (value.segment !== null || value.confidence === 'low'));

export type Scenario = z.infer<typeof scenarioSchema>;
export function scenarioPe(price: number | null, scenario: Scenario): number | null {
  return price !== null && Number.isFinite(price) && price > 0 && scenario.scenarioEps > 0 && Number.isFinite(price / scenario.scenarioEps)
    ? price / scenario.scenarioEps : null;
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
