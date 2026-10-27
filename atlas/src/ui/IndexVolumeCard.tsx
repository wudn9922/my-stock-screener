import { useEffect, useState } from 'react';
import { getDailyBarResult } from '../app/dataSources';
import type { BarResult } from '../market-data/MarketDataProvider';
import { hasIndexVolume, INDEX_VOLUME_PROXIES, indexVolumeFeatures, withProxyVolume } from '../report/indexVolume';
import type { IndexStatus } from '../report/schema';
import { ResearchNote } from './research/Common';
import { VolumeRatios } from './research/VolumeRatios';
import './indexVolume.css';

export function IndexVolumeCard({ indices }: { indices: readonly IndexStatus[] }) {
  const [selected, setSelected] = useState(indices[0]?.symbol ?? '');
  const [retry, setRetry] = useState(0);
  const [loaded, setLoaded] = useState<{ symbol: string; retry: number; result?: BarResult; proxied?: boolean; error?: boolean }>();
  const index = indices.find(item => item.symbol === selected) ?? indices[0];
  const symbol = index?.symbol ?? '';
  const proxy = INDEX_VOLUME_PROXIES[symbol];
  useEffect(() => {
    if (!symbol) return;
    let active = true;
    void getDailyBarResult(symbol).then(
      async result => {
        // An index whose source has no volume at all borrows its labelled ETF's volume.
        const substitute = INDEX_VOLUME_PROXIES[symbol];
        const proxied = !hasIndexVolume(result) && !!substitute;
        const merged = proxied ? withProxyVolume(result, await getDailyBarResult(substitute.symbol), symbol) : result;
        if (active) setLoaded({ symbol, retry, result: merged, proxied });
      },
    ).catch(() => { if (active) setLoaded({ symbol, retry, error: true }); });
    return () => { active = false; };
  }, [symbol, retry]);
  if (!index) return null;
  const current = loaded?.symbol === symbol && loaded.retry === retry ? loaded : undefined;
  const result = current?.result;
  const features = result ? indexVolumeFeatures(result, symbol) : [];
  const asOf = features.at(-1)?.date;
  return <section className="card index-volume-card" aria-label="指數價量分析">
    <h3>指數價量分析</h3>
    <label className="index-volume-picker">指數
      <select aria-label="價量分析指數" value={symbol} onChange={event => setSelected(event.target.value)}>
        {indices.map(item => <option key={item.symbol} value={item.symbol}>{item.name} ({item.symbol})</option>)}
      </select>
    </label>
    {result ? <>
      <p className="source-note index-volume-date">已收盤日線 · {asOf ?? '尚無完整交易日'}</p>
      {current?.proxied && proxy && <p className="source-note index-volume-proxy" role="note">
        {`此指數的資料來源沒有成交量；成交量改用 ${proxy.label}同日成交量，漲跌仍依指數本身。`}
      </p>}
      <VolumeRatios key={symbol} features={features}>
        <p>分母為窗口內有效的上漲或下跌日；放量／縮量比較當日與前一個交易日的成交量，相同成交量不歸入任一類。沒有有效上漲或下跌日顯示 N/A。</p>
        <p>使用此指數自己的已收盤日線；零量、無效 OHLC 與未確認的大幅跳價不產生訊號，也不跨越無效交易日比較。</p>
      </VolumeRatios>
      {!features.some(feature => feature.returnPct !== null) && <p className="source-note" role="status">有效成交量或相鄰交易日不足，暫無可計算比例。</p>}
      <ResearchNote title="行情來源">
        <p>{result.source}</p>
        <p>{current?.proxied && proxy ? `價格沿用指數日線來源，成交量為 ${proxy.label}。` : '價格與成交量沿用指數日線來源；成交量口徑可能因指數而異。'}{result.cacheStatus === 'stale' ? '目前沿用較舊快取。' : '延遲行情。'}</p>
      </ResearchNote>
    </> : <div className="index-volume-status">
      <p className="source-note" role="status">{current?.error ? '此指數價量資料暫時無法取得，請稍後重試。' : '載入指數價量資料…'}</p>
      {current?.error && <button type="button" onClick={() => setRetry(value => value + 1)}>重試價量分析</button>}
    </div>}
  </section>;
}
