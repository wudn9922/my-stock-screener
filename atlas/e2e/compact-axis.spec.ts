import { expect, test } from '@playwright/test';
import type { UTCTimestamp } from 'lightweight-charts';
import type { Bar, BarResult } from '../src/market-data/MarketDataProvider';

const samples = [
  { id: 'price-275', price: 275.19, label: '275.19', ordinary: true },
  { id: 'price-44', price: 44.94, label: '44.94', ordinary: true },
  { id: 'price-69', price: 69.7, label: '69.7', ordinary: true },
  { id: 'price-114', price: 114.95, label: '114.95', ordinary: true },
  { id: 'price-2550', price: 2550, label: '2550', ordinary: true },
  { id: 'price-million', price: 1_000_000, label: '1000000', ordinary: false },
] as const;

test('compact price axis saves ordinary width and preserves native chart geometry and crosshair input', async ({
  page,
}) => {
  await page.goto('/');
  await expect(page.locator('.chart-loading')).toHaveCount(0);

  let ordinaryAxisWidth: number | undefined;
  for (const sample of samples) {
    const measurement = await page.evaluate(async (caseData) => {
      const engineUrl = new URL('/src/chart/ChartEngine.ts', location.origin).href;
      const axisUrl = new URL('/src/chart/AxisAppearance.ts', location.origin).href;
      const labelsUrl = new URL('/src/chart/MarketTimeLabels.ts', location.origin).href;
      const marketUrl = new URL('/src/market-data/MarketProfile.ts', location.origin).href;
      const chartLibraryUrl = new URL(
        '/node_modules/.vite/deps/lightweight-charts.js',
        location.origin,
      ).href;
      const [{ ChartEngine }, { ensureAxisFont }, { marketTimeOptions }, { getMarketProfile }, chartLibrary] = await Promise.all([
        import(engineUrl) as Promise<typeof import('../src/chart/ChartEngine')>,
        import(axisUrl) as Promise<typeof import('../src/chart/AxisAppearance')>,
        import(labelsUrl) as Promise<typeof import('../src/chart/MarketTimeLabels')>,
        import(marketUrl) as Promise<typeof import('../src/market-data/MarketProfile')>,
        import(chartLibraryUrl) as Promise<typeof import('lightweight-charts')>,
      ]);
      const fontLoaded = await ensureAxisFont();
      await document.fonts.ready;
      const fontAvailable = document.fonts.check('11px "AtlasNarrowAxis"');
      const width = Math.max(320, Math.min(720, window.innerWidth - 24));
      const height = 360;
      const start = Date.parse('2026-01-05T00:00:00Z') / 1000;
      const step = caseData.price >= 100_000 ? 10 : caseData.price >= 1000 ? 0.25 : 0.01;
      const bars: Bar[] = Array.from({ length: 36 }, (_, index) => {
        const close = caseData.price + (index - 18) * step;
        const open = close + (index % 2 === 0 ? step : -step);
        return {
          time: start + index * 86400,
          open,
          high: Math.max(open, close) + step,
          low: Math.min(open, close) - step,
          close,
          volume: 100_000 + index * 1_000,
        };
      });
      const result: BarResult = {
        bars,
        source: 'Controlled chart-rendering fixture',
        session: 'regular',
        delayed: false,
        adjusted: false,
        priceBasis: 'unadjusted',
        asOf: bars.at(-1)!.time,
        latestBarAt: bars.at(-1)!.time,
        dataState: 'simulated',
        cacheStatus: 'fresh',
      };
      const nextFrame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      const host = (name: string) => {
        const element = document.createElement('div');
        element.dataset.axisHost = name;
        Object.assign(element.style, {
          position: 'fixed',
          left: '12px',
          top: '12px',
          width: `${width}px`,
          height: `${height}px`,
          zIndex: '10000',
          background: '#0e1521',
        });
        document.body.append(element);
        return element;
      };
      const callbacks = {
        commit: () => undefined,
        selection: () => undefined,
        tool: () => undefined,
        view: () => undefined,
      };
      const baselineHost = host(`baseline-${caseData.id}`);
      const baselineChart = chartLibrary.createChart(baselineHost, {
        autoSize: true,
        layout: {
          background: { type: chartLibrary.ColorType.Solid, color: '#0e1521' },
          textColor: '#8290a5',
          fontFamily: 'Inter, system-ui, sans-serif',
          fontSize: 11,
          attributionLogo: true,
        },
        grid: { vertLines: { color: '#1a2332' }, horzLines: { color: '#1a2332' } },
        crosshair: {
          mode: chartLibrary.CrosshairMode.Normal,
          vertLine: { color: '#73849b', width: 1, labelBackgroundColor: '#31415a' },
          horzLine: { color: '#73849b', width: 1, labelBackgroundColor: '#31415a' },
        },
        rightPriceScale: {
          borderColor: '#243043',
          minimumWidth: 40,
          scaleMargins: { top: 0.05, bottom: 0.22 },
        },
        timeScale: {
          borderColor: '#243043',
          rightOffset: 40,
          barSpacing: 6,
          timeVisible: true,
          secondsVisible: false,
        },
        handleScroll: {
          mouseWheel: true,
          pressedMouseMove: true,
          horzTouchDrag: true,
          vertTouchDrag: true,
        },
        handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
        localization: { locale: 'en-US' },
      });
      const baselineCandles = baselineChart.addSeries(chartLibrary.CandlestickSeries, {
        upColor: '#39baa0',
        downColor: '#ef6b7b',
        borderVisible: false,
        wickUpColor: '#39baa0',
        wickDownColor: '#ef6b7b',
        priceLineColor: '#39baa0',
      });
      chartLibrary.createSeriesMarkers(baselineCandles, [], { autoScale: false });
      const baselineVolume = baselineChart.addSeries(chartLibrary.HistogramSeries, {
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
        priceLineVisible: false,
        lastValueVisible: false,
      });
      baselineVolume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
      baselineChart.addSeries(chartLibrary.LineSeries, {
        color: '#f0b35b',
        lineWidth: 2,
        priceScaleId: 'volume',
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        visible: false,
        title: '',
      });
      baselineChart.applyOptions(marketTimeOptions(getMarketProfile('AAPL'), '1D'));
      baselineCandles.setData(bars.map((bar) => ({ ...bar, time: bar.time as UTCTimestamp })));
      baselineVolume.setData(
        bars.map((bar) => ({
          time: bar.time as UTCTimestamp,
          value: bar.volume,
          color: bar.close >= bar.open ? '#39baa0' : '#ef6b7b',
        })),
      );
      baselineChart.timeScale().setVisibleLogicalRange({ from: 0, to: bars.length - 1 });
      await nextFrame();
      await nextFrame();
      await nextFrame();
      const baselineOptions = baselineChart.options();
      const baselineCoordinate = baselineCandles.priceToCoordinate(caseData.price);
      const baseline = {
        axisWidth: baselineCandles.priceScale().width(),
        paneWidth: baselineChart.paneSize().width,
        hostWidth: baselineHost.clientWidth,
        hostHeight: baselineHost.clientHeight,
        minimumWidth: baselineOptions.rightPriceScale.minimumWidth,
        fontFamily: baselineOptions.layout.fontFamily,
        fontSize: baselineOptions.layout.fontSize,
        priceFormatterAbsent: !baselineOptions.localization.priceFormatter,
        priceCoordinate: baselineCoordinate,
      };
      baselineChart.remove();
      baselineHost.remove();

      const candidateHost = host(`candidate-${caseData.id}`);
      const header = document.createElement('div');
      const source = document.createElement('div');
      const candidateEngine = new ChartEngine(candidateHost, header, source, callbacks);
      candidateEngine.load('AAPL', '1D', result, [], []);
      candidateEngine.chart.timeScale().setVisibleLogicalRange({ from: 0, to: bars.length - 1 });
      await nextFrame();
      await nextFrame();
      await nextFrame();
      const candidateOptions = candidateEngine.chart.options();
      const crosshairReadout = document.createElement('output');
      crosshairReadout.dataset.axisCrosshair = caseData.id;
      crosshairReadout.style.position = 'fixed';
      crosshairReadout.style.left = '-10000px';
      document.body.append(crosshairReadout);
      candidateEngine.chart.subscribeCrosshairMove((param) => {
        const hovered = param.seriesData.get(candidateEngine.candles);
        if (param.point && hovered && 'close' in hovered) {
          crosshairReadout.textContent = Number(hovered.close).toFixed(2);
        }
      });
      const anchor = bars[18];
      const x = candidateEngine.chart.timeScale().timeToCoordinate(anchor.time as never);
      const y = candidateEngine.candles.priceToCoordinate(anchor.close);
      const rect = candidateHost.getBoundingClientRect();
      const priceFormatter = candidateOptions.localization.priceFormatter;
      const candidate = {
        axisWidth: candidateEngine.candles.priceScale().width(),
        paneWidth: candidateEngine.chart.paneSize().width,
        hostWidth: candidateHost.clientWidth,
        hostHeight: candidateHost.clientHeight,
        minimumWidth: candidateOptions.rightPriceScale.minimumWidth,
        fontFamily: candidateOptions.layout.fontFamily,
        fontSize: candidateOptions.layout.fontSize,
        priceFormatter:
          priceFormatter?.(
            caseData.price as Parameters<NonNullable<typeof priceFormatter>>[0],
          ) ?? null,
        priceCoordinate: y,
        mousePoint: { x: rect.left + (x ?? 0), y: rect.top + (y ?? 0) },
        anchorClose: anchor.close.toFixed(2),
      };
      Object.assign(candidateHost, {
        cleanupAxisTest: () => {
          candidateEngine.destroy();
          crosshairReadout.remove();
        },
      });
      return { baseline, candidate, fontLoaded, fontAvailable };
    }, sample);

    expect(measurement.fontLoaded, 'the compact axis font asset should load').toBe(true);
    expect(measurement.fontAvailable).toBe(true);
    expect(measurement.baseline.minimumWidth).toBe(40);
    expect(measurement.baseline.fontFamily).toContain('Inter');
    expect(measurement.baseline.fontSize).toBe(11);
    expect(measurement.baseline.priceFormatterAbsent).toBe(true);
    expect(measurement.candidate.minimumWidth).toBe(0);
    expect(measurement.candidate.fontFamily).toContain('AtlasNarrowAxis');
    expect(measurement.candidate.fontSize).toBe(11);
    expect(measurement.candidate.priceFormatter).toBe(sample.label);
    expect(measurement.candidate.hostWidth).toBe(measurement.baseline.hostWidth);
    expect(measurement.candidate.hostHeight).toBe(measurement.baseline.hostHeight);
    expect(measurement.candidate.paneWidth).toBeGreaterThan(0);
    expect(measurement.candidate.axisWidth).toBeGreaterThan(0);
    expect(
      measurement.candidate.paneWidth + measurement.candidate.axisWidth,
    ).toBeLessThanOrEqual(measurement.candidate.hostWidth);
    expect(measurement.candidate.priceCoordinate).not.toBeNull();
    expect(measurement.baseline.priceCoordinate).not.toBeNull();
    expect(
      Math.abs(measurement.candidate.priceCoordinate! - measurement.baseline.priceCoordinate!),
    ).toBeLessThanOrEqual(1);
    if (sample.ordinary) {
      expect(measurement.candidate.axisWidth).toBeLessThanOrEqual(
        measurement.baseline.axisWidth * 0.7,
      );
      ordinaryAxisWidth ??= measurement.candidate.axisWidth;
    } else {
      expect(ordinaryAxisWidth).toBeDefined();
      expect(measurement.candidate.axisWidth).toBeGreaterThan(ordinaryAxisWidth!);
    }

    await page.mouse.move(measurement.candidate.mousePoint.x, measurement.candidate.mousePoint.y);
    await expect(page.locator(`[data-axis-crosshair="${sample.id}"]`)).toHaveText(
      measurement.candidate.anchorClose,
    );
    await page.evaluate((id) => {
      const candidate = document.querySelector(`[data-axis-host="candidate-${id}"]`) as
        | (HTMLDivElement & { cleanupAxisTest?: () => void })
        | null;
      candidate?.cleanupAxisTest?.();
      candidate?.remove();
    }, sample.id);
  }
});
