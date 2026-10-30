import { useEffect, useMemo, useState } from 'react';
import { japanRatesLoader, japanSpread, type JapanRates } from '../report/bondResearch';
import type { CachedResult } from '../report/rates';
import { suppliedJapanQuote } from '../rates/yieldBand';
import { valueOnOrBefore, windowStartIndex } from '../rates/ratesView';
import { PageState } from '../ui/PageState';
import { YieldBandCard } from '../ui/rates/YieldBandCard';
import { RatesKeyNumbers, monthBefore } from '../ui/rates/KeyNumbers';
import { TimeChart, type ChartLine } from '../ui/rates/TimeChart';
import { Segmented, fmtYield, slashDate } from '../ui/rates/shared';
import '../ui/rates.css';

export default function JapanRatesView() {
  const [loaded, setLoaded] = useState<CachedResult<JapanRates> | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [range, setRange] = useState<'1y' | '5y' | 'all'>('5y');
  useEffect(() => {
    let live = true;
    void japanRatesLoader.load(attempt > 0).then(value => { if (live) setLoaded(value); });
    return () => { live = false; };
  }, [attempt]);
  const data = loaded?.status === 'ok' ? loaded.data : null;
  const lines = useMemo((): ChartLine[] => {
    if (!data) return [];
    const block = range === '1y' ? data.daily : data.weekly;
    const start = range === 'all' ? 0 : windowStartIndex(block.dates, range === '1y' ? 1 : 5);
    return [{ id: 'jp10yboj', color: '#3d8bfd', kind: 'baseline', points: japanSpread(block).slice(start) }];
  }, [data, range]);
  if (!loaded) return <PageState kind="loading" title="載入日本國債利差…" />;
  const value = data?.tenYear.value ?? suppliedJapanQuote.value;
  const policy = data?.policy.value ?? suppliedJapanQuote.policy;
  const asOf = data?.asOf ?? suppliedJapanQuote.asOf;
  const old = (Date.now() - Date.parse(`${asOf}T00:00:00Z`)) / 86400000 > 6;
  const spread = value - policy;
  const past = monthBefore(asOf);
  const tenThen = data ? valueOnOrBefore(data.daily, 'JGB10', past) : null;
  const policyThen = data ? valueOnOrBefore(data.daily, 'BOJ_TARGET', past) : null;
  const spreadChange = tenThen && policyThen && tenThen.date === policyThen.date
    ? (spread - (tenThen.value - policyThen.value)) * 100 : null;
  return <div className="rates-view japan-rates-view">
    {!data && <div className="rates-alert" role="status"><span>{loaded.status !== 'ok' ? loaded.message : ''} 以下為使用者附檔的 {slashDate(asOf)} 基準快照，並非即時行情。</span><button type="button" className="ghost-button" onClick={() => setAttempt(v => v + 1)}>重試</button></div>}
    {data && ((loaded.status === 'ok' && loaded.stale) || old || data.missing.length > 0) && <p className="rates-alert" role="status">日本國債資料部分未更新，顯示最後可用資料：{slashDate(asOf)}。</p>}
    <p className="rates-meta">資料日 {slashDate(asOf)} · {data ? '日本財務省國債利率／日銀政策目標' : '使用者提供附檔'}</p>
    <RatesKeyNumbers items={[
      { key: '10y', label: '10年期', value: fmtYield(value), change: tenThen ? (value - tenThen.value) * 100 : null },
      { key: '10yboj', label: '10Y−BOJ', value: fmtYield(spread), change: spreadChange, badge: spread < 0 ? '倒掛' : null },
      { key: 'boj', label: '日銀目標區間', value: fmtYield(policy), change: policyThen ? (policy - policyThen.value) * 100 : null, title: '日銀目前公布單一利率目標，未提供上下限區間' },
    ]} />
    <div className="rates-grid">
      <YieldBandCard country="JP" policy={policy} value={value} date={asOf} />
      <section className="card rates-card rates-wide" aria-label="日本國債利差">
        <header className="card-head"><h2>利差<small>日本 10 年殖利率 − 日銀政策目標（10Y−BOJ）</small></h2>
          <Segmented label="日本利差區間" value={range} onChange={setRange} options={[{ value: '1y', label: '1年' }, { value: '5y', label: '5年' }, { value: 'all', label: '全部' }]} />
        </header>
        <div className="rates-card-body">
          <div className="rates-headline"><b>{spread > 0 ? '+' : ''}{spread.toFixed(2)}%</b><span className="rates-muted">10Y {fmtYield(value)} − BOJ {fmtYield(policy)}</span></div>
          {lines.some(line => line.points.length > 0) ? <div className="rates-chart-wrap"><TimeChart lines={lines} zeroLine format={v => `${v.toFixed(2)}%`} ariaLabel="日本10Y−BOJ利差走勢（%）" /></div> : <p role="status">歷史利差尚未產生；每日資料工作完成後即可顯示。</p>}
          <details className="rates-disclosure"><summary>日本利差來源與解讀</summary><div className="rates-disclosure-body">
            <p>單位為百分點；低於 0 代表倒掛。日銀目前公布單一利率目標，因此摘要顯示單一數值。日利率取日本財務省每日 10 年國債殖利率，政策歷史依附件日期表，近次變動已按日銀公告生效日對齊；全部歷史自 2006 年有明確數值目標後開始。</p>
            {data && <p>政策目標生效日 {slashDate(data.policy.date)}。<a href="https://www.mof.go.jp/jgbs/reference/interest_rate/index.htm" target="_blank" rel="noreferrer">日本財務省</a> · <a href="https://www.boj.or.jp/en/mopo/mpmdeci/state_2026/index.htm" target="_blank" rel="noreferrer">日本銀行</a>。</p>}
          </div></details>
        </div>
      </section>
    </div>
  </div>;
}
