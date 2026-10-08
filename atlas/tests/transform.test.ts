import { it, expect } from 'vitest';
import type { IChartApi, ISeriesApi } from 'lightweight-charts';
import { ChartTransform } from '../src/chart/ChartTransform';
import { TimeMapper } from '../src/chart/TimeMapper';
const bars = Array.from({ length: 100 }, (_, i) => ({
  time: 1700000000 + i * 86400,
  open: 100,
  high: 110,
  low: 90,
  close: 105,
  volume: 1,
}));
it('public-API projection round trips anchors, and persisted points stay invariant through pan/zoom/resize', () => {
  let spacing = 6,
    offset = 20,
    priceScale = 2,
    width = 1000,
    height = 600;
  const chart = {
    timeScale: () => ({
      logicalToCoordinate: (l: number) => (l - offset) * spacing,
      coordinateToLogical: (x: number) => x / spacing + offset,
    }),
    paneSize: () => ({ width, height }),
  } as unknown as IChartApi;
  const series = {
    priceToCoordinate: (p: number) => height - p * priceScale,
    coordinateToPrice: (y: number) => (height - y) / priceScale,
  } as unknown as ISeriesApi<'Candlestick'>;
  const mapper = new TimeMapper(bars, '1D'),
    t = new ChartTransform(chart, series, mapper),
    anchor = mapper.anchor(129.5, 105),
    copy = structuredClone(anchor);
  for (const view of [
    { spacing: 6, offset: 20, priceScale: 2, width: 1000, height: 600 },
    { spacing: 12, offset: 90, priceScale: 3, width: 390, height: 650 },
    { spacing: 2, offset: 120, priceScale: 1, width: 1200, height: 800 },
  ]) {
    ({ spacing, offset, priceScale, width, height } = view);
    const point = t.toPoint(anchor)!;
    expect(t.toAnchor(point)?.time).toBeCloseTo(anchor.time, 5);
    expect(t.toAnchor(point)?.price).toBeCloseTo(anchor.price);
    expect(anchor).toEqual(copy);
    expect(t.width()).toBe(width);
    expect(t.height()).toBe(height);
  }
});
it('null conversion cannot create an invalid anchor or drawing', () => {
  const chart = {
      timeScale: () => ({ logicalToCoordinate: () => null, coordinateToLogical: () => null }),
      paneSize: () => ({ width: 0, height: 0 }),
    } as unknown as IChartApi,
    series = {
      priceToCoordinate: () => null,
      coordinateToPrice: () => null,
    } as unknown as ISeriesApi<'Candlestick'>;
  const t = new ChartTransform(chart, series, new TimeMapper(bars, '1D'));
  expect(t.toPoint(t.mapper.anchor(10, 100))).toBeNull();
  expect(t.toAnchor({ x: 10, y: 10 })).toBeNull();
});

import { restoreViewRange } from '../src/chart/ChartPreferences';
it('restores tail-relative view when switching providers with shorter history', () => {
  expect(restoreViewRange({ from: 2350, to: 2539, barCount: 2500 }, 1254)).toEqual({
    from: 1104,
    to: 1293,
  });
  expect(restoreViewRange({ from: 2350, to: 2539 }, 1254)).toEqual({ from: 1103, to: 1293 });
  expect(restoreViewRange({ from: 2300, to: 2400, barCount: 2500 }, 2500)).toEqual({
    from: 2300,
    to: 2400,
  });
});
