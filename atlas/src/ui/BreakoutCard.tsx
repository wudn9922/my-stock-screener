import { useEffect, useState } from 'react';
import { ArrowUpRight, GitBranch, Layers, Rocket, ShieldCheck, Target, Timer } from 'lucide-react';
import {
  DEFAULT_BREAKOUT_PARAMS, OUTCOME_RULES, SUCCESS_MODEL, loadBreakouts, loadBreakoutsHistory, fetchBreakoutSnapshot,
  type BreakoutMarket, type BreakoutMarketKey, type BreakoutMonthlyPoint,
  type BreakoutParams, type BreakoutStats, type BreakoutWindow, type BreakoutsLoadResult, type BreakoutsHistoryResult, type OutcomeKey,
} from '../report/breakouts';
import { formatDateTime } from './format';
import { BreakoutHistoryPlot } from './BreakoutHistoryPlot';
import './breakouts.css';

const MARKET_NAMES: Record<BreakoutMarketKey, string> = { tw: '台股', us: '美股' };
const METHODS = [
  { key: 'swingHigh', label: '突破波段前高進場法', short: '波段前高', hint: '確認波段後，盤中越過前高' },
  { key: 'box20', label: '箱型突破進場法', short: '箱型突破', hint: '整理區間收斂後，收盤突破箱頂' },
  { key: 'ema20', label: '突破20EMA進場法', short: '20EMA', hint: '收盤由下向上穿越20EMA' },
];
const RULE_ICONS = [ShieldCheck, ArrowUpRight, GitBranch, Target];
const count = (value: number) => value.toLocaleString('zh-TW');
const rate = (value: number | null) => value === null ? '—' : `${value.toFixed(1)}%`;

function OutcomeSelector({ selected, onSelect }: { selected: OutcomeKey; onSelect: (key: OutcomeKey) => void }) {
  return <div className="bo-rule-picker" role="group" aria-label="勝率計算方式">
    {OUTCOME_RULES.map((rule, index) => {
      const Icon = RULE_ICONS[index]!;
      return <button type="button" key={rule.key} className={`bo-rule-choice${selected === rule.key ? ' active' : ''}`}
        aria-pressed={selected === rule.key} onClick={() => onSelect(rule.key)}>
        <Icon size={17} aria-hidden="true" />
        <span><small>口徑 0{index + 1}</small><b>{rule.label}</b></span>
        <i aria-hidden="true" />
      </button>;
    })}
  </div>;
}

function StrategyRow({ method, stats, rule, params, index, points, months, asOf }: {
  method: typeof METHODS[number]; stats: BreakoutStats | undefined; rule: OutcomeKey; params: BreakoutParams; index: number; points: BreakoutMonthlyPoint[]; months: number; asOf: string | null;
}) {
  const comparison = stats?.comparison;
  const outcome = comparison?.rules[rule];
  const low = outcome && outcome.evaluatedN > 0 && outcome.evaluatedN < params.minSample;
  const note = !stats ? '報表尚未提供此方法' : !comparison || !outcome ? '此口徑資料尚未提供' :
    !comparison.n ? '窗口內沒有新突破' : !comparison.completeN ? comparison.pendingN ? '等待5日觀察' : '後續日線不足' : null;
  return <li className={`bo-row${low ? ' low' : ''}`} data-method={method.key}>
    <div className="bo-row-head">
      <div className="bo-method-name"><span className="bo-method-index">0{index + 1}</span><div>
        <h3 className="bo-label">{method.label}</h3><details className="bo-inline-help"><summary>方法說明</summary><p>{method.hint}</p></details>
      </div></div>
      <b className="bo-win" aria-label={`進場成功率 ${note ? '尚無完整結果' : rate(outcome?.winRate ?? null)}`}>
        {note ? '—' : rate(outcome?.winRate ?? null)}<small>{asOf ? formatDateTime(asOf, false) : '資料日'} 測得</small>
      </b>
    </div>
    <BreakoutHistoryPlot points={points} method={method.key} rule={rule} months={months} />
    {note ? <p className="bo-note" role="status">{note}</p> : <p className="bo-ratio">
      <strong>{count(outcome!.wins)}</strong>／{count(outcome!.evaluatedN)} <span>成功／完整訊號樣本</span>
      {low ? <span className="bo-tag">樣本偏少</span> : null}
    </p>}
    {comparison ? <>
      <dl className="bo-metrics">
        <div><dt>全部訊號</dt><dd>{count(comparison.n)}</dd></div>
        <div><dt>等待觀察</dt><dd>{count(comparison.pendingN)}</dd></div>
        <div><dt>後續缺日</dt><dd>{count(comparison.missingN)}</dd></div>
      </dl>
      <div className="bo-row-foot"><span>完整樣本中，突破日收盤即失敗 <b>{count(comparison.commonFailedN)}</b> 次</span>
        {rule === 'firstHit' && comparison.sameBarFailedN > 0 ? <span title="同根日K無法確認先後，保守判失敗">先後不明 {count(comparison.sameBarFailedN)} 次</span> : null}
      </div>
    </> : null}
  </li>;
}

function ComparisonTable({ current, selected, onSelect }: { current: BreakoutWindow; selected: OutcomeKey; onSelect: (key: OutcomeKey) => void }) {
  return <details className="bo-details bo-compare"><summary><Layers size={14} aria-hidden="true" />比較全部四種勝率</summary>
    <div className="bo-table-wrap"><table><caption>同一批完整5日訊號，各口徑分開判定</caption>
      <thead><tr><th scope="col">進場法</th>{OUTCOME_RULES.map(rule => <th key={rule.key} scope="col">
        <button type="button" aria-pressed={selected === rule.key} onClick={() => onSelect(rule.key)}>{rule.short}</button>
      </th>)}</tr></thead>
      <tbody>{METHODS.map(method => <tr key={method.key}><th scope="row">{method.short}</th>{OUTCOME_RULES.map(rule => {
        const outcome = current.strategies[method.key]?.comparison?.rules[rule.key];
        return <td key={rule.key} className={selected === rule.key ? 'selected' : ''}>{rate(outcome?.winRate ?? null)}
          <small>{outcome ? `${count(outcome.wins)}／${count(outcome.evaluatedN)}` : '未提供'}</small></td>;
      })}</tr>)}</tbody>
    </table></div>
  </details>;
}

function Methodology({ data, current, params }: { data: BreakoutMarket; current: BreakoutWindow; params: BreakoutParams }) {
  return <details className="bo-details"><summary>計算與資料說明</summary><ul className="bo-method">
    <li><b>同批樣本比較</b>訊號日期落在所選窗口內，並有突破後完整 {params.observationDays} 個市場交易日（不含突破日）的日線，才進入四組的分母。
      未成熟與缺日分開列出，不當成失敗。沒有訊號的股票不進分母；成功率＝成功筆數／完整訊號樣本。
      {current.observedThrough ? ` 本次可完整觀察的訊號日期截至 ${formatDateTime(current.observedThrough, false)}。` : ''}
      各柱狀值以該參考日前21個市場交易日的訊號重新計算，不使用參考日之後的K線。</li>
    <li><b>共通失敗優先</b>突破日收盤低於關鍵點＋{params.closeBufferAtr} × Wilder ATR({params.successAtrPeriod})，四組皆失敗，仍保留在分母；等於門檻可通過。
      ATR只使用突破日收盤及之前的資料。關鍵點固定在訊號當日，後續的±{params.barrierPct}%不隨價格移動。</li>
    <li><b>先觸發判定</b>後續逐日依時間檢查：先跌到−5%失敗；先漲到＋5%或隔日先創高成功；五日都未跌到−5%也成功。
      開盤已越過條件線時，可確認該條件先成立；其餘同根日K若同時達成成功與失敗條件，先後不明則保守判失敗。已判定的結果不被後續走勢改寫。</li>
    <li><b>目標＋續強</b>五日內碰＋5%且隔日創高，兩項皆成立才成功；只共用突破日收盤失敗條件，不另加−5%否決。這些是條件成功率，未設定實際停利出場價。</li>
    <li><b>三種突破與重試</b>波段高點先有 {params.swingReversalAtr ?? 2} 倍 ATR({params.swingAtrPeriod ?? 20}) 的上升，再有同幅度收盤回落才確認；確認收盤後的下一根可使用，橫盤小轉折不取代前高。
      波段盤中最高價嚴格超過前高即記一次嘗試。收盤未過共通門檻，前高仍保留，隔日可再次突破；通過後，同一高點不重複，新波段可再次入選，不設10日冷卻。
      箱型維持20日整理區間（門檻 {data.boxMaxWidthPct ?? '—'}%）與收盤突破；20EMA維持收盤上穿。箱型與20EMA保留 {params.cooldownDays ?? 10} 日冷卻及每檔／窗口第一次新訊號。</li>
    <li><b>資料涵蓋</b>{data.universe}；全市場名單 {count(data.universeCount)} 檔，資料日有效日線 {count(data.evaluated)} 檔，歷史缺漏 {count(data.coverage?.missing ?? 0)} 檔，未成交／停更 {count(data.stale)} 檔。
      {data.listingAsOf ? ` 名單日期 ${formatDateTime(data.listingAsOf, false)}。` : ''}{data.listingSource ? ` 來源 ${data.listingSource}。` : ''}</li>
    <li><b>價格與限制</b>波段理論進場為 max(開盤價, 關鍵點)，箱型與20EMA為訊號日收盤。日線無法證明實際成交，未模擬最小跳動、滑價與交易成本。
      無成交與缺日不補價，週末K線不採用（本次 {count(data.weekendBars)} 根）；收盤跳價超過 {params.jumpLimitPct ?? 30}% 時只用最後異常日之後的資料。
      名單是目前上市股票，含存活者偏差；歷史也使用這份現有名單，並非當時上市成分。快取最多320根日線，股利未還原，結果僅供研究。歷史有效日線覆蓋未達90%或沒有完整樣本時不畫百分比。</li>
  </ul></details>;
}

function BreakoutBody({ data, params }: { data: BreakoutMarket; params: BreakoutParams }) {
  const [months, setMonths] = useState(5);
  const [selectedRule, setRule] = useState<OutcomeKey>('hold5');
  const current = data.windows['1m'];
  if (!current) return <p className="muted-text">沒有可顯示的近月統計。</p>;
  if (params.successModel !== SUCCESS_MODEL) return <p className="bo-update" role="status"><Timer size={18} aria-hidden="true" />四種勝率口徑更新中，等待新版全市場報表。舊報酬勝率不混入新口徑。</p>;
  const rule = OUTCOME_RULES.find(entry => entry.key === selectedRule)!;
  const complete = Object.values(current.strategies).some(stats => stats.comparison?.completeN);
  const points: BreakoutMonthlyPoint[] = data.monthly?.points ?? (data.asOf ? [{ targetDate: data.asOf, asOf: data.asOf,
    evaluated: data.evaluated, universeCount: data.universeCount, status: 'ok', strategies: current.strategies }] : []);
  const historicalCount = points.filter(point => point.asOf && point.asOf !== data.asOf && point.status === 'ok').length;
  return <>
    <div className="bo-toolbar"><span className="bo-overline">3 種進場法 · 每次回看21個交易日</span>
      <div className="bo-zoom" role="group" aria-label="成功率圖表時間範圍">
        <button type="button" aria-label="成功率時間放大" disabled={months === 1} onClick={() => setMonths(value => value === 0 ? 5 : Math.max(1, value - 1))}>＋</button>
        <label><span className="sr-only">顯示範圍</span><select aria-label="成功率顯示月數" value={months} onChange={event => setMonths(Number(event.target.value))}>
          {[1, 2, 3, 4, 5, 6].map(value => <option key={value} value={value}>{value}個月</option>)}<option value={0}>全部</option>
        </select></label>
        <button type="button" aria-label="成功率時間縮小" disabled={months === 0} onClick={() => setMonths(value => value >= 6 ? 0 : value + 1)}>−</button>
      </div>
    </div>
    {!data.monthly ? <p className="bo-note">每月重算結果尚未產生，目前顯示這個資料日的測試值。</p> : historicalCount < 3 ? <p className="bo-note">歷史有效日線不足，已取得 {historicalCount} 次每月結果；缺少資料的月份留空。</p> : null}
    <OutcomeSelector selected={selectedRule} onSelect={setRule} />
    <details className="bo-details" key={selectedRule}><summary>{rule.label} · 判定說明</summary>
      <div className="bo-rule-description"><p>{rule.description}</p></div>
      <div className="bo-common"><ShieldCheck size={15} aria-hidden="true" /><p><b>突破日收盤門檻，四組共用</b><span>關鍵點 ＋ 0.1 ATR(14)；未達即失敗</span></p></div>
    </details>
    {!complete ? <p className="bo-pending-note"><Timer size={14} aria-hidden="true" />這段訊號仍在觀察或缺少後續日線，尚無完整比較結果。</p> : null}
    <ul className="bo-list">{METHODS.map((method, index) => <StrategyRow key={method.key} method={method} index={index}
      stats={current.strategies[method.key]} rule={selectedRule} params={params} points={points} months={months} asOf={data.asOf} />)}</ul>
    <details className="bo-details"><summary>每月測試數值與涵蓋</summary><div className="bo-table-wrap"><table className="bo-history-table">
      <thead><tr><th>參考資料日</th>{METHODS.map(method => <th key={method.key}>{method.short}</th>)}<th>有效／名單</th></tr></thead>
      <tbody>{points.map(point => <tr key={point.targetDate}><th>{point.asOf ?? point.targetDate}</th>{METHODS.map(method => <td key={method.key}>{point.status === 'ok' ? rate(point.strategies[method.key]?.comparison?.rules[selectedRule]?.winRate ?? null) : '資料不足'}</td>)}<td>{count(point.evaluated)}／{count(point.universeCount)}</td></tr>)}</tbody>
    </table></div></details>
    <ComparisonTable current={current} selected={selectedRule} onSelect={setRule} />
    <Methodology data={data} current={current} params={params} />
    <p className="bo-foot"><i />{data.universe} · 有效日線 {count(data.evaluated)} 檔</p>
  </>;
}

export function BreakoutCard({ market }: { market: BreakoutMarketKey }) {
  const [latest, setLatest] = useState<BreakoutsLoadResult | null>(null);
  const [history, setHistory] = useState<BreakoutsHistoryResult | null>(null);
  const [selectedDate, setSelectedDate] = useState('latest');
  const [archived, setArchived] = useState<{ asOf: string; result: BreakoutsLoadResult } | null>(null);
  useEffect(() => {
    let active = true;
    void loadBreakouts().then(next => { if (active) setLatest(next); });
    void loadBreakoutsHistory().then(next => { if (active) setHistory(next); });
    return () => { active = false; };
  }, []);
  const snapshots = history?.status === 'ok' ? history.data.snapshots.filter(entry => entry.markets[market]) : [];
  const activeDate = selectedDate === 'latest' || snapshots.some(entry => entry.asOf === selectedDate) ? selectedDate : 'latest';
  useEffect(() => {
    if (activeDate === 'latest') return;
    const controller = new AbortController();
    void fetchBreakoutSnapshot(activeDate, latest?.url, fetch, controller.signal).then(result => {
      if (!controller.signal.aborted) setArchived({ asOf: activeDate, result });
    });
    return () => controller.abort();
  }, [activeDate, latest?.url]);
  const result = activeDate === 'latest' ? latest : archived?.asOf === activeDate ? archived.result : null;
  const data = result?.status === 'ok' ? result.data.markets[market] : undefined;
  const params = result?.status === 'ok' ? result.data.params : DEFAULT_BREAKOUT_PARAMS;
  return <section className="card breakout-card" aria-label={`${MARKET_NAMES[market]}全市場進場成功率`} aria-busy={result === null}>
    <header className="card-head"><h2><Rocket size={16} aria-hidden="true" />全市場進場成功率<small>{MARKET_NAMES[market]}</small></h2>
      {data?.scope === 'full' && data.asOf ? <span className="bo-asof">資料日 {formatDateTime(data.asOf, false)}</span> : null}
    </header>
    <div className="bo-body">
      <div className="bo-history">
        <label>測試紀錄<select aria-label="進場成功率測試日期" value={activeDate} onChange={event => setSelectedDate(event.target.value)}>
          <option value="latest">今天／最新一次</option>
          {snapshots.map(entry => <option key={entry.asOf} value={entry.asOf}>{formatDateTime(entry.asOf, false)} 保存結果</option>)}
        </select></label>
        {result?.status === 'ok' && result.data.generatedAt ? <small>測試時間 {formatDateTime(result.data.generatedAt)}</small> : null}
        <details className="bo-inline-help"><summary>參考日與保存說明</summary><p>以選取報表的最新有效資料日為基準，往前每個日曆月重算一次，共6個月；非交易日退到當時最近的有資料交易日。各次只使用該日以前的K線與完整5日觀察。另保留每 {history?.status === 'ok' ? history.data.intervalDays : 20} 個日曆天的原始報表；舊報表沒有每月重算值時不補造。最新一次的實際測試時間與行情資料日分開標示。</p></details>
        {history && !snapshots.length ? <small>{history.status === 'ok' ? '尚無已保存的歷史結果。' : history.message}</small> : null}
      </div>
      {result === null ? <p className="muted-text" role="status">載入中…</p> : data?.scope === 'full' ? <BreakoutBody key={`${market}:${activeDate}`} data={data} params={params} /> :
      data ? <p className="muted-text" role="status">全市場進場成功率尚未產生；舊的部分市場樣本不列入。</p> :
        <p className="muted-text" role="status">{result.status === 'ok' ? `${MARKET_NAMES[market]}的突破勝率尚未產生。` : result.message}</p>}
    </div>
  </section>;
}
