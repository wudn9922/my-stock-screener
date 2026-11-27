import { describe, expect, it } from 'vitest';
import { conservativePeScenarioBaseline, scenarioIsCurrent, scenarioPe, scenarioSchema } from '../src/fundamentals/conservativePeAi';
import { parseFinancials } from '../src/fundamentals/FinancialHistory';
import financials from './fixtures/financials/us-full.json';

const current = Date.parse('2026-11-27T12:00:00Z');
const sample = {
  version: 1, symbol: 'TEST', generatedAt: '2026-11-27T00:00:00Z',
  baseline: { eps: 10, basis: 'annual', end: '2025-12-31', annualEnd: '2025-12-31', currency: 'USD', shareBasis: 'us-reported', adrRatio: null },
  scenarioEps: 7, affectedEarningsRatio: 0.3, concern: '風險', evidence: '假設',
  confidence: 'medium', provider: 'gemini', model: 'gemini-3.8-flash',
  news: [{ url: 'https://news.google.com/rss/articles/example', date: '2026-11-25', title: 'News' }],
  segment: { url: 'https://www.sec.gov/Archives/edgar/test.htm', date: '2026-02-05', periodEnd: '2025-12-31',
    metric: 'operating-income', excerpt: 'Operating income in USD', maxRatio: 0.4 },
};
const price = { value: 70, currency: 'USD' };

describe('scenario PE', () => {
  it('divides a comparable close by the validated scenario EPS', () => {
    const data = scenarioSchema.parse(sample);
    expect(scenarioPe(price, data, data.baseline, current)).toBe(10);
    expect(scenarioPe(null, data, data.baseline, current)).toBeNull();
    expect(scenarioPe({ value: 0, currency: 'USD' }, data, data.baseline, current)).toBeNull();
    expect(scenarioPe({ value: 70, currency: 'TWD' }, data, data.baseline, current)).toBeNull();
    const zero = scenarioSchema.parse({ ...sample, scenarioEps: 0, affectedEarningsRatio: 1, segment: { ...sample.segment, maxRatio: 1 } });
    expect(scenarioPe(price, zero, zero.baseline, current)).toBeNull();
  });

  it('requires auditable operating income and rejects unsupported estimates or unsafe links', () => {
    const invalid = [
      { scenarioEps: 8 }, { confidence: 'high' }, { segment: null },
      { scenarioEps: 5, affectedEarningsRatio: 0.5 },
      { segment: { ...sample.segment, periodEnd: '2024-12-31' } },
      { news: [{ ...sample.news[0], date: '2026-10-01' }] },
      { news: [{ ...sample.news[0], url: 'javascript:alert(1)' }] },
    ];
    for (const patch of invalid) expect(scenarioSchema.safeParse({ ...sample, ...patch }).success).toBe(false);
    const normalized = { ...sample, segment: { ...sample.segment, periodEnd: '2025-12-27' } };
    expect(scenarioSchema.safeParse(normalized).success).toBe(true);
    const narrative = scenarioSchema.parse({ ...sample, segment: null, scenarioEps: null, affectedEarningsRatio: null, confidence: 'low' });
    expect(scenarioPe(price, narrative, narrative.baseline, current)).toBeNull();
  });

  it('withholds valuation for stale reports and changed periods, EPS or shares', () => {
    const data = scenarioSchema.parse(sample);
    const generated = Date.parse(sample.generatedAt);
    expect(scenarioIsCurrent(data, generated - 1)).toBe(false);
    expect(scenarioIsCurrent(data, generated + 7 * 86400000)).toBe(false);
    expect(scenarioIsCurrent(data, generated + 7 * 86400000 - 1)).toBe(true);
    expect(scenarioPe(price, data, data.baseline, generated + 7 * 86400000)).toBeNull();
    for (const patch of [{ eps: 11 }, { end: '2026-01-01' }, { shareBasis: 'adr' as const, adrRatio: 3 }]) {
      expect(scenarioPe(price, data, { ...data.baseline, ...patch }, current)).toBeNull();
    }
  });

  it('shares W6 EPS eligibility and compares genuine trailing quarters with the annual period', () => {
    const parsed = parseFinancials({ ...financials,
      annual: { end: Array.from({ length: 10 }, (_, i) => String(2016 + i) + '-12-31'),
        fy: Array.from({ length: 10 }, (_, i) => 2016 + i), values: { epsDiluted: Array(10).fill(10) } },
      quarterly: { end: ['2025-09-30', '2025-12-31', '2026-03-31', '2026-06-30'], fy: [2025, 2025, 2026, 2026],
        fq: [3, 4, 1, 2], values: { epsDiluted: [2, 3, 4, 5] } },
      quality: { ...financials.quality, issues: [] },
    });
    if (parsed.status !== 'ok') throw new Error(parsed.message);
    const data = parsed.data;
    expect(conservativePeScenarioBaseline(data)).toMatchObject({ basis: 'ttm', eps: 14, end: '2026-06-30', annualEnd: '2025-12-31' });
    expect(conservativePeScenarioBaseline({ ...data, currency: 'TWD' })).toBeNull();
    expect(conservativePeScenarioBaseline({ ...data, quality: { ...data.quality, issues: ['quarterly-ytd-suspected'] } }))
      .toMatchObject({ basis: 'annual', eps: 10 });
    data.quarterly.end[1] = '2025-10-01';
    expect(conservativePeScenarioBaseline(data)).toMatchObject({ basis: 'annual', eps: 10 });
    data.annual.values.epsDiluted[9] = -1;
    expect(conservativePeScenarioBaseline(data)).toBeNull();
  });
});
