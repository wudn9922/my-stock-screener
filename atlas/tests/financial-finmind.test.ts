import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { hasSecHistory, parseFinancials, provenance, type Revision } from '../src/fundamentals/FinancialHistory';
import { depthText, issueText, provenanceText, revisionText } from '../src/ui/financials/financialsView';

// Synthetic FinMind statements file (made-up numbers in the builder's format): tests/fixtures/financials/tw-finmind.json,
// written by atlas/scripts/build_financials_tw_finmind.py from synthetic FinMind rows.
const fixture = (name: string) =>
  JSON.parse(readFileSync(resolve(import.meta.dirname, 'fixtures/financials', name), 'utf8')) as Record<string, unknown>;

const ok = (data: unknown) => {
  const result = parseFinancials(data);
  if (result.status !== 'ok') throw new Error(`expected ok, got ${result.status}`);
  return result.data;
};

describe('FinMind statements (Taiwan, official source "finmind")', () => {
  it('keeps finmind as its own period source and not as Yahoo', () => {
    const data = ok(fixture('tw-finmind.json'));
    expect(data.market).toBe('TW');
    expect(data.source).toBe('FinMind（公開資訊觀測站）');
    expect(new Set(data.quarterly.src)).toEqual(new Set(['finmind']));
    expect(new Set(data.annual.src)).toEqual(new Set(['finmind']));
    expect(data.quarterly.end.length).toBe(46);
    expect(data.quality.issues).toEqual(expect.arrayContaining(['q4-derived', 'eps-annual-summed', 'debt-approx']));
    expect(provenance(data.annual)).toEqual([expect.objectContaining({ source: 'finmind', count: 11 })]);
    expect(hasSecHistory(data)).toBe(false);
  });

  it('reads a mixed file (FinMind periods with Yahoo periods); an unknown source is read as Yahoo, as before', () => {
    const raw = fixture('tw-finmind.json');
    const quarterly = raw.quarterly as { src: string[]; end: string[] };
    const mixed = {
      ...raw,
      source: 'FinMind（公開資訊觀測站）+ Yahoo Finance',
      quarterly: { ...quarterly, src: quarterly.src.map((src, index) => (index === quarterly.src.length - 1 ? 'yahoo' : 'other')) },
    };
    const data = ok(mixed);
    expect(data.quarterly.src.at(-1)).toBe('yahoo');
    expect(data.quarterly.src[0]).toBe('yahoo');
    expect(provenance(data.quarterly).map((span) => span.source)).toEqual(['yahoo']);
  });
});

describe('FinMind statements: texts', () => {
  it('names the FinMind source in the provenance line and the depth line', () => {
    const data = ok(fixture('tw-finmind.json'));
    expect(provenanceText(data, 'annual')).toBe('資料來源：公開資訊觀測站（FinMind）');
    expect(depthText(data, 'annual')).toBe('含 FinMind（公開資訊觀測站）申報歷史，共約 11 年（FY2015 起）；之後每季自動累積。');
    expect(depthText(data, 'quarterly')).toMatch(/^目前 46 季/);
  });

  it('explains the FinMind-specific quality codes', () => {
    expect(issueText('eps-annual-summed')).toContain('四季 EPS 加總');
    expect(issueText('debt-approx')).toContain('FinMind');
    expect(issueText('debt-missing')).toContain('不計算');
    expect(issueText('q4-derived')).toContain('全年減前三季');
    expect(issueText('yahoo-differs')).toContain('官方申報');
  });

  it('says which official source kept the value in a revision', () => {
    const data = ok(fixture('tw-finmind.json'));
    const revision: Revision = {
      kind: 'yahoo-differs', period: 'annual', end: '2024-12-31', field: 'revenue', old: 1e11, new: 1.1e11, seen: null,
    };
    expect(revisionText(data, revision)).toContain('（保留 FinMind 值，Yahoo 不同）');
  });
});
