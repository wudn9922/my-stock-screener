import { afterEach, expect, it, vi } from 'vitest';

vi.mock('lightweight-charts', () => {
  const series = () => ({
    applyOptions: vi.fn(),
    priceScale: () => ({ applyOptions: vi.fn() }),
    setData: vi.fn(),
    priceToCoordinate: () => 0,
  });
  const timeScale = () => ({
    subscribeVisibleLogicalRangeChange: vi.fn(),
    unsubscribeVisibleLogicalRangeChange: vi.fn(),
    subscribeSizeChange: vi.fn(),
    unsubscribeSizeChange: vi.fn(),
  });
  return {
    CandlestickSeries: 'CandlestickSeries',
    HistogramSeries: 'HistogramSeries',
    LineSeries: 'LineSeries',
    ColorType: { Solid: 'solid' },
    CrosshairMode: { Normal: 0 },
    createChart: () => ({
      addSeries: () => series(),
      applyOptions: vi.fn(),
      removeSeries: vi.fn(),
      subscribeCrosshairMove: vi.fn(),
      unsubscribeCrosshairMove: vi.fn(),
      timeScale,
      remove: vi.fn(),
    }),
    createSeriesMarkers: () => ({ setMarkers: vi.fn() }),
  };
});

import { ChartEngine } from '../src/chart/ChartEngine';

afterEach(() => vi.unstubAllGlobals());

function pointerEvent(type: string, pointerId: number, buttons: number): Event {
  const event = new Event(type);
  Object.defineProperties(event, {
    pointerId: { value: pointerId },
    buttons: { value: buttons },
  });
  return event;
}

it('ends tracked pointers outside the chart and clears them on blur, then removes global listeners', () => {
  const frames: FrameRequestCallback[] = [];
  const requestFrame = vi.fn((callback: FrameRequestCallback) => {
    frames.push(callback);
    return frames.length;
  });
  const windowTarget = new EventTarget();
  vi.stubGlobal('window', windowTarget);
  vi.stubGlobal('requestAnimationFrame', requestFrame);
  vi.stubGlobal('cancelAnimationFrame', vi.fn());

  const host = new EventTarget() as HTMLElement;
  const header = { textContent: '', style: {} } as unknown as HTMLElement;
  const sourceLabel = { textContent: '', title: '' } as unknown as HTMLElement;
  const chart = new ChartEngine(host, header, sourceLabel, {
    commit: vi.fn(),
    selection: vi.fn(),
    tool: vi.fn(),
    view: vi.fn(),
  });
  (chart as unknown as { indicatorInstances: unknown[] }).indicatorInstances = [
    { type: 'SMA', widthMode: 'atr', scope: {} },
  ];
  const runFrame = () => frames.shift()?.(0);

  host.dispatchEvent(pointerEvent('pointerdown', 7, 1));
  host.dispatchEvent(pointerEvent('pointermove', 7, 1));
  expect(requestFrame).toHaveBeenCalledTimes(1);
  runFrame();

  windowTarget.dispatchEvent(pointerEvent('pointerup', 7, 0));
  expect(requestFrame).toHaveBeenCalledTimes(2);
  runFrame();
  host.dispatchEvent(pointerEvent('pointermove', 7, 0));
  expect(requestFrame).toHaveBeenCalledTimes(2);

  host.dispatchEvent(pointerEvent('pointerdown', 8, 1));
  host.dispatchEvent(pointerEvent('pointermove', 8, 1));
  expect(requestFrame).toHaveBeenCalledTimes(3);
  runFrame();
  windowTarget.dispatchEvent(new Event('blur'));
  expect(requestFrame).toHaveBeenCalledTimes(4);
  runFrame();
  host.dispatchEvent(pointerEvent('pointermove', 8, 0));
  expect(requestFrame).toHaveBeenCalledTimes(4);

  chart.destroy();
  host.dispatchEvent(pointerEvent('pointerdown', 9, 1));
  host.dispatchEvent(pointerEvent('pointermove', 9, 1));
  windowTarget.dispatchEvent(pointerEvent('pointerup', 9, 0));
  windowTarget.dispatchEvent(new Event('blur'));
  expect(requestFrame).toHaveBeenCalledTimes(4);
});
