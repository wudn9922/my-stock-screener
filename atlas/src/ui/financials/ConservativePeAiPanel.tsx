import { useEffect, useState } from 'react';
import { fetchScenario, sameScenarioBaseline, scenarioIsCurrent, scenarioPe, type Scenario, type ScenarioBaseline } from '../../fundamentals/conservativePeAi';

export function ConservativePeAiPanel({ symbol, baseline, price }: {
  symbol: string; baseline: ScenarioBaseline | null; price: { value: number; currency: string } | null;
}) {
  const [loaded, setLoaded] = useState<{ symbol: string; data: Scenario | null; error?: boolean } | null>(null);
  const eligible = baseline !== null;
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
  if (!baseline) return <p>未取得符合盈餘穩定與股數基準的財報。</p>;
  if (loaded?.symbol !== symbol) return <p>載入市場擔憂情境…</p>;
  if (!loaded.data) return <p>尚未產生{loaded.error ? '（資料暫時無法取得）' : ''}</p>;
  const data = loaded.data;
  const current = scenarioIsCurrent(data);
  if (!current) return <p>AI 分析已逾期，等待重新分析。</p>;
  const sameBasis = sameScenarioBaseline(data.baseline, baseline);
  const pe = scenarioPe(price, data, baseline);
  return <section aria-label="市場擔憂情境" style={{ overflowWrap: 'anywhere' }}>
    {data.scenarioEps === null ? <p>分部盈餘證據不足，暫無情境估值。</p> : <p>
      情境本益比 {pe === null ? '暫無' : pe.toFixed(2) + ' 倍'} · 估計 EPS {data.scenarioEps.toFixed(4)}
    </p>}
    {!sameBasis && <p className="fin-warn">財報基期或股數基準已更新，待重新分析後估值。</p>}
    <p>基期 EPS {data.baseline.currency} {data.baseline.eps.toFixed(4)}（{data.baseline.basis === 'ttm' ? '近四季' : '年度'}，{data.baseline.end}）
      {data.affectedEarningsRatio !== null && ` · 假設盈餘損失 ${(data.affectedEarningsRatio * 100).toFixed(1)}%`}</p>
    <p>市場擔憂：{data.concern}</p><p>估計依據：{data.evidence}</p>
    <p>AI 分析 {data.generatedAt.slice(0, 10)} · {data.model}（{data.provider}）</p>
    <p>信心：{{ low: '低', medium: '中', high: '高' }[data.confidence]}</p>
    {data.segment ? <details><summary>財報分部營業利益</summary><a href={data.segment.url} target="_blank" rel="noreferrer">{data.segment.periodEnd} 財報</a><p>{data.segment.excerpt}</p></details>
      : <p className="fin-warn">缺少已核對的分部盈餘證據；僅低信心情境。</p>}
    <details><summary>新聞來源（標題）</summary><ul>{data.news.map(item => <li key={item.url}><a href={item.url} target="_blank" rel="noreferrer">{item.date} {item.title}</a></li>)}</ul></details>
    <p>以基期 EPS × (1 − 盈餘損失比例) 計算，再使用上方可比較的收盤價除以情境 EPS；非保證最低盈餘。</p>
  </section>;
}
