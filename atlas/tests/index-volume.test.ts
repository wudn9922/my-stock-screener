import { describe, expect, it } from 'vitest';
import type { Bar, BarResult } from '../src/market-data/MarketDataProvider';
import { getMarketProfile } from '../src/market-data/MarketProfile';
import { hasIndexVolume, INDEX_VOLUME_PROXIES, indexVolumeFeatures, withProxyVolume } from '../src/report/indexVolume';
import { volumeStructure } from '../src/report/research';

const epoch = (value: string) => Date.parse(value) / 1000;
function bars(values: [number, number][], start = '2026-09-01T01:00:00Z'): Bar[] {
  let time = epoch(start);
  return values.map(([close, volume]) => {
    while ([0, 6].includes(new Date(time * 1000).getUTCDay())) time += 86400;
    const bar = { time, open: close, high: close + 1, low: close - 1, close, volume };
    time += 86400;
    return bar;
  });
}
function result(rows: Bar[], symbol = '^TWII', asOf = epoch('2026-10-25T12:00:00Z')): BarResult {
  return { bars: rows, market: getMarketProfile(symbol), source: 'Test daily series', session: 'regular', delayed: true, adjusted: false, asOf };
}

describe('index directional volume', () => {
  it('counts all four directions against the immediately previous volume, including equal-volume denominators', () => {
    const features = indexVolumeFeatures(result(bars([[100, 100], [110, 200], [120, 100], [100, 50], [90, 100], [100, 100]])), '^TWII');
    expect(volumeStructure(features, 20)).toEqual({
      observations: 5,
      upShrink: { numerator: 1, denominator: 3, pct: expect.closeTo(100 / 3, 10) },
      upExpand: { numerator: 1, denominator: 3, pct: expect.closeTo(100 / 3, 10) },
      downExpand: { numerator: 1, denominator: 2, pct: 50 },
      downShrink: { numerator: 1, denominator: 2, pct: 50 },
    });
    expect(features.every(feature => !('rvol' in feature))).toBe(true);
  });

  it('never bridges a zero-volume session to find an older comparison', () => {
    const features = indexVolumeFeatures(result(bars([[100, 100], [101, 0], [102, 150], [101, 100]])), '^TWII');
    expect(features.map(feature => feature.valid)).toEqual([true, false, true, true]);
    expect(features.slice(0, 3).every(feature => feature.returnPct === null)).toBe(true);
    expect(volumeStructure(features, 20)).toMatchObject({ observations: 1, upExpand: { pct: null }, downShrink: { pct: 100 } });
  });

  it('excludes invalid OHLC, non-finite volume and unverified large jumps with their adjacent comparison', () => {
    for (const invalid of [{ low: 200 }, { volume: Infinity }, { close: 200, high: 201, low: 199, open: 200 }]) {
      const rows = bars([[100, 100], [101, 120], [102, 130], [101, 100]]);
      rows[1] = { ...rows[1], ...invalid };
      const features = indexVolumeFeatures(result(rows), '^TWII');
      expect(features[1]!.valid).toBe(false);
      expect(features[1]!.returnPct).toBeNull();
      expect(features[2]!.returnPct).toBeNull();
    }
  });

  it('shows N/A for absent directional observations, without manufacturing zero percent', () => {
    for (const values of [[], [[100, 100]], [[100, 0], [101, 0]], [[100, 100], [100, 200]]] as [number, number][][]) {
      const value = volumeStructure(indexVolumeFeatures(result(bars(values)), '^TWII'), 20);
      for (const ratio of [value.upShrink, value.upExpand, value.downShrink, value.downExpand]) {
        expect(ratio.denominator).toBe(0);
        expect(ratio.pct).toBeNull();
      }
    }
  });

  it('retains the preceding session at the 20-day boundary and changes only the requested window', () => {
    const values: [number, number][] = Array.from({ length: 25 }, (_, index) => [100 + index % 2 * 2, index < 20 ? 100 : [300, 200, 100, 200, 400][index - 20]!]);
    const features = indexVolumeFeatures(result(bars(values)), '^TWII');
    expect(features).toHaveLength(21);
    expect(volumeStructure(features, 20)).toMatchObject({ observations: 20, upShrink: { numerator: 1, denominator: 10, pct: 10 }, downExpand: { numerator: 2, denominator: 10, pct: 20 } });
    expect(volumeStructure(features, 5)).toMatchObject({ observations: 5, upShrink: { numerator: 1, denominator: 2, pct: 50 }, downExpand: { numerator: 2, denominator: 3, pct: expect.closeTo(200 / 3, 10) } });
  });

  it('waits for Taiwan close and does not promote a pre-close cached bar later in the day', () => {
    const rows = bars([[100, 100], [101, 200]], '2026-10-07T01:00:00Z');
    const before = epoch('2026-10-08T05:29:59Z'), close = epoch('2026-10-08T05:30:00Z');
    const captured = result(rows, '^TWII', before);
    expect(indexVolumeFeatures(captured, '^TWII', before)).toHaveLength(1);
    expect(indexVolumeFeatures(captured, '^TWII', close + 3600)).toHaveLength(1);
    expect(indexVolumeFeatures(result(rows, '^TWII', close), '^TWII', close)).toHaveLength(2);
  });

  it.each([
    ['2026-10-07T13:30:00Z', '2026-10-08T20:00:00Z'],
    ['2026-11-04T14:30:00Z', '2026-11-05T21:00:00Z'],
  ])('uses the US close across daylight saving time (%s)', (start, closeTime) => {
    const rows = bars([[100, 100], [101, 200]], start);
    const close = epoch(closeTime), data = result(rows, '^GSPC', close);
    expect(indexVolumeFeatures(data, '^GSPC', close - 1)).toHaveLength(1);
    expect(indexVolumeFeatures(data, '^GSPC', close)).toHaveLength(2);
  });
});

describe('labelled volume proxy for indices without volume', () => {
  it('only SOX has a proxy, and only an index with no recent volume uses it', () => {
    expect(INDEX_VOLUME_PROXIES['^SOX']).toEqual({ symbol: 'SOXX', label: 'SOXX（iShares 半導體 ETF）' });
    expect(INDEX_VOLUME_PROXIES['^GSPC']).toBeUndefined();
    expect(hasIndexVolume(result(bars([[100, 0], [101, 0]]), '^SOX'))).toBe(false);
    expect(hasIndexVolume(result(bars([[100, 0], [101, 5]]), '^SOX'))).toBe(true);
  });

  it('keeps the index prices and dates, takes same-day proxy volume and never fills a missing proxy day', () => {
    const index = result(bars([[100, 0], [110, 0], [99, 0], [100, 0]]), '^SOX', epoch('2026-10-25T12:00:00Z'));
    const proxyBars = bars([[50, 100], [55, 200], [49, 300]]);
    const proxy = result(proxyBars, 'SOXX', epoch('2026-10-24T12:00:00Z'));
    const merged = withProxyVolume(index, proxy, '^SOX');
    expect(merged.bars.map((bar) => bar.close)).toEqual([100, 110, 99, 100]);
    expect(merged.bars.map((bar) => bar.volume)).toEqual([100, 200, 300, 0]);
    expect(merged.asOf).toBe(epoch('2026-10-24T12:00:00Z'));
    const features = indexVolumeFeatures(merged, '^SOX', epoch('2026-10-25T12:00:00Z'));
    expect(features.map((feature) => feature.valid)).toEqual([true, true, true, false]);
    expect(volumeStructure(features, 20)).toMatchObject({ observations: 2, upExpand: { numerator: 1 }, downExpand: { numerator: 1 } });
  });
});
