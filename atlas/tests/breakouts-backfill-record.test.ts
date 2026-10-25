import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { OUTCOME_KEYS, parseBreakouts } from '../src/report/breakouts';

// These are real archived-data calculations, never synthetic browser prices.
const bytes = readFileSync(resolve(import.meta.dirname, '../../docs/report/breakouts-backfill.json'));
const proof = JSON.parse(readFileSync(resolve(import.meta.dirname, '../../docs/report/breakouts-backfill-proof.json'), 'utf8'));
const raw = JSON.parse(bytes.toString('utf8'));

describe('shipped real monthly calculation', () => {
  it('matches its source proof and contains only monthly cohorts', () => {
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(proof.reportSha256);
    expect(proof.archiveGitCommit).toMatch(/^[a-f0-9]{40}$/);
    expect(proof.inputChecksumsSha256).toMatch(/^[a-f0-9]{64}$/);
    expect(proof.inputCacheFileCount).toBe(7328);
    expect(raw.generatedAt).toBe(proof.reportGeneratedAt);
    expect(raw.params.windows.map((window: { key: string }) => window.key)).toEqual(['1m']);
    expect(proof.referenceDatePolicy).toContain('never use prices after that reference date');
  });

  it.each(['tw', 'us'] as const)('%s has six prior valid monthly results plus the reference date', key => {
    const parsed = parseBreakouts(raw);
    expect(parsed.status).toBe('ok');
    if (parsed.status !== 'ok') return;
    const market = parsed.data.markets[key]!;
    expect(market.scope).toBe('full');
    expect(market.asOf).toBe(proof.marketReferenceCutoffs[key]);
    expect(Object.keys(market.windows)).toEqual(['1m']);
    const history = market.monthly!;
    expect(history.referenceDate).toBe(market.asOf);
    expect(history.points).toHaveLength(7);
    expect(history.points.filter(point => point.targetDate < history.referenceDate)).toHaveLength(6);
    for (const point of history.points) {
      expect(point.status).toBe('ok');
      expect(point.asOf! <= point.targetDate && point.targetDate <= history.referenceDate).toBe(true);
      expect(point.evaluated / point.universeCount).toBeGreaterThanOrEqual(0.9);
      for (const method of ['swingHigh', 'box20', 'ema20']) {
        const comparison = point.strategies[method]!.comparison!;
        expect(comparison.completeN).toBeGreaterThan(0);
        for (const rule of OUTCOME_KEYS) {
          expect(comparison.rules[rule]!.winRate).not.toBeNull();
          expect(comparison.rules[rule]!.evaluatedN).toBe(comparison.completeN);
        }
      }
    }
  });
});
