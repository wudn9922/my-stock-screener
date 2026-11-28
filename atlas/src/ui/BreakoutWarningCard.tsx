import { useEffect, useId, useState } from 'react';
import {
  loadBreakoutWarning, type BreakoutWarning, type BreakoutWarningMarket, type BreakoutWarningMethod,
  type BreakoutWarningResult,
} from '../report/breakoutWarning';
import { useCompactLayout } from './useCompactLayout';
import { TimeChart, type ChartLine, type ChartScale } from './rates/TimeChart';
import './breakoutWarning.css';

const ORDER = ['swing', 'ema20'] as const;
const pct = (value: number | null, digits = 1) => value === null ? '資料不足' : `${(value * 100).toFixed(digits)}%`;
const rank = (value: number | null) => value === null ? '資料不足' : `P${value < 1 ? value.toFixed(1) : Math.floor(value)}`;
const indexFormat = (value: number) => value.toLocaleString('zh-TW', { maximumFractionDigits: 0 });
const rankFormat = (value: number) => 'P' + value.toFixed(0);
const indexScale: ChartScale = { format: indexFormat, logarithmic: true };
const percentileScale: ChartScale = { format: rankFormat, range: { min: 0, max: 100 } };

function CombinedChart({ market, method, limit }: { market: BreakoutWarningMarket; method: BreakoutWarningMethod; limit: number }) {
  const lines: ChartLine[] = [
    { id: '指數', scale: 'left', color: '#d3ddec', points: market.closes.map(row => ({ time: row.date, value: row.close })) },
    { id: '分位', scale: 'right', color: '#86b7eb', points: method.rows.map(row => ({ time: row.date, value: row.pct })) },
    { id: 'P' + limit + ' 門檻', scale: 'right', color: '#e45858', dashed: true, width: 1, points: method.rows.map(row => ({ time: row.date, value: limit })) },
  ];
  const bands = method.rows.filter(row => row.low).map(row => ({ from: row.date, to: row.date, color: '#e4585833' }));
  return <>
    <TimeChart lines={lines} format={rankFormat} leftScale={indexScale} rightScale={percentileScale} bands={bands} zoom
      className="bw-chart bw-combined" ariaLabel={market.index.name + '與' + method.label + '達成率分位合併圖'} />
    <p className="bw-legend">左：{market.index.name}（對數） · 右：達成率近兩年分位（0–100） · P{limit} 虛線 · 紅底：此口徑偏低提醒</p>
  </>;
}

function MethodBlock({ method, data, market }: { method: BreakoutWarningMethod; data: BreakoutWarning; market: BreakoutWarningMarket }) {
  const { latest, stats } = method, p = data.params;
  const done = stats.episodes;
  return <div className="bw-method" data-state={latest.low ? 'low' : 'normal'}>
    <div className="bw-method-head">
      <h3>{method.label}</h3>
      <span className="bw-label">{latest.low ? `▼ 偏低提醒 · 已連續 ${latest.lowDays} 個交易日` : '● 未偏低'}</span>
    </div>
    <dl className="bw-values">
      <div><dt>近期達成率</dt><dd>{pct(latest.rate)}<small>（{latest.n.toLocaleString('zh-TW')} 筆）</small></dd></div>
      <div><dt>近兩年分位</dt><dd>{rank(latest.pct)}<small>（≤ P{p.lowPercentile} 提醒）</small></dd></div>
    </dl>
    <CombinedChart market={market} method={method} limit={p.lowPercentile} />
    <p className="bw-history">
      {done ? <>過去 {done} 次提醒，{p.horizonDays} 個交易日內大盤跌 {Math.round(p.drawdown * 100)}% 以上 <b>{stats.hits} 次</b>
        （任一天約 {pct(stats.baseHitRate, 0)}）；平均最大跌幅 {pct(stats.avgDrawdown)}（任一天 {pct(stats.baseAvgDrawdown)}）。</>
        : '尚無已完成的歷史提醒。'}
      {stats.pending ? ` 另有 ${stats.pending} 次尚在觀察中。` : ''}
    </p>
    {stats.list.length > 0 && <details className="bw-episodes"><summary>歷次提醒</summary>
      <table><thead><tr><th>開始日</th><th>達成率</th><th>分位</th><th>{p.horizonDays} 日最大跌幅</th></tr></thead><tbody>
        {[...stats.list].reverse().map(e => <tr key={e.date} data-hit={e.hit === null ? 'pending' : String(e.hit)}>
          <td>{e.date}</td><td>{pct(e.rate)}</td><td>{rank(e.pct)}</td><td>{e.drawdown === null ? '觀察中' : pct(e.drawdown)}</td>
        </tr>)}
      </tbody></table></details>}
  </div>;
}

export function WarningBody({ data, market }: { data: BreakoutWarning; market: BreakoutWarningMarket }) {
  const methods = ORDER.flatMap(key => market.methods[key] ? [[key, market.methods[key]] as [string, BreakoutWarningMethod]] : []);
  const lowCount = methods.filter(([, m]) => m.latest.low).length;
  const compact = useCompactLayout();
  const [open, setOpen] = useState(false);
  const detailId = useId();
  const p = data.params;
  return <>
    <button type="button" className="bw-summary" aria-expanded={open} aria-controls={!compact || open ? detailId : undefined} onClick={() => setOpen(v => !v)}>
      <span className="bw-summary-text"><b>突破達成率警示</b><small>{methods.map(([, m]) => m.label).join('、')}</small></span>
      <span className="bw-summary-count"><b>{lowCount}</b><small>／{methods.length} 項偏低</small></span>
    </button>
    {(!compact || open) && <div className="bw-detail" id={detailId}>
      <div className="bw-score"><b>{lowCount ? '風險升高' : '未偏低'}</b><span>{lowCount}／{methods.length} 項偏低</span><time dateTime={market.asOf}>{market.asOf}</time></div>
      <p className="bw-purpose"><b>不是賣出訊號。</b>全市場突破的成功率跌到自己近兩年最低一成時，之後一段時間大盤出現較深回檔的比例較高；但回測中提醒之後的報酬並沒有比較差。</p>
      {methods.map(([key, m]) => <MethodBlock key={key} method={m} data={data} market={market} />)}
      <div className="bw-notes">
        {market.note && <p>{market.note}</p>}
        <p>達成率：訊號日落在參考日前 {p.windowDays - 1}～{p.observationDays} 個交易日、已滿 {p.observationDays} 日的突破，{p.observationDays} 日內最低價都守住才算成功（與突破卡片「固定五日守住」相同）；少於 {p.minSignals} 筆留空。分位：與自己過去 {p.percentileWindow} 個交易日相比（至少 {p.percentileMinValid} 個有效值），只用當日以前資料。</p>
        <p>提醒間隔不到 {p.episodeGapDays} 個交易日的算同一次；跌幅以提醒當天收盤為基準，看之後 {p.horizonDays} 個交易日的最低價。</p>
        <p>樣本少（2021 年起），提醒多集中在 2022 年空頭；個股名單為目前仍在封存中的股票，有存活者偏差。資料來自 GitHub 永久 K 線封存，通常比當日行情晚一個交易日。有效 {market.stocks.toLocaleString('zh-TW')} 檔{market.failedStocks ? `，${market.failedStocks} 檔讀取失敗` : ''}。</p>
      </div>
    </div>}
  </>;
}

export function BreakoutWarningCard({ market }: { market: 'tw' | 'us' }) {
  const [result, setResult] = useState<BreakoutWarningResult>();
  useEffect(() => {
    const controller = new AbortController();
    void loadBreakoutWarning(controller.signal).then(value => { if (!controller.signal.aborted) setResult(value); });
    return () => controller.abort();
  }, []);
  const report = result?.data?.markets[market];
  const ready = !!result?.data && !!report && Object.keys(report.methods).length > 0;
  return <section className="card breakout-warning-card" aria-label="突破達成率警示" data-testid="breakout-warning-card" data-ready={ready ? '' : undefined}>
    <h2>突破達成率警示 <small>{market === 'tw' ? '台股' : '美股'}</small></h2>
    {ready ? <WarningBody key={market} data={result.data!} market={report} />
      : <p className="muted-text" role="status">{result?.error ?? (result ? '此市場的突破達成率警示尚未產生。' : '正在載入突破達成率警示…')}</p>}
  </section>;
}
