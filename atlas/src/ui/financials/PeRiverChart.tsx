import { useEffect, useRef } from 'react';
import { ColorType, CrosshairMode, LineSeries, LineStyle, PriceScaleMode, createChart, type MouseEventParams, type Time } from 'lightweight-charts';
import { formatMultiple } from '../../fundamentals/peRiver';
import { PRICE_COLOR, type RiverChartData } from './peRiverChartData';

const timeKey = (time: Time | undefined): string | null => {
  if (time === undefined) return null;
  if (typeof time === 'string') return time;
  if (typeof time === 'number') return new Date(time * 1000).toISOString().slice(0, 10);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${time.year}-${pad(time.month)}-${pad(time.day)}`;
};

const priceText = (value: number) => (value >= 1000 ? value.toFixed(0) : value.toFixed(2));

/**
 * Price (amber) over five P/E-multiple bands (blue, light = cheap multiple). Each band's axis label is its
 * multiple (e.g. 18.4x) next to the price it stands for today. The crosshair readout is written straight
 * into a DOM node, so pointer movement does not re-render React.
 */
export default function PeRiverChart({ data, ariaLabel }: { data: RiverChartData; ariaLabel: string }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const readoutRef = useRef<HTMLParagraphElement>(null);
  const dataRef = useRef(data);
  dataRef.current = data;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const chart = createChart(host, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#8a93a3',
        fontSize: 11,
        fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif',
        attributionLogo: false,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: '#1b212b' } },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#6b7587', labelBackgroundColor: '#2b3340' },
        horzLine: { color: '#6b7587', labelBackgroundColor: '#2b3340' },
      },
      // Logarithmic: ten years of growth would otherwise flatten the early bands against the axis.
      rightPriceScale: { borderVisible: false, mode: PriceScaleMode.Logarithmic, scaleMargins: { top: 0.08, bottom: 0.06 } },
      timeScale: { borderVisible: false, rightOffset: 1, minBarSpacing: 0.2 },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: false },
      localization: { locale: 'zh-TW', priceFormatter: priceText },
    });
    for (const band of dataRef.current.bands) {
      const line = chart.addSeries(LineSeries, {
        color: band.color,
        lineWidth: band.p === 50 ? 2 : 1,
        lineStyle: LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerVisible: false,
        title: band.label,
      });
      line.setData(band.points);
    }
    const price = chart.addSeries(LineSeries, {
      color: PRICE_COLOR,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerRadius: 4,
      title: '',
    });
    price.setData(dataRef.current.price);
    chart.timeScale().fitContent();

    const move = (param: MouseEventParams) => {
      const node = readoutRef.current;
      if (!node) return;
      const day = timeKey(param.time);
      const point = param.seriesData.get(price) as { value?: number } | undefined;
      if (!day || !point || typeof point.value !== 'number') {
        node.textContent = '移動游標（手機長按）圖表查看當日股價與本益比';
        return;
      }
      const pe = dataRef.current.peByDate[day];
      node.textContent = `${day} · 股價 ${priceText(point.value)} · ${typeof pe === 'number' ? `本益比 ${formatMultiple(pe)}` : '本益比不適用（無 EPS 或虧損）'}`;
    };
    chart.subscribeCrosshairMove(move);
    return () => {
      chart.unsubscribeCrosshairMove(move);
      chart.remove();
    };
    // The chart is rebuilt when the data object changes (a different stock or a reload).
  }, [data]);

  return (
    <div className="pe-river-chart-wrap">
      <div ref={hostRef} className="pe-river-chart" role="img" aria-label={ariaLabel} />
      <p ref={readoutRef} className="pe-river-readout" aria-live="off">
        移動游標（手機長按）圖表查看當日股價與本益比
      </p>
    </div>
  );
}
