import { useEffect, useState } from 'react';
import { fetchScenario, scenarioPe, type Scenario } from '../../fundamentals/conservativePeAi';

export function ConservativePeAiPanel({ symbol, eligible, price }: { symbol: string; eligible: boolean; price: number | null }) {
  const [loaded, setLoaded] = useState<{ symbol: string; data: Scenario | null; error?: boolean } | null>(null);
  useEffect(() => {
    if (!eligible) return;
    const controller = new AbortController();
    void fetchScenario(symbol, controller.signal).then(data => {
      if (!controller.signal.aborted) setLoaded({ symbol, data });
    }).catch(() => {
      if (!controller.signal.aborted) setLoaded({ symbol, data: null, error: true });
    });
    return () => controller.abort();
  }, [symbol, eligible]);
  if (!eligible) return <p>不適用：未通過方法一的盈餘穩定條件。</p>;
  if (loaded?.symbol !== symbol) return <p>載入市場擔憂情境…</p>;
  if (!loaded.data) return <p>尚未產生{loaded.error ? '（資料暫時無法取得）' : ''}</p>;
  const data = loaded.data;
  const pe = scenarioPe(price, data);
  return <section aria-label="市場擔憂情境" style={{ overflowWrap: 'anywhere' }}>
    <p>情境本益比：{pe === null ? '無法計算' : pe.toFixed(2) + ' 倍'}；估計 EPS {data.scenarioEps.toFixed(4)}</p>
    <p>基期 EPS {data.baseline.eps.toFixed(4)}（{data.baseline.basis === 'ttm' ? '近四季' : '年度'}，{data.baseline.end}）；假設盈餘損失 {(data.affectedEarningsRatio * 100).toFixed(1)}%</p>
    <p>市場擔憂：{data.concern}</p><p>估計依據：{data.evidence}</p>
    <p>信心：{{ low: '低', medium: '中', high: '高' }[data.confidence]}；模型 {data.provider} / {data.model}；分析日 {data.generatedAt.slice(0, 10)}
      {Date.now() - Date.parse(data.generatedAt) > 7 * 86400000 ? '（超過七日）' : ''}</p>
    {data.segment ? <details><summary>財報分部證據</summary><a href={data.segment.url} target="_blank" rel="noreferrer">{data.segment.date} 財報</a><p>{data.segment.excerpt}</p></details>
      : <p className="fin-warn">缺少已核對的分部盈餘證據；僅低信心情境。</p>}
    <details><summary>新聞來源（標題）</summary><ul>{data.news.map(item => <li key={item.url}><a href={item.url} target="_blank" rel="noreferrer">{item.date} {item.title}</a></li>)}</ul></details>
    <p>以基期 EPS × (1 − 盈餘損失比例) 計算，再使用上方可比較的收盤價除以情境 EPS；非保證最低盈餘。</p>
  </section>;
}
