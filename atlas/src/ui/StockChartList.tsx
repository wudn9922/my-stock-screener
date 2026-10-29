import { useEffect, useRef, useState } from 'react';
import type { OpenChartOptions } from '../app/Site';
import { MiniChart } from './MiniChart';
import { displayTicker, formatDateTime, formatPercent, formatPrice, toneClass } from './format';
import './stockChartList.css';

export interface StockChartRow { symbol: string; name: string }

function StockChartCard({ row, openChart }: { row: StockChartRow; openChart: (options: OpenChartOptions) => void }) {
  const host = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  const [quote, setQuote] = useState<{ price: number; change: number | null; date: string }>();
  const [error, setError] = useState(false);
  useEffect(() => {
    if (!host.current) return;
    if (typeof IntersectionObserver === 'undefined') { setVisible(true); return; }
    const observer = new IntersectionObserver(entries => setVisible(entries.some(entry => entry.isIntersecting)), { rootMargin: '500px' });
    observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  return <article className="stock-chart-card" aria-label={`${row.name} ${row.symbol} 圖表`} ref={host}>
    <header>
      <div className="stock-chart-name"><h3>{row.name === row.symbol ? displayTicker(row.symbol) : row.name}</h3>{row.name !== row.symbol && <span>{displayTicker(row.symbol)}</span>}</div>
      <div className="stock-chart-quote"><b>{formatPrice(quote?.price)}</b><small className={toneClass(quote?.change)}>{error ? '暫無行情' : formatPercent(quote?.change)}</small></div>
      <button type="button" className="text-button" onClick={() => openChart({ symbol: row.symbol, name: row.name })}>開啟個股</button>
    </header>
    <div className="stock-chart-canvas">
      {visible ? <MiniChart symbol={row.symbol} height={280} interactive showVolume wheelZoom={false} onStatus={(status, bars) => {
        setError(status === 'error');
        const last = bars?.at(-1), previous = bars?.at(-2);
        if (status === 'ready' && last) setQuote({ price: last.close, change: previous && previous.close > 0 ? (last.close / previous.close - 1) * 100 : null,
          date: new Date(last.time * 1000).toISOString() });
      }} /> : <span className="mini-chart-empty">捲動至此處時載入圖表</span>}
    </div>
    <small className="stock-chart-date">{quote ? `日 K 至 ${formatDateTime(quote.date, false)} · 延遲行情` : '日 K · 成交量'}</small>
  </article>;
}

/** Read-only chart review: no preset stock indicators, and no private-list persistence. */
export function StockChartList({ rows, openChart }: { rows: readonly StockChartRow[]; openChart: (options: OpenChartOptions) => void }) {
  const [limit, setLimit] = useState(20);
  const more = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!more.current || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting)) setLimit(value => value + 20); }, { rootMargin: '600px' });
    observer.observe(more.current);
    return () => observer.disconnect();
  }, [limit, rows.length]);
  return <section className="stock-chart-list" aria-label="連續股票圖表">
    {rows.slice(0, limit).map(row => <StockChartCard key={row.symbol} row={row} openChart={openChart} />)}
    {rows.length > limit && <button ref={more} type="button" className="ghost-button" onClick={() => setLimit(value => value + 20)}>繼續載入圖表（{limit}／{rows.length}）</button>}
  </section>;
}
