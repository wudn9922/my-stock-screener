import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { GrowthProvider, growthKey, mergeGrowth, parseGrowthFile, parseGrowthRecord, parseReportGrowth } from '../src/fundamentals/GrowthProvider';

const fixtureText = readFileSync('tests/fixtures/screener/us-growth.json', 'utf8');

function response(body: string, status = 200) {
  return new Response(body, { status, headers: { 'content-type': 'application/json' } });
}

describe('us-growth.json', () => {
  it('parses the contract fixture', () => {
    const file = parseGrowthFile(JSON.parse(fixtureText))!;
    expect(file.source).toBe('Yahoo Finance');
    expect(file.generatedAt).toBe('2026-10-09T03:10:00+00:00');
    expect(file.items.size).toBeGreaterThan(100);
    const intel = file.items.get('INTC')!;
    expect(intel).toMatchObject({ epsTurn: true, basis: 'diluted', latest: '2026-06-30', checkedAt: '2026-10-08' });
    expect(intel.epsYoY).toEqual([null, null, null, null]);
    expect(file.items.get('BRK-B')).toBeDefined();
    expect(file.items.has('RIVN')).toBe(false);
  });

  it('is tolerant per record and strict about the document', () => {
    expect(parseGrowthRecord({ epsYoY: [12, 'x', '3.5', null, 9, 9], revYoY: 'bad', epsTurn: 'yes', basis: 'basic', latest: 'soon' })).toEqual({
      epsYoY: [12, null, 3.5, null, 9, 9], // up to the 12-quarter horizon of growth.json (the Yahoo file has four)
      revYoY: [],
      epsTurn: false,
      epsTurnQ: [false],
      epsYoYAnnual: [],
      revYoYAnnual: [],
      epsTurnAnnual: [],
      basis: 'basic',
      latest: null,
      latestAnnual: null,
      checkedAt: null,
      source: 'yahoo',
      originQ: { eps: 'us-growth', rev: 'us-growth' },
      originY: null,
      derivedQ: [],
      excludedEpsQ: 0,
      quality: null,
    });
    expect(parseGrowthRecord([1])).toBeNull();
    expect(parseGrowthFile({ version: 2, market: 'US', items: {} })).toBeNull();
    expect(parseGrowthFile({ version: 1, market: 'TW', items: {} })).toBeNull();
    expect(parseGrowthFile({ version: 1, market: 'US', items: [] })).toBeNull();
    const file = parseGrowthFile({ version: 1, market: 'US', items: { 'brk.b': { epsYoY: [1] }, BAD: 7 } })!;
    expect([...file.items.keys()]).toEqual(['BRK-B']);
    expect(growthKey(' brk.b ')).toBe('BRK-B');
  });

  it('loads once, reloads after 30 minutes and keeps the last good copy on failure', async () => {
    let now = 0;
    let fail = false;
    const fetcher = vi.fn(async () => (fail ? response('down', 503) : response(fixtureText)));
    const provider = new GrowthProvider('/atlas/valuation', { fetcher, now: () => now });
    expect(provider.url).toBe('/atlas/valuation/us-growth.json');
    const first = await provider.load();
    expect(first?.items.size).toBeGreaterThan(100);
    await provider.load();
    expect(fetcher).toHaveBeenCalledTimes(1);
    now = 31 * 60_000;
    fail = true;
    expect(await provider.load()).toBe(first);
    expect(fetcher).toHaveBeenCalledTimes(2);
    // A failure is retried after a minute, not on every call.
    now += 30_000;
    await provider.load();
    expect(fetcher).toHaveBeenCalledTimes(2);
    now += 31_000;
    fail = false;
    await provider.load();
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it('resolves null when the file is missing or invalid', async () => {
    expect(await new GrowthProvider('/', { fetcher: vi.fn(async () => response('nope', 404)) }).load()).toBeNull();
    expect(await new GrowthProvider('/', { fetcher: vi.fn(async () => response('<html>')) }).load()).toBeNull();
    expect(await new GrowthProvider('/', { fetcher: vi.fn(async () => { throw new TypeError('offline'); }) }).load()).toBeNull();
  });
});

describe('quarterly EPS quality exclusions', () => {
  const fields = ['symbol', 'qEnd', 'epsQ', 'revQ', 'src', 'gQ'];
  const row = ['MSFT', '2026-06-30', [25, null, null], [5], 'sec', 0b110];

  it('reads gQ by field name and defaults pre-gQ rows and files to zero', () => {
    const read = (columns: string[], values: unknown[]) =>
      parseReportGrowth({ version: 1, fields: columns, rows: [values] })!.items.get('MSFT')!;
    expect(read(fields, row).excludedEpsQ).toBe(0b110);
    expect(read(fields, row.slice(0, -1)).excludedEpsQ).toBe(0);
    expect(read(fields.slice(0, -1), row).excludedEpsQ).toBe(0);
  });

  it('keeps the mask when only revenue is replaced and clears it when EPS comes from Yahoo', () => {
    const report = parseReportGrowth({ version: 1, fields, rows: [row] });
    const yahoo = (epsYoY: number[]) => parseGrowthFile({
      version: 1, market: 'US', items: { MSFT: { latest: '2026-09-30', epsYoY, revYoY: [10] } },
    });
    expect(mergeGrowth(yahoo([]), report)?.items.get('MSFT')?.excludedEpsQ).toBe(0b110);
    expect(mergeGrowth(yahoo([15]), report)?.items.get('MSFT')?.excludedEpsQ).toBe(0);
  });
});
