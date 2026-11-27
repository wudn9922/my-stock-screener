import { useEffect, useMemo, useState } from 'react';
import type { FinancialHistory } from '../../fundamentals/FinancialHistory';
import { assessEligibility, conservativeEps, conservativePeClose, conservativePePriceIssue, forecastInertia, type Forecast } from '../../fundamentals/conservativePe';
import { conservativePeScenarioBaseline } from '../../fundamentals/conservativePeAi';
import { ConservativePeAiPanel } from './ConservativePeAiPanel';
import { FoldCard } from './FoldCard';

const number = (value: number) => value.toLocaleString('zh-TW', { maximumFractionDigits: 4 });

function ForecastDetail({ title, result }: { title: string; result: Forecast }) {
  return <section aria-label={title}>
    <h3>{title}</h3>
    <p>{result.applicable ? result.pe === null ? '缺少可比較的收盤價' : `${number(result.pe)} 倍` : `不適用：${result.reason}`}</p>
    {result.inertia && <>
      <p>g1 / g2 / g3：{result.inertia.rates.map(v => `${number(v)}%`).join(' / ')}</p>
      <p>速度 {number(result.inertia.velocity)} 個百分點；加速度 {number(result.inertia.acceleration)} 個百分點；下一期年增率 {number(result.inertia.nextGrowth)}%</p>
      {result.inertia.excessive && <p className="fin-warn">外推幅度過大</p>}
    </>}
    {result.applicable && <p>預估基期 EPS {number(result.base.eps!)}（{result.base.end}）；預估下一期 EPS {number(result.projectedEps)}；本益比分母 EPS {number(result.denominator)}</p>}
    {result.inputs && <details><summary>驗算用 EPS</summary><ul>{result.inputs.map(row => <li key={row.end}>{row.end}：{row.eps === null ? '—' : number(row.eps)}</li>)}</ul></details>}
  </section>;
}

export function ConservativePeCard({ history }: { history: FinancialHistory }) {
  const [loaded, setLoaded] = useState<{ symbol: string; price: ReturnType<typeof conservativePeClose> } | null>(null);
  const annual = useMemo(() => conservativeEps(history, 'annual'), [history]);
  const quarterly = useMemo(() => conservativeEps(history, 'quarterly'), [history]);
  const eligibility = useMemo(() => assessEligibility(annual), [annual]);
  const scenarioBaseline = useMemo(() => conservativePeScenarioBaseline(history), [history]);
  const symbol = history.symbol;
  const priceIssue = conservativePePriceIssue(history);
  useEffect(() => {
    if (!eligibility.applicable || priceIssue) return;
    let active = true;
    void import('../../app/dataSources').then(({ getDailyBarResult }) => getDailyBarResult(symbol)).then(result => {
      if (active) setLoaded({ symbol, price: conservativePeClose(symbol, result) });
    }).catch(() => { if (active) setLoaded({ symbol, price: null }); });
    return () => { active = false; };
  }, [symbol, eligibility.applicable, priceIssue]);
  const price = loaded?.symbol === symbol ? loaded.price : null;
  const priceValue = priceIssue ? null : price?.value ?? null;
  return <FoldCard title="保守本益比" summary={eligibility.applicable ? '符合適用條件' : '不適用'} dataAttributes={{ 'data-group': 'conservative-pe' }}>
    {eligibility.applicable ? <>
      <p>適用：最近 {eligibility.years} 年 EPS 均為正；{eligibility.stableGrowth ? '穩定成長' : '穩定波動'}{eligibility.stableGrowth && eligibility.stableRange ? '、穩定波動' : ''}（成長比例 {number(eligibility.growthShare * 100)}%，平均 EPS {number(eligibility.mean)}）。</p>
      <p>{priceIssue ?? (price ? `估值參考（日 K 收盤）：${price.currency} ${number(price.value)} · ${price.date} · ${price.source}${price.stale ? '（過期快取）' : ''}` : loaded?.symbol === symbol ? '找不到可用的日 K 收盤價' : '載入日 K 收盤價')}</p>
      <h3>方法一：盈餘慣性</h3>
      <ForecastDetail title="季度慣性本益比" result={forecastInertia(quarterly, 'quarterly', priceValue)} />
      <ForecastDetail title="年度慣性本益比" result={forecastInertia(annual, 'annual', priceValue)} />
    </> : <p>不適用：{eligibility.reason}</p>}
    <h3>方法二：市場擔憂情境</h3>
    <ConservativePeAiPanel symbol={symbol} baseline={scenarioBaseline} price={priceIssue ? null : price} />
    <p>盈餘慣性為數學外推，不保證為最差情況；僅供研究參考。</p>
  </FoldCard>;
}
