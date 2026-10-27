import { useEffect, useState } from 'react';
import type { PageProps } from '../pages/pageProps';
import { fetchTrendingStocks, type TrendingStocksResult } from '../report/trendingStocks';
import { formatDateTime } from './format';
import './trendingStocks.css';

/** Shares the exact ordered symbols captured by the LINE financial report, not a second Yahoo query. */
export function TrendingStocksCard({ openChart }: Pick<PageProps, 'openChart'>) {
  const [result, setResult] = useState<TrendingStocksResult>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    void fetchTrendingStocks(abort.signal).then(value => {
      if (!abort.signal.aborted) setResult(value);
    });
    return () => abort.abort();
  }, [attempt]);
  const data = result?.status === 'ok' ? result.data : undefined;
  const old = data && Date.now() - Date.parse(data.fetchedAt) > 36 * 3600_000;
  return (
    <section className="card trending-stocks" aria-label="LINE 熱門美股">
      <header className="card-head"><h2>LINE 熱門美股 · {data ? `${data.symbols.length} 檔` : '最多 5 檔'}</h2></header>
      {data ? <>
        <p className="muted-text">與 LINE 財經報告相同的清單 · 抓取於 {formatDateTime(data.fetchedAt)}</p>
        {old && <p role="status">這是上次成功推播的清單，尚未取得較新資料。</p>}
        <ol>{data.symbols.map(symbol => <li key={symbol}>
          <button type="button" onClick={() => openChart({ symbol, tf: '1D' })} aria-label={`${symbol}，開啟 K 線`}>
            <b>{symbol}</b><span>K 線 →</span>
          </button>
        </li>)}</ol>
        <small className="muted-text">來源：{data.source} · 熱門排名不代表投資建議。</small>
      </> : <p role="status">{result?.status === 'error' ? result.message : '載入 LINE 熱門美股…'}</p>}
      {result?.status === 'error' && <button type="button" className="text-button" onClick={() => {
        setResult(undefined);
        setAttempt(n => n + 1);
      }}>重試熱門股清單</button>}
    </section>
  );
}
