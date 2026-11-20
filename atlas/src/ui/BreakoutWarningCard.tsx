import { useEffect, useId, useState } from 'react';
import {
  loadBreakoutWarning, type BreakoutWarning, type BreakoutWarningMarket, type BreakoutWarningMethod,
  type BreakoutWarningResult, type BreakoutWarningRow,
} from '../report/breakoutWarning';
import { useCompactLayout } from './useCompactLayout';
import './breakoutWarning.css';

const ORDER = ['swing', 'ema20'] as const;
const pct = (value: number | null, digits = 1) => value === null ? '資料不足' : `${(value * 100).toFixed(digits)}%`;
const rank = (value: number | null) => value === null ? '資料不足' : `P${value < 1 ? value.toFixed(1) : Math.floor(value)}`;
const LEFT = 4, RIGHT = 596, SPAN = RIGHT - LEFT;
const xAt = (index: number, length: number) => LEFT + index * SPAN / Math.max(length - 1, 1);

function linePath(values: readonly (number | null)[], y: (v: number) => number): string {
  let open = false;
  return values.map((v, i) => {
    if (v === null || !Number.isFinite(v)) { open = false; return ''; }
    const part = `${open ? 'L' : 'M'}${xAt(i, values.length).toFixed(1)},${y(v).toFixed(1)}`;
    open = true;
    return part;
  }).join(' ');
}

/** Contiguous low runs as [start, end] index pairs, so the chart draws a few rectangles instead of one per day. */
function lowRuns(flags: readonly boolean[]): [number, number][] {
  const runs: [number, number][] = [];
  flags.forEach((on, i) => {
    if (!on) return;
    const last = runs.at(-1);
    if (last && last[1] === i - 1) last[1] = i; else runs.push([i, i]);
  });
  return runs;
}

function Shade({ flags, top, height }: { flags: readonly boolean[]; top: number; height: number }) {
  const step = SPAN / Math.max(flags.length - 1, 1);
  return <>{lowRuns(flags).map(([a, b]) => <rect key={a} data-state="low" x={xAt(a, flags.length) - step / 2} y={top}
    width={(b - a + 1) * step} height={height} fill="#e4585833" />)}</>;
}

function IndexChart({ market, methods }: { market: BreakoutWarningMarket; methods: [string, BreakoutWarningMethod][] }) {
  const lowDates = new Set(methods.flatMap(([, m]) => m.rows.filter(r => r.low).map(r => r.date)));
  const logs = market.closes.map(c => Math.log(c.close));
  const lo = Math.min(...logs), hi = Math.max(...logs);
  const y = (v: number) => 104 - 92 * (v - lo) / (hi - lo || 1);
  return <svg className="bw-chart" viewBox="0 0 600 110" preserveAspectRatio="none" role="img"
    aria-label={`${market.index.name}對數走勢；紅底為任一口徑偏低提醒期間`}>
    <Shade flags={market.closes.map(c => lowDates.has(c.date))} top={6} height={100} />
    <path d={linePath(logs, y)} fill="none" stroke="#d3ddec" strokeWidth="1.8" vectorEffect="non-scaling-stroke" />
  </svg>;
}

function RankChart({ rows, limit, label }: { rows: BreakoutWarningRow[]; limit: number; label: string }) {
  const y = (v: number) => 58 - v * 0.52;
  return <svg className="bw-chart bw-rank" viewBox="0 0 600 62" preserveAspectRatio="none" role="img"
    aria-label={`${label}達成率的近兩年分位；虛線為 ${limit} 分位門檻，紅底為偏低提醒`}>
    <Shade flags={rows.map(r => r.low)} top={4} height={54} />
    <line x1={LEFT} x2={RIGHT} y1={y(limit)} y2={y(limit)} stroke="#e45858" strokeDasharray="6 5" strokeWidth="1.2" vectorEffect="non-scaling-stroke" />
    <path d={linePath(rows.map(r => r.pct), y)} fill="none" stroke="#86b7eb" strokeWidth="1.8" vectorEffect="non-scaling-stroke" />
  </svg>;
}

function MethodBlock({ method, data }: { method: BreakoutWarningMethod; data: BreakoutWarning }) {
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
    <RankChart rows={method.rows} limit={p.lowPercentile} label={method.label} />
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
      <IndexChart market={market} methods={methods} />
      <p className="bw-legend">{market.index.name}（對數）· 紅底：任一口徑偏低提醒 · {market.closes[0]!.date} 起</p>
      {methods.map(([key, m]) => <MethodBlock key={key} method={m} data={data} />)}
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
