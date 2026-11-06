import { useEffect, useState } from 'react';
import type { MarketWarnings } from '../report/schema';
import { loadMarketWarnings, weeklySample, type WarningsResult } from '../report/marketWarnings';
import { TimeChart, type ChartLine } from './rates/TimeChart';
import type { TimeBand } from './rates/TimeBands';
import { ResearchNote } from './research/Common';
import './marketWarnings.css';

const ratio = (value: number | null | undefined) => value == null ? '資料不足' : `${(value * 100).toFixed(1)}%`;
const percent = (value: number) => `${(value * 100).toFixed(0)}%`;
const number = (value: number) => value.toFixed(1);
const COLORS = ['#4699ef', '#35bba0', '#dfb550', '#a798e8'];
function stateBands(rows: { date: string }[], active: (i: number) => boolean, color: string): TimeBand[] {
  return rows.flatMap((row, i) => active(i) ? [{ from: row.date, to: row.date, color }] : []);
}
type AvailableMarket = Extract<MarketWarnings['markets']['tw'], { asOf: string }>;
const sourceLabel = (source: 'yahoo' | 'kline-archive' | undefined) => source === 'kline-archive' ? 'GitHub 永久封存（Yahoo 分割調整、未股利還原）' : 'Yahoo 已收盤日線';
function WarningBody({ data, market, report }: { data: MarketWarnings; market: 'tw' | 'us'; report: AvailableMarket }) {
  const params = data.params, cfg = params.markets[market];
  const [selected, setSelected] = useState(cfg.indices[0]!);
  const [window, setWindow] = useState<5 | 10 | 20 | 60>(params.defaultWindow);
  const index = report.indices[selected]!, latest = report.latest;
  const grey = (symbol: string): TimeBand[] => {
    const rows = report.indices[symbol]!.series, first = rows[0]!.date, last = rows.at(-1)!.date;
    return report.indices[symbol]!.corrections.filter(band => band.end >= first && band.start <= last)
      .map(band => ({ from: band.start < first ? first : band.start, to: band.end > last ? last : band.end, color: '#919bad23' }));
  };
  const chosen = weeklySample(index.series, window <= 10);
  const ratioLine = (id: string, label: string, color: string, dashed = false): ChartLine => ({
    id: label, color, dashed, points: index.series.map(row => ({ time: row.date,
      value: chosen.has(row.date) ? row[id as keyof typeof row] as number | null : null })),
  });
  const volumeLines = [
    ratioLine(`up_vol_up_ratio_${window}`, '上漲日量增', COLORS[0]!),
    ratioLine(`down_vol_down_ratio_${window}`, '下跌日量縮', COLORS[1]!),
    ratioLine('avg60', '60日兩比例平均', COLORS[2]!),
    ratioLine('median756', '過去中位數', COLORS[3]!, true),
  ];
  const marketLine = (key: 'leader_success' | 'market_success' | 'breakout_count_per_1000' | 'count_p30', id: string, color: string, dashed = false): ChartLine => ({
    id, color, dashed, points: report.series.map(row => ({ time: row.date, value: row[key] })),
  });
  const lights = [
    { label: '指數價量偏弱', value: latest.vol60_weak, na: market === 'us' },
    { label: '領頭羊突破受阻', value: latest.leader_lag, na: false },
    { label: '突破家數背離', value: latest.breadth_divergence, na: false },
  ];
  const unknown = lights.filter(light => !light.na && light.value === null).length;
  return <>
    {report.update?.status === 'retained' && <p className="mw-update-note" role="status">本市場未更新，保留 {report.asOf} 的已發布資料。原因：{report.update.reason}</p>}
    <div className="mw-score"><b>{latest.warning_level} <small>項亮燈</small></b><span>{unknown ? `${unknown} 項資料不足` : latest.warning_level ? '風險升高' : '目前未亮燈'}</span><time>{report.asOf}</time></div>
    <p className="mw-purpose">不是買賣訊號；回測顯示無法預測修正時點，但亮燈後下一次修正較深的比例較高</p>
    <ul className="mw-lights">{lights.map(light => <li key={light.label} data-state={light.na || light.value === null ? 'na' : light.value ? 'warning' : 'off'}>
      <i aria-hidden="true" /><span>{light.label}</span><b>{light.na ? '不適用（無歷史驗證）' : light.value === null ? '資料不足' : light.value ? '警示' : '未亮燈'}</b>
    </li>)}</ul>
    <div className="mw-chart-block">
      <div className="mw-tools"><label>指數<select aria-label="警示指數" value={selected} onChange={event => setSelected(event.target.value)}>{cfg.indices.map(symbol => <option key={symbol}>{symbol}</option>)}</select></label>
        <label>窗口<select aria-label="警示價量窗口" value={window} onChange={event => setWindow(Number(event.target.value) as typeof window)}>{params.windows.map(value => <option key={value} value={value}>{value} 日</option>)}</select></label></div>
      <h3>指數價量 · {window} 日{window <= 10 ? '（每週取一次）' : ''}</h3>
      <p className="source-note">{index.validated ? '僅台股有歷史參考價值' : '此指數無歷史驗證'}{index.proxy ? ` · 成交量代理 ${index.proxy}` : ''} · {sourceLabel(index.meta?.price.source)}{index.proxy && index.meta?.volume ? ` · 量來源：${sourceLabel(index.meta.volume.source)}` : ''}</p>
      <dl className="mw-values"><div><dt>上漲日量增</dt><dd>{ratio(index.latest[`up_vol_up_ratio_${window}`])}</dd></div><div><dt>下跌日量縮</dt><dd>{ratio(index.latest[`down_vol_down_ratio_${window}`])}</dd></div><div><dt>avg60／過去中位數</dt><dd>{ratio(index.latest.avg60)}／{ratio(index.latest.median756)}</dd></div></dl>
      <TimeChart lines={volumeLines} format={percent} zoom className="mw-plot" ariaLabel={`${selected} 指數價量比例圖`}
        bands={[...grey(selected), ...stateBands(index.series, i => index.series[i]!.vol60_weak === true, '#e4585828')]} />
      <p className="mw-legend">藍：上漲日量增 · 綠：下跌日量縮 · 黃：60日平均 · 紫虛線：前 {params.medianWindow} 日中位數；紅底：平均低於中位數 {params.volumeGap * 100} 點</p>
    </div>
    <div className="mw-chart-block"><h3>領頭羊 vs 全市場突破成功率</h3>
      <dl className="mw-values"><div><dt>領頭羊</dt><dd>{ratio(latest.leader_success)}</dd></div><div><dt>全市場</dt><dd>{ratio(latest.market_success)}</dd></div></dl>
      <TimeChart lines={[marketLine('leader_success', '領頭羊', COLORS[0]!), marketLine('market_success', '全市場', COLORS[1]!)]}
        format={percent} zoom className="mw-plot" ariaLabel="領頭羊與全市場突破成功率圖"
        bands={[...grey(cfg.indices[0]!), ...stateBands(report.series, i => report.series[i]!.leader_lag === true, '#e4585828')]} />
      <p className="mw-legend">藍：領頭羊 · 綠：全市場；上漲走勢且領頭羊落後 {params.leaderGap * 100} 點以上塗色。{cfg.successWindow} 日成熟窗口、{params.smoothDays} 日平均；少於 {params.minBreakouts} 次留空。</p>
    </div>
    <div className="mw-chart-block"><h3>突破家數 · 每千檔</h3><p className="mw-latest">{latest.breakout_count_per_1000 === null ? '資料不足' : number(latest.breakout_count_per_1000)}<small>／近 {params.countWindow} 日</small></p>
      <TimeChart lines={[marketLine('breakout_count_per_1000', '突破家數', COLORS[0]!), marketLine('count_p30', '歷史分位', COLORS[3]!, true)]}
        format={number} zoom className="mw-plot" ariaLabel="每千檔突破家數圖" bands={grey(cfg.indices[0]!)} />
      <p className="mw-legend">藍：突破家數 · 紫虛線：過去 {params.quantileWindow} 日的 {params.countQuantile * 100}% 分位（至少 {params.quantileMinValid} 個有效值）</p>
    </div>
    <p className="mw-legend">所有圖表灰底：收盤高點至低點跌幅 ≥{percent(params.correctionDrop)} 的修正段（zigzag 事後標註，未完成段會變動）；僅供回顧，不參與燈號。</p>
    <ResearchNote title="計算說明">
      <p>每個高點只算第一次、只算更高的高點；收盤 ≥ 關鍵點＋{params.closeBufferAtr} ATR({params.atrPeriod})，再觀察完整 {params.observationDays} 個交易日，最低價皆大於關鍵點 × (1−{params.barrier}) 才成功。重試、較低高點、收盤沒站上另列，不進成功率分母。</p>
      <p>{params.leaderReturnDays} 日報酬、前 {params.liquidityDays} 日平均成交值；{params.leaderMembership}。{params.countDenominator}。{params.firstPivot}。</p>
      <p>領頭羊：平均成交值（收盤價 × 股數代理）至少 {cfg.minAmount.toLocaleString('zh-TW')} {market === 'tw' ? 'TWD' : 'USD'}、前日股價至少 {cfg.minPrice}，取報酬前 {percent(cfg.leaderShare)}。已知零量計為零，未知量保持缺值。</p>
      <p>所有中位數與分位數只用當日以前資料；只採已收盤日線，不跨無效日比較。上漲走勢：收盤高於 {params.trendDays} 日均線且均線高於 {params.trendSlopeDays} 日前。近 {params.highWindow} 日高點 × {params.nearHigh} 以上才檢查家數背離。</p>
      <p>指數優先採 Yahoo 已收盤日線，下載不足時以 GitHub 永久封存備援；兩者均為分割調整、未作股利還原，實際來源見各指數標示。個股使用 universe-full 的完整名單與日線快取。以下研究結論為使用者提供的原文，本次未重新驗證完整 21 年研究。</p>
      <p>{params.diagnosticDenominators}</p>
      {report.limitations.map(text => <p key={text}>{text}</p>)}
      {data.evidence.map(text => <p className="mw-evidence" key={text}>{text}</p>)}
      <details><summary>完整門檻設定</summary><pre>{JSON.stringify(params, null, 2)}</pre></details>
      <p>資料日 {report.asOf} · 計算時間 {report.update?.generatedAt ?? data.generatedAt} · 有效 {report.evaluated}／{report.universeCount} 檔 · 名單日 {report.listingAsOf}</p>
    </ResearchNote>
  </>;
}
export function MarketWarningCard({ market }: { market: 'tw' | 'us' }) {
  const [retry, setRetry] = useState(0);
  const [loaded, setLoaded] = useState<{ retry: number; result: WarningsResult }>();
  useEffect(() => {
    const controller = new AbortController();
    void loadMarketWarnings(controller.signal).then(result => { if (!controller.signal.aborted) setLoaded({ retry, result }); });
    return () => controller.abort();
  }, [retry]);
  const result = loaded?.retry === retry ? loaded.result : undefined;
  const report = result?.data?.markets[market];
  return <section className="card market-warning-card" aria-label="多頭末期警示"><h2>多頭末期警示 <small>{market === 'tw' ? '台股' : '美股'}</small></h2>
    {result?.data && report ? 'asOf' in report ? <WarningBody key={market} data={result.data} market={market} report={report} /> : <div role="status">
      <p className="mw-update-note">本市場未更新：{report.update.reason}。尚無可保留的已發布資料。</p>
      <button type="button" onClick={() => setRetry(value => value + 1)}>重試警示資料</button>
    </div> : <div role="status"><p>{result?.error ?? '載入警示資料…'}</p>
      {result?.error && <button type="button" onClick={() => setRetry(value => value + 1)}>重試警示資料</button>}</div>}
  </section>;
}
