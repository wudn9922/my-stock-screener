import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { parseFinancials, type FinancialHistory } from '../src/fundamentals/FinancialHistory';
import { adrEpsNote, issueText } from '../src/ui/financials/financialsView';

// adr-rescaled.json: SYNTHETIC file of the same shape (made-up numbers), also used by the e2e page test.
const adrFixture = () =>
  JSON.parse(readFileSync(resolve(import.meta.dirname, 'fixtures/financials/adr-rescaled.json'), 'utf8')) as unknown;

// SYNTHETIC document in the shape atlas/scripts/build_financials.py writes for a foreign reporter whose SEC
// per-share EPS were put on the ADR basis (adrRatio epsFactor 5). Made-up numbers; see tests/test_financials.py.
const adrDocument = (adrRatio?: unknown) => ({
  version: 1,
  symbol: 'TSM',
  market: 'US',
  currency: 'TWD',
  foreignReporter: true,
  generatedAt: '2026-10-11T09:00:00Z',
  checkedAt: '2026-10-11',
  source: 'SEC EDGAR companyfacts + Yahoo Finance',
  fyEndMonth: 12,
  annual: {
    end: ['2023-12-31', '2024-12-31'],
    fy: [2023, 2024],
    src: ['sec', 'yahoo'],
    values: { revenue: [2162000000000, 2894000000000], epsDiluted: [210.5, 226.25] },
  },
  quarterly: { end: [], fy: [], fq: [], values: {} },
  split: { adjusted: false, events: [] },
  quality: { level: 'partial', annualYears: 2, quarters: 0, issues: ['adr-eps-rescaled', 'foreign-currency'] },
  ...(adrRatio === undefined ? {} : { adrRatio }),
});

const parsed = (adrRatio?: unknown): FinancialHistory => {
  const result = parseFinancials(adrDocument(adrRatio));
  if (result.status !== 'ok') throw new Error(`expected ok, got ${result.status}`);
  return result.data;
};

describe('adrRatio of a foreign reporter file', () => {
  it('parses the factor with its derivation day and pair count', () => {
    const history = parsed({ epsFactor: 5, basis: 'ADR', derivedAt: '2026-10-11', pairs: 14 });
    expect(history.adrRatio).toEqual({ epsFactor: 5, derivedAt: '2026-10-11', pairs: 14 });
  });

  it('is null when the file has none, or when the factor is not a positive number', () => {
    expect(parsed().adrRatio).toBeNull();
    expect(parsed({ epsFactor: 0, basis: 'ADR' }).adrRatio).toBeNull();
    expect(parsed({ epsFactor: -5 }).adrRatio).toBeNull();
    expect(parsed({ epsFactor: 'five' }).adrRatio).toBeNull();
    expect(parsed({}).adrRatio).toBeNull();
    expect(parsed('5').adrRatio).toBeNull();
  });

  it('keeps a factor written as a string number and drops a bad day and a negative count', () => {
    const history = parsed({ epsFactor: '5', derivedAt: 'yesterday', pairs: -3 });
    expect(history.adrRatio).toEqual({ epsFactor: 5, derivedAt: null, pairs: 0 });
  });
});

describe('basis note of the financials page', () => {
  it('says the EPS are per ADR with the number of ordinary shares per ADR', () => {
    expect(adrEpsNote(parsed({ epsFactor: 5 }))).toBe('EPS 以每單位 ADR 計（1 ADR ＝ 5 股普通股，由 SEC 每股數字換算）');
  });

  it('shows a non-integer factor to two decimals', () => {
    expect(adrEpsNote(parsed({ epsFactor: 2.5 }))).toBe('EPS 以每單位 ADR 計（1 ADR ＝ 2.50 股普通股，由 SEC 每股數字換算）');
    expect(adrEpsNote(parsed({ epsFactor: 0.2 }))).toContain('1 ADR ＝ 0.20 股');
  });

  it('is absent when the factor is 1 (the EPS already are per ADR) or the file has no adrRatio', () => {
    expect(adrEpsNote(parsed({ epsFactor: 1 }))).toBeNull();
    expect(adrEpsNote(parsed())).toBeNull();
  });

  it('reads the ADR-basis fixture: factor 5, EPS per ADR, the note and the quality code', () => {
    const result = parseFinancials(adrFixture());
    if (result.status !== 'ok') throw new Error(`expected ok, got ${result.status}`);
    expect(result.data.adrRatio?.epsFactor).toBe(5);
    expect(result.data.annual.values.epsDiluted?.[4]).toBe(62);
    expect(result.data.quality.issues).toContain('adr-eps-rescaled');
    expect(adrEpsNote(result.data)).toBe('EPS 以每單位 ADR 計（1 ADR ＝ 5 股普通股，由 SEC 每股數字換算）');
  });

  it('explains the rescale quality code instead of the generic fallback', () => {
    expect(issueText('adr-eps-rescaled')).toContain('ADR');
    expect(issueText('adr-eps-rescaled')).not.toContain('其他資料提示');
  });
});
