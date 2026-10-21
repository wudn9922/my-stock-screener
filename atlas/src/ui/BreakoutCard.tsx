import { useEffect, useState } from 'react';
import { ArrowUpRight, GitBranch, Layers, Rocket, ShieldCheck, Target, Timer } from 'lucide-react';
import {
  DEFAULT_BREAKOUT_PARAMS, OUTCOME_RULES, SUCCESS_MODEL, loadBreakouts,
  type BreakoutMarket, type BreakoutMarketKey,
  type BreakoutParams, type BreakoutStats, type BreakoutWindow, type BreakoutsLoadResult, type OutcomeKey,
} from '../report/breakouts';
import { formatDateTime } from './format';
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

function StrategyRow({ method, stats, rule, params, index }: {
  method: typeof METHODS[number]; stats: BreakoutStats | undefined; rule: OutcomeKey; params: BreakoutParams; index: number;
}) {
  const comparison = stats?.comparison;
  const outcome = comparison?.rules[rule];
  const low = outcome && outcome.evaluatedN > 0 && outcome.evaluatedN < params.minSample;
  const note = !stats ? '報表尚未提供此方法' : !comparison || !outcome ? '此口徑資料尚未提供' :
    !comparison.n ? '窗口內沒有新突破' : !comparison.completeN ? comparison.pendingN ? '等待5日觀察' : '後續日線不足' : null;
  return <li className={`bo-row${low ? ' low' : ''}`} data-method={method.key}>
    <div className="bo-row-head">
      <div className="bo-method-name"><span className="bo-method-index">0{index + 1}</span><div>
        <h3 className="bo-label">{method.label}</h3><p>{method.hint}</p>
      </div></div>
      <b className="bo-win" aria-label={`進場成功率 ${note ? '尚無完整結果' : rate(outcome?.winRate ?? null)}`}>
        {note ? '—' : rate(outcome?.winRate ?? null)}<small>成功率</small>
      </b>
    </div>
    <div className="bo-bar" role="img" aria-label={`成功率 ${note ? '尚無完整結果' : rate(outcome?.winRate ?? null)}`}>
      <i style={{ width: `${note ? 0 : Math.min(100, Math.max(0, outcome?.winRate ?? 0))}%` }} />
    </div>
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
      近1週的新訊號可能全在等待觀察，切換近1月可查看較完整結果。</li>
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
      名單是目前上市股票，含存活者偏差；快取最多320根日線，股利未還原，結果僅供研究。</li>
  </ul></details>;
}

function BreakoutBody({ data, params }: { data: BreakoutMarket; params: BreakoutParams }) {
  const [selectedWindow, setWindow] = useState('1m');
  const [selectedRule, setRule] = useState<OutcomeKey>('hold5');
  const windows = params.windows.filter(entry => data.windows[entry.key]);
  const active = windows.find(entry => entry.key === selectedWindow) ?? windows[0];
  if (!active) return <p className="muted-text">沒有可顯示的統計窗口。</p>;
  if (params.successModel !== SUCCESS_MODEL) return <p className="bo-update" role="status"><Timer size={18} aria-hidden="true" />四種勝率口徑更新中，等待新版全市場報表。舊報酬勝率不混入新口徑。</p>;
  const current = data.windows[active.key]!;
  const rule = OUTCOME_RULES.find(entry => entry.key === selectedRule)!;
  const complete = Object.values(current.strategies).some(stats => stats.comparison?.completeN);
  return <>
    <div className="bo-toolbar"><span className="bo-overline">3 種進場法 · 4 種判定</span><div className="segmented compact" role="group" aria-label="統計窗口">
      {windows.map(entry => <button type="button" key={entry.key} className={entry.key === active.key ? 'active' : ''}
        aria-pressed={entry.key === active.key} onClick={() => setWindow(entry.key)}>{entry.label}</button>)}
    </div></div>
    <OutcomeSelector selected={selectedRule} onSelect={setRule} />
    <div className="bo-rule-description" aria-live="polite"><b>{rule.label}</b><p>{rule.description}</p></div>
    <div className="bo-common"><ShieldCheck size={15} aria-hidden="true" /><p><b>突破日收盤門檻，四組共用</b><span>關鍵點 ＋ 0.1 ATR(14)；未達即失敗</span></p></div>
    {!complete ? <p className="bo-pending-note"><Timer size={14} aria-hidden="true" />這段訊號仍在觀察或缺少後續日線，尚無完整比較結果。</p> : null}
    <ul className="bo-list">{METHODS.map((method, index) => <StrategyRow key={method.key} method={method} index={index}
      stats={current.strategies[method.key]} rule={selectedRule} params={params} />)}</ul>
    <ComparisonTable current={current} selected={selectedRule} onSelect={setRule} />
    <Methodology data={data} current={current} params={params} />
    <p className="bo-foot"><i />{data.universe} · 有效日線 {count(data.evaluated)} 檔</p>
  </>;
}

export function BreakoutCard({ market }: { market: BreakoutMarketKey }) {
  const [result, setResult] = useState<BreakoutsLoadResult | null>(null);
  useEffect(() => {
    let active = true;
    void loadBreakouts().then(next => { if (active) setResult(next); });
    return () => { active = false; };
  }, []);
  const data = result?.status === 'ok' ? result.data.markets[market] : undefined;
  const params = result?.status === 'ok' ? result.data.params : DEFAULT_BREAKOUT_PARAMS;
  return <section className="card breakout-card" aria-label={`${MARKET_NAMES[market]}全市場進場成功率`} aria-busy={result === null}>
    <header className="card-head"><h2><Rocket size={16} aria-hidden="true" />全市場進場成功率<small>{MARKET_NAMES[market]}</small></h2>
      {data?.scope === 'full' && data.asOf ? <span className="bo-asof">資料日 {formatDateTime(data.asOf, false)}</span> : null}
    </header>
    <div className="bo-body">{result === null ? <p className="muted-text" role="status">載入中…</p> : data?.scope === 'full' ? <BreakoutBody data={data} params={params} /> :
      data ? <p className="muted-text" role="status">全市場進場成功率尚未產生；舊的部分市場樣本不列入。</p> :
        <p className="muted-text" role="status">{result.status === 'ok' ? `${MARKET_NAMES[market]}的突破勝率尚未產生。` : result.message}</p>}
    </div>
  </section>;
}
