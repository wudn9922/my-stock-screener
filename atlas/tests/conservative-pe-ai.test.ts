import { describe, expect, it } from 'vitest';
import { scenarioPe, scenarioSchema } from '../src/fundamentals/conservativePeAi';

const sample = {
  version: 1, symbol: 'TEST', generatedAt: '2026-11-27T00:00:00Z',
  baseline: { eps: 10, basis: 'annual', end: '2025-12-31' },
  scenarioEps: 7, affectedEarningsRatio: 0.3, concern: '風險', evidence: '假設',
  confidence: 'low', provider: 'gemini', model: 'test',
  news: [{ url: 'https://example.com/a', date: '2026-11-25', title: 'News' }], segment: null,
};
describe('scenario PE', () => {
  it('calculates PE from the actual comparison price and validated EPS', () => {
    expect(scenarioPe(70, scenarioSchema.parse(sample))).toBe(10);
    expect(scenarioPe(null, scenarioSchema.parse(sample))).toBeNull();
    expect(scenarioPe(70, scenarioSchema.parse({ ...sample, scenarioEps: 0, affectedEarningsRatio: 1 }))).toBeNull();
  });
  it('rejects inconsistent calculations, unsupported confidence and unsafe links', () => {
    expect(scenarioSchema.safeParse({ ...sample, scenarioEps: 8 }).success).toBe(false);
    expect(scenarioSchema.safeParse({ ...sample, confidence: 'high' }).success).toBe(false);
    expect(scenarioSchema.safeParse({ ...sample, news: [{ ...sample.news[0], url: 'javascript:alert(1)' }] }).success).toBe(false);
  });
});
