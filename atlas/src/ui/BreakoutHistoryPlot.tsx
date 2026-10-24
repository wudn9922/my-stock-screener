import { useEffect, useRef } from 'react';
import { ColorType, HistogramSeries, createChart, type IChartApi, type ISeriesApi, type Time } from 'lightweight-charts';
import type { BreakoutMonthlyPoint, OutcomeKey } from '../report/breakouts';

/** Percentages are recalculated as of each session; missing observations stay whitespace. */
export function BreakoutHistoryPlot({ points, method, rule, months }: {
  points: BreakoutMonthlyPoint[]; method: string; rule: OutcomeKey; months: number;
}) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<'Histogram'> | null>(null);
  const values = points.map(point => ({ date: point.asOf ?? point.targetDate,
    rate: point.status === 'ok' ? point.strategies[method]?.comparison?.rules[rule]?.winRate ?? null : null }));
  useEffect(() => {
    if (!host.current) return;
    const instance = createChart(host.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#8893a3', fontSize: 10, attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { color: '#858d9a16' } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0 } },
      timeScale: { borderVisible: false, rightOffset: 0.5, minBarSpacing: 8 },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: { time: true, price: false } },
      localization: { locale: 'zh-TW' },
    });
    const bars = instance.addSeries(HistogramSeries, { color: '#35a797', priceLineVisible: false, lastValueVisible: false,
      priceFormat: { type: 'custom', formatter: (value: number) => `${value.toFixed(1)}%`, minMove: 0.1 },
      autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) });
    chart.current = instance;
    series.current = bars;
    return () => { chart.current = null; series.current = null; instance.remove(); };
  }, []);
  useEffect(() => {
    const bars = series.current, instance = chart.current;
    if (!bars || !instance || !points.length) return;
    // Weekend-aligned targets can repeat a session only when the source is empty
    // for an entire month. Keep a unique, ascending time per public series API.
    const data = new Map<string, number | null>();
    for (const point of points) data.set(point.asOf ?? point.targetDate,
      point.status === 'ok' ? point.strategies[method]?.comparison?.rules[rule]?.winRate ?? null : null);
    const ordered = [...data].sort(([a], [b]) => a.localeCompare(b));
    bars.setData(ordered.map(([date, value]) => value === null ? { time: date as Time } : { time: date as Time, value }));
    if (months === 0) instance.timeScale().fitContent();
    else {
      const last = ordered.at(-1)?.[0];
      if (!last) return;
      const start = new Date(`${last}T00:00:00Z`);
      const anchorDay = start.getUTCDate();
      start.setUTCDate(1);
      start.setUTCMonth(start.getUTCMonth() - months);
      const lastDay = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 0)).getUTCDate();
      start.setUTCDate(Math.min(anchorDay, lastDay));
      instance.timeScale().setVisibleRange({ from: start.toISOString().slice(0, 10), to: last });
    }
  }, [points, method, rule, months]);
  return <div className="bo-history-chart" role="img" aria-label={`每月成功率柱狀圖：${values.map(value => `${value.date} ${value.rate === null ? '無完整結果' : `${value.rate.toFixed(1)}%`}`).join('；')}`}
    data-visible-months={months || 'all'} data-point-count={points.length}>
    <div ref={host} aria-hidden="true" />
  </div>;
}
