import { it, expect, vi } from 'vitest';
import { HistogramSeries, LineSeries, type IChartApi } from 'lightweight-charts';
import { IndicatorEngine } from '../src/indicators/IndicatorEngine';
import type { IndicatorInstance, IndicatorType } from '../src/indicators/IndicatorRegistry';
import { ema } from '../src/indicators/ExponentialMovingAverage';
import { coloredVolume, volume, volumeSma } from '../src/indicators/Volume';
import type { Bar } from '../src/market-data/MarketDataProvider';

const bars: Bar[] = [
  { time: 10, open: 1, high: 3, low: 0, close: 2, volume: 100 },
  { time: 20, open: 3, high: 5, low: 2, close: 4, volume: 200 },
  { time: 30, open: 5, high: 7, low: 4, close: 6, volume: 300 },
  { time: 40, open: 7, high: 9, low: 6, close: 8, volume: 400 },
  { time: 50, open: 9, high: 11, low: 8, close: 10, volume: 500 },
];

it('EMA seeds with the first period SMA, then uses a finite lookback and the selected source', () => {
  expect(ema(bars, 3, 'high')).toEqual([
    { time: 30, value: 5 },
    { time: 40, value: 7 },
    { time: 50, value: 9 },
  ]);
  expect(ema(bars.slice(0, 2), 3)).toEqual([]);
  expect(() => ema(bars, 0)).toThrow('EMA period must be a positive integer');
});

it('Volume returns each bar volume with its original timestamp', () => {
  expect(volume(bars)).toEqual([
    { time: 10, value: 100 },
    { time: 20, value: 200 },
    { time: 30, value: 300 },
    { time: 40, value: 400 },
    { time: 50, value: 500 },
  ]);
});

it('colors volume by candle direction and starts its rolling mean after a full window', () => {
  const mixed = [bars[0], { ...bars[1], open: 5, close: 4 }, { ...bars[2], open: 6, close: 6 }];
  expect(coloredVolume(mixed)).toEqual([
    { time: 10, value: 100, color: '#39baa0b3' },
    { time: 20, value: 200, color: '#ef6b7bb3' },
    { time: 30, value: 300, color: '#39baa0b3' },
  ]);
  expect(volumeSma(bars, 3)).toEqual([
    { time: 30, value: 200 },
    { time: 40, value: 300 },
    { time: 50, value: 400 },
  ]);
  expect(volumeSma(bars)).toEqual([]);
  expect(volumeSma(bars, 1)).toEqual(bars.map(({ time, volume: value }) => ({ time, value })));
  expect(() => volumeSma(bars, 0)).toThrow('positive integer');
  expect(() => volumeSma(bars, 1.5)).toThrow('positive integer');
});

function instance(type: IndicatorType, id: string): IndicatorInstance {
  return {
    id,
    symbol: 'AAPL',
    type,
    period: type === 'Volume' ? 1 : 3,
    source: 'close',
    visible: true,
    locked: false,
    lineWidth: 2,
    color: '#f0b35b',
    scope: {},
  };
}

it('IndicatorEngine registers paired volume series, uses per-volume scales, and caches unchanged data', () => {
  const addedTypes: unknown[] = [];
  const seriesOptions: unknown[] = [];
  const scaleOptions: unknown[] = [];
  const created: { setData: ReturnType<typeof vi.fn>; applyOptions: ReturnType<typeof vi.fn> }[] =
    [];
  const chart = {
    addSeries: vi.fn((type: unknown, options: unknown) => {
      addedTypes.push(type);
      seriesOptions.push(options);
      const api = {
        setData: vi.fn(),
        applyOptions: vi.fn(),
        priceScale: () => ({
          applyOptions: vi.fn((options: unknown) => scaleOptions.push(options)),
        }),
      };
      created.push(api);
      return api;
    }),
    removeSeries: vi.fn(),
  } as unknown as IChartApi;
  const engine = new IndicatorEngine(chart);
  const smaInstance = instance('SMA', 'ma-id');
  const volumeInstance = instance('Volume', 'volume-id');

  engine.sync([smaInstance, volumeInstance], bars, '1D', 1);
  expect(addedTypes).toEqual([LineSeries, HistogramSeries, LineSeries]);
  expect(scaleOptions).toEqual([{ scaleMargins: { top: 0.8, bottom: 0 } }]);
  expect(seriesOptions[1]).toMatchObject({ priceScaleId: 'volume-indicator-volume-id' });
  expect(seriesOptions[2]).toMatchObject({
    priceScaleId: 'volume-indicator-volume-id',
    color: volumeInstance.color,
    lineWidth: volumeInstance.lineWidth,
  });
  expect(created[1].setData).toHaveBeenCalledWith(coloredVolume(bars));
  expect(created[2].setData).toHaveBeenCalledWith([]);
  expect(engine.registry.get('Volume')?.calculate(bars, 99, 'high')).toEqual(
    bars.map((bar) => ({ time: bar.time, value: bar.volume })),
  );

  engine.sync([{ ...smaInstance, color: '#ffffff' }, volumeInstance], bars, '1D', 1);
  expect(created[0].setData).toHaveBeenCalledTimes(1);
  expect(created[1].setData).toHaveBeenCalledTimes(1);
  expect(created[2].setData).toHaveBeenCalledTimes(1);

  engine.sync([instance('EMA', 'ma-id'), volumeInstance], bars, '1D', 1);
  expect(chart.removeSeries).toHaveBeenCalledTimes(1);
  expect(addedTypes).toHaveLength(4);
  expect(addedTypes[3]).toBe(LineSeries);
  engine.clear();
  expect(chart.removeSeries).toHaveBeenCalledTimes(4);
});

it('keeps time-keyed volume values independent by id and removes both series on replacement', () => {
  const created: { setData: ReturnType<typeof vi.fn>; applyOptions: ReturnType<typeof vi.fn> }[] =
    [];
  const removed: unknown[] = [];
  const chart = {
    addSeries: vi.fn(() => {
      const api = {
        setData: vi.fn(),
        applyOptions: vi.fn(),
        priceScale: () => ({ applyOptions: vi.fn() }),
      };
      created.push(api);
      return api;
    }),
    removeSeries: vi.fn((series: unknown) => removed.push(series)),
  } as unknown as IChartApi;
  const engine = new IndicatorEngine(chart),
    first = instance('Volume', 'first-volume'),
    second = { ...instance('Volume', 'second-volume'), period: 3 };
  const volumeBars: Bar[] = Array.from({ length: 25 }, (_, i) => {
    const n = i + 1;
    return { time: n * 10, open: n, high: n + 2, low: n - 1, close: n + 1, volume: n * 100 };
  });
  engine.sync([first, second], volumeBars, '1D', 7);

  expect(engine.valueAt(first.id, 20)).toBe(200);
  expect(engine.volumeAverageAt(first.id, 190)).toBeNull();
  expect(engine.volumeAverageAt(first.id, 200)).toBe(1050);
  expect(engine.volumeAverageAt(first.id, 250)).toBe(1550);
  expect(engine.volumeAverageAt(second.id, 200)).toBe(1050);
  expect(engine.valueAt(first.id, 25)).toBeNull();
  expect(engine.valueAt('missing', 20)).toBeNull();

  engine.sync([{ ...first, color: '#ffffff', lineWidth: 4 }, second], volumeBars, '1D', 7);
  expect(created[0].applyOptions).toHaveBeenLastCalledWith({
    color: '#ffffff',
    visible: true,
    title: '',
  });
  expect(created[1].applyOptions).toHaveBeenLastCalledWith({
    color: '#ffffff',
    lineWidth: 4,
    visible: true,
    title: '',
  });
  expect(created[0].setData).toHaveBeenCalledTimes(1);
  expect(created[1].setData).toHaveBeenCalledTimes(1);

  engine.sync([{ ...first, period: 3 }, second], volumeBars, '1D', 7);
  expect(created[0].setData).toHaveBeenCalledTimes(1);
  expect(created[1].setData).toHaveBeenCalledTimes(1);
  expect(created[2].setData).toHaveBeenCalledTimes(1);
  expect(engine.volumeAverageAt(first.id, 200)).toBe(1050);
  engine.sync([{ ...first, period: 3 }, second], volumeBars, '1D', 8);
  expect(created[0].setData).toHaveBeenCalledTimes(2);
  expect(created[1].setData).toHaveBeenCalledTimes(2);
  expect(created[2].setData).toHaveBeenCalledTimes(2);
  expect(created[3].setData).toHaveBeenCalledTimes(2);

  engine.sync([{ ...first, period: 3, visible: false }, second], volumeBars, '1D', 8);
  expect(engine.valueAt(first.id, 20)).toBeNull();
  expect(engine.volumeAverageAt(first.id, 200)).toBeNull();
  engine.sync(
    [
      { ...first, period: 3, visible: false },
      { ...second, scope: { timeframe: '1W' } },
    ],
    volumeBars,
    '1D',
    8,
  );
  expect(engine.valueAt(first.id, 20)).toBeNull();
  expect(engine.valueAt(second.id, 20)).toBeNull();
  expect(removed).toHaveLength(2);

  engine.sync([instance('SMA', first.id)], volumeBars, '1D', 9);
  expect(removed).toHaveLength(4);
  expect(engine.volumeAverageAt(first.id, 200)).toBeNull();
  engine.clear();
});
