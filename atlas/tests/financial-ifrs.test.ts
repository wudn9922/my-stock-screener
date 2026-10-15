import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { parseFinancials } from '../src/fundamentals/FinancialHistory';
import { issueText, provenanceText, unitText } from '../src/ui/financials/financialsView';

// ifrs-twd.json is a SYNTHETIC document written by atlas/scripts/sec_ifrs.py from made-up companyfacts
// (an IFRS 20-F filer reporting in TWD, see tests/test_sec_ifrs.py); it is not real financial data.
const fixture = (name: string) =>
  JSON.parse(readFileSync(resolve(import.meta.dirname, 'fixtures/financials', name), 'utf8')) as Record<string, unknown>;

describe('IFRS foreign-filer history (atlas/scripts/sec_ifrs.py)', () => {
  it('parses as a foreign reporter in its reporting currency, with SEC periods', () => {
    const result = parseFinancials(fixture('ifrs-twd.json'));
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    const history = result.data;
    expect(history.currency).toBe('TWD');
    expect(history.foreignReporter).toBe(true);
    expect(history.source).toBe('SEC EDGAR companyfacts (IFRS)');
    expect(history.quality.issues).toEqual(expect.arrayContaining(['foreign-currency', 'ifrs', 'ifrs-fallback']));
    expect(history.annual.end).toEqual(['2020-12-31', '2021-12-31', '2022-12-31', '2023-12-31', '2024-12-31']);
    expect(history.annual.src.every((source) => source === 'sec')).toBe(true);
    expect(history.quarterly.end).toEqual(['2025-03-31', '2025-06-30']);
    expect(history.quarterly.fq).toEqual([1, 2]);
    expect(history.annual.values.netIncome?.[4]).toBe(10_500_000_000);
    expect(history.annual.values.cash?.every((value) => value === null)).toBe(true);
  });

  it('explains the IFRS quality codes instead of the generic fallback', () => {
    expect(issueText('ifrs')).toContain('IFRS');
    expect(issueText('ifrs-fallback')).toContain('非控制權益');
    expect(issueText('ifrs')).not.toContain('其他資料提示');
    expect(issueText('ifrs-fallback')).not.toContain('其他資料提示');
  });

  it('explains the US GAAP foreign-filer codes the same way and parses such a document', () => {
    expect(issueText('foreign-gaap')).toContain('US GAAP');
    expect(issueText('foreign-gaap-fallback')).toContain('非控制權益');
    expect(issueText('foreign-gaap')).not.toContain('其他資料提示');
    expect(issueText('foreign-gaap-fallback')).not.toContain('其他資料提示');
    const base = fixture('ifrs-twd.json');
    const quality = {
      ...(base.quality as Record<string, unknown>),
      issues: ['foreign-currency', 'foreign-gaap', 'foreign-gaap-fallback'],
    };
    const result = parseFinancials({ ...base, source: 'SEC EDGAR companyfacts (US GAAP, foreign filer)', quality });
    if (result.status !== 'ok') throw new Error(result.status);
    expect(result.data.foreignReporter).toBe(true);
    expect(result.data.quality.issues).toEqual(expect.arrayContaining(['foreign-gaap', 'foreign-gaap-fallback']));
  });

  it('labels the sources as SEC and the EPS unit with the reporting currency', () => {
    const result = parseFinancials(fixture('ifrs-twd.json'));
    if (result.status !== 'ok') throw new Error(result.status);
    expect(provenanceText(result.data, 'annual')).toContain('SEC EDGAR');
    expect(provenanceText(result.data, 'annual')).not.toContain('Yahoo');
    expect(provenanceText(result.data, 'quarterly')).toContain('SEC EDGAR');
    expect(unitText('eps', 'TWD')).toBe('元');
  });
});
