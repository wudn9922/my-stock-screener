import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { publishMarkets, validateMarket, type RankedEntry } from '../scripts/build-pe-river.ts';
import type { PeRiverFile } from '../src/fundamentals/peRiverRank';

const dates = { TW: '2026-11-28', US: '2026-11-28' };
const us: RankedEntry[] = [['AAPL', [0, 0]], ['MSFT', [100, 1]]];
const fixture = async (): Promise<PeRiverFile> => JSON.parse(await readFile(new URL('./fixtures/pe-river-output-gate.json', import.meta.url), 'utf8'));

describe('P/E river output quality gate', () => {
  it('rejects duplicate, missing anchor, nonfinite and out-of-range percentiles; permits endpoints and cold start', async () => {
    const prior = await fixture();
    const entries = Object.entries(prior.items);
    expect(() => validateMarket('TW', [...entries, entries[0]!], prior, dates.TW)).toThrow(/duplicate/);
    expect(() => validateMarket('TW', entries.slice(1), prior, dates.TW)).toThrow(/anchors/);
    for (const value of [NaN, Infinity, -0.1, 100.1]) {
      expect(() => validateMarket('TW', [['2330.TW', [value, 0]]], null, dates.TW)).toThrow(/percentile/);
    }
    expect(validateMarket('US', us, null, dates.US)).toEqual(Object.fromEntries(us));
    expect(() => validateMarket('TW', [], null, dates.TW)).toThrow(/0 usable/);
  });

  it('retains rejected market bytes, publishes other market, permits exactly 85% and ordinary missing stocks', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'pe-river-gate-'));
    try {
      const tw = join(directory, 'pe-river-tw.json');
      const u = join(directory, 'pe-river-us.json');
      const prior = await fixture();
      const raw = JSON.stringify(prior, null, 2) + '\n';
      const entries = Object.entries(prior.items);
      await writeFile(tw, raw);
      const previousUs = { ...prior, market: 'US', items: Object.fromEntries(us) };
      await writeFile(u, JSON.stringify(previousUs));
      for (const bad of [[], entries.slice(0, 16), entries.slice(1),
        [...entries.slice(0, 16), ['9999.TW', [20, 0]] as RankedEntry]]) {
        expect(await publishMarkets(directory, { TW: bad, US: us }, dates, 0, 20)).toBe(1);
        expect(await readFile(tw, 'utf8')).toBe(raw);
        expect(JSON.parse(await readFile(u, 'utf8')).priceThrough).toBe(dates.US);
      }
      const beforeUs = await readFile(u, 'utf8');
      expect(await publishMarkets(directory, { TW: entries, US: us }, dates, 11, 20)).toBe(1);
      expect(await readFile(tw, 'utf8')).toBe(raw);
      expect(await readFile(u, 'utf8')).toBe(beforeUs);
      expect(await publishMarkets(directory, { TW: entries.slice(0, 17), US: us }, dates, 10, 20)).toBe(0);
      expect(Object.keys(JSON.parse(await readFile(tw, 'utf8')).items)).toHaveLength(17);
      const acceptedUs = await readFile(u, 'utf8');
      expect(await publishMarkets(directory, { TW: entries, US: [] }, dates, 0, 20)).toBe(1);
      expect(await readFile(u, 'utf8')).toBe(acceptedUs);
      expect(Object.keys(JSON.parse(await readFile(tw, 'utf8')).items)).toHaveLength(20);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
});
