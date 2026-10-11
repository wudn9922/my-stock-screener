import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { GrowthProvider, growthKey, parseGrowthFile, parseGrowthRecord } from '../src/fundamentals/GrowthProvider';

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
      epsYoY: [12, null, 3.5, null],
      revYoY: [],
      epsTurn: false,
      basis: 'basic',
      latest: null,
      checkedAt: null,
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
