import { useEffect, useMemo, useRef, useState } from 'react';
import { loadFinancials, type FinancialsLoadResult } from '../../fundamentals/FinancialHistory';
import { buildPeRiver, formatMultiple } from '../../fundamentals/peRiver';
import { calendarDate, getMarketProfile } from '../../market-data/MarketProfile';
import { useNearViewport } from '../MiniChart';
import { HelpDisclosure } from '../HelpDisclosure';
import { TimeChart } from '../rates/TimeChart';
import { loadWeeklyPrices, type PriceLoad } from './PeRiverCard';
import { buildRiverChartData } from './peRiverChartData';

/** Published historical EPS, never today's EPS applied backwards to historical prices. */
export function ChartPeHistory({ symbol }: { symbol: string }) {
  const { ref, near } = useNearViewport<HTMLElement>('200px');
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<{ financials: FinancialsLoadResult; prices: PriceLoad }>();
  const readout = useRef<HTMLSpanElement>(null);
  const hoverFrame = useRef<number | null>(null);
  useEffect(() => {
    if (!near) return;
    const controller = new AbortController();
    void Promise.all([loadFinancials(symbol, attempt > 0), loadWeeklyPrices(symbol, controller.signal)]).then(([financials, prices]) => {
      if (!controller.signal.aborted) setLoaded({ financials, prices });
    });
    return () => controller.abort();
  }, [symbol, near, attempt]);
  const result = useMemo(() => loaded?.financials.status === 'ok' && loaded.prices.status === 'ok'
    ? buildPeRiver(loaded.financials.data, loaded.prices.bars, { asOfSec: loaded.prices.asOfSec, priceBasis: loaded.prices.priceBasis }) : null, [loaded]);
  const data = useMemo(() => result?.status === 'ok' ? buildRiverChartData(result.model, getMarketProfile(symbol)) : null, [result, symbol]);
  const lines = useMemo(() => data ? [{ id: 'pe', color: '#6ba3f2', points: Object.entries(data.peByDate).map(([time, value]) => ({ time, value })) }] : [], [data]);
  const error = loaded?.financials.status !== 'ok' ? loaded?.financials.message : loaded.prices.status === 'error' ? loaded.prices.message : undefined;
  const headline = result?.status === 'ok'
    ? result.model.current ? formatMultiple(result.model.current.pe) : result.model.currentReason ?? '目前本益比無法計算。'
    : '週收盤價 ÷ 歷史 EPS';
  useEffect(() => {
    if (readout.current) readout.current.textContent = headline;
    return () => { if (hoverFrame.current !== null) cancelAnimationFrame(hoverFrame.current); };
  }, [headline]);
  const showHover = (values: Record<string, number> | null, date: string | null) => {
    if (hoverFrame.current !== null) cancelAnimationFrame(hoverFrame.current);
    hoverFrame.current = requestAnimationFrame(() => {
      hoverFrame.current = null;
      if (readout.current) readout.current.textContent = date ? `${date} · ${values?.pe === undefined ? '—' : formatMultiple(values.pe)}` : headline;
    });
  };
  return <section ref={ref} className="card chart-pe-history" aria-label={`${symbol} 本益比走勢`}>
    <div className="chart-pe-heading"><h2>本益比走勢</h2><span ref={readout}>{headline}</span></div>
    {!loaded ? <p role="status">{near ? '載入歷史本益比…' : '往下滑即可載入本益比圖表。'}</p> : error ? <div role="status"><p>{error}</p><button className="ghost-button" type="button" onClick={() => { setLoaded(undefined); setAttempt(value => value + 1); }}>重新載入本益比</button></div> : result?.status === 'unavailable' ? <p role="status">{result.message}</p> : data ? <>
      <TimeChart className="chart-pe-canvas" lines={lines} format={value => formatMultiple(value)} zoom ariaLabel={`${symbol} 歷史本益比折線圖，可拖曳縮放`} onHover={showHover} />
      <p className="source-note">雙指縮放或拖曳時間軸查看歷史。{loaded.prices.status === 'ok' && loaded.prices.stale ? '目前顯示快取資料。' : ''}</p>
    </> : null}
    <HelpDisclosure label="本益比計算與資料說明">
      <p>週收盤價除以當時已公布的近四季 EPS；不足時使用年度 EPS。虧損、缺少財報或股價與 EPS 分割基準不一致的區間留白。沿用財務頁的公布日期與除權息檢查。歷史本益比僅供研究。</p>
      {loaded?.financials.status === 'ok' && <p>財報來源：{loaded.financials.data.source ?? '見財務頁'} · 更新：{loaded.financials.data.checkedAt ?? loaded.financials.data.generatedAt ?? '日期未提供'}</p>}
      {loaded?.prices.status === 'ok' && <p>股價資料日：{calendarDate(loaded.prices.asOfSec, getMarketProfile(symbol))}</p>}
      {(result?.status === 'ok' ? result.model.notes : result?.notes ?? []).map((note, index) => <p key={index}>{note}</p>)}
    </HelpDisclosure>
  </section>;
}
