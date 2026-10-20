import { useEffect, useState, type ReactNode } from 'react';
import { Rocket } from 'lucide-react';
import {
  DEFAULT_BREAKOUT_PARAMS,
  loadBreakouts,
  sampleLevel,
  type BreakoutMarket,
  type BreakoutMarketKey,
  type BreakoutParams,
  type BreakoutStats,
  type BreakoutStrategyMeta,
  type BreakoutWindow,
  type BreakoutsLoadResult,
} from '../report/breakouts';
import { formatDateTime, formatPercent, toneClass } from './format';
import './breakouts.css';

const MARKET_NAMES: Record<BreakoutMarketKey, string> = {
  tw: '台股',
  us: '美股',
};
const DEFAULT_WINDOW = '1m';

const rate = (value: number | null) => (value === null ? '—' : `${value.toFixed(1)}%`);
const count = (value: number) => value.toLocaleString('zh-TW');
const clampPct = (value: number | null) => (value === null ? 0 : Math.min(100, Math.max(0, value)));

function StrategyRow({ meta, stats, params }: { meta: BreakoutStrategyMeta; stats: BreakoutStats | undefined; params: BreakoutParams }) {
  const level = sampleLevel(stats, params.minSample);
  const head = (status: ReactNode) => (
    <div className="bo-row-head">
      <span className="bo-label">{meta.label}</span>
      {status}
    </div>
  );
  if (!stats) return <li className="bo-row muted">{head(<span className="bo-note">報表尚未提供此方法</span>)}</li>;
  if (level === 'ineligible') return <li className="bo-row muted">{head(<span className="bo-note">日K長度不足，無法計算</span>)}</li>;
  if (level === 'none') return <li className="bo-row muted">{head(<span className="bo-note">窗口內沒有新突破</span>)}</li>;
  if (level === 'pending')
    return (
      <li className="bo-row muted">
        {head(<span className="bo-note">尚無成熟樣本</span>)}
        <p className="bo-sub">
          窗口內 {count(stats.n)} 筆，突破後皆未滿 {params.matureDays} 個交易日
        </p>
      </li>
    );

  const low = level === 'low';
  return (
    <li className={`bo-row${low ? ' low' : ''}`} data-sample={level}>
      {head(
        <>
          {low ? <span className="bo-tag">樣本偏少，參考性低</span> : null}
          <b className="bo-win" aria-label={`進場成功率 ${rate(stats.winRate)}`}>
            <small>進場成功率</small>
            {rate(stats.winRate)}
          </b>
        </>,
      )}
      <div className="bo-bar" role="img" aria-label={`進場成功率 ${rate(stats.winRate)}`}>
        <i style={{ width: `${clampPct(stats.winRate)}%` }} />
      </div>
      <dl className="bo-metrics">
        <div>
          <dt>成功／成熟訊號樣本</dt>
          <dd>
            {stats.wins === null ? '—' : count(stats.wins)}／{count(stats.maturedN)}
          </dd>
        </div>
        <div>
          <dt>歷史足夠的股票</dt>
          <dd>{count(stats.eligible)} 檔</dd>
        </div>
        <div>
          <dt>樣本（成熟／全部）</dt>
          <dd>
            {count(stats.maturedN)}／{count(stats.n)}
          </dd>
        </div>
        <div>
          <dt>平均報酬</dt>
          <dd className={toneClass(stats.avgReturn)}>{formatPercent(stats.avgReturn)}</dd>
        </div>
        <div>
          <dt>中位數報酬</dt>
          <dd className={toneClass(stats.medianReturn)}>{formatPercent(stats.medianReturn)}</dd>
        </div>
        <div>
          <dt>低於進場參考價／線</dt>
          <dd>{rate(stats.failRate)}</dd>
        </div>
      </dl>
    </li>
  );
}

function StrategyList({ strategies, current, params }: { strategies: BreakoutStrategyMeta[]; current: BreakoutWindow; params: BreakoutParams }) {
  return (
    <ul className="bo-list">
      {strategies.map((meta) => (
        <StrategyRow key={meta.key} meta={meta} stats={current.strategies[meta.key]} params={params} />
      ))}
    </ul>
  );
}

function Methodology({ data, current, params, windowLabel }: { data: BreakoutMarket; current: BreakoutWindow; params: BreakoutParams; windowLabel: string }) {
  const windows = params.windows.map((entry) => `${entry.label}＝資料日往前 ${entry.days} 個交易日（含資料日）`).join('；');
  const holdDays = Object.values(current.strategies)
    .map((stats) => stats.avgHoldDays)
    .filter((value): value is number => value !== null);
  return (
    <details className="bo-details">
      <summary>口徑說明</summary>
      <ul className="bo-method">
        <li>
          <b>樣本</b>
          {data.universe}；資料日 {formatDateTime(data.asOf, false)}，全市場名單 {count(data.universeCount)} 檔，資料日有效日線 {count(data.evaluated)} 檔。
          歷史缺漏 {count(data.coverage?.missing ?? 0)} 檔、資料日未成交／停更 {count(data.stale)} 檔不列入；各方法另排除回看歷史不足的股票。
          {data.listingAsOf ? ` 名單日期 ${formatDateTime(data.listingAsOf, false)}。` : ''}{data.listingSource ? ` 名單來源 ${data.listingSource}。` : ''}
        </li>
        <li>
          <b>窗口</b>
          {windows}。{windowLabel}自 {formatDateTime(current.start, false)} 起。
        </li>
        <li>
          <b>進場與成功</b>
          掃描全市場所有股票；成功＝進場後至少滿 {params.matureDays} 個交易日，且資料日收盤高於進場價。
          進場成功率＝成功樣本數 ÷ 出現該進場訊號且已成熟的樣本數（持平不算成功）。沒有訊號的股票不進分母，未成熟或資料不足不當成失敗。
          波段突破採理論停買進場：開盤已高於前高時以開盤價，否則以波段高點作進場價；不以當天收盤價替代盤中進場價。
          箱型與20EMA維持訊號日收盤進場。日線無法證明實際成交價，未模擬最小跳動、滑價與費用。
          「低於進場參考價／線」＝資料日收盤低於進場當日固定的前高、箱頂或 EMA20。
          {holdDays.length ? ` 本窗口平均持有約 ${Math.min(...holdDays).toFixed(1)}～${Math.max(...holdDays).toFixed(1)} 個交易日。` : ''}
        </li>
        <li>
          <b>成熟度</b>
          只統計突破後已滿 {params.matureDays} 個交易日的突破
          {current.maturedThrough ? `（突破日在 ${formatDateTime(current.maturedThrough, false)} 以前）` : ''}
          ；太新的突破還沒有結果，只算在「全部」筆數。成熟樣本少於 {params.minSample} 筆時標示「樣本偏少，參考性低」。
        </li>
        <li>
          <b>定義</b>
          {params.strategies
            .filter((meta) => !meta.volume && meta.definition)
            .map((meta) => `${meta.label}：${meta.definition}${meta.kind === 'box' && data.boxMaxWidthPct !== null ? `（門檻 ${data.boxMaxWidthPct}%）` : ''}`)
            .join('；')}
          。波段高點以前 {params.swingPivotLeft} 根及後 {params.swingPivotRight} 根日線確認，右側日線收盤後的下一根才可觸發，避免提前使用尚未確認的高點。
          使用最近已確認的波段高點，例如123的2號高點；不額外要求完整123或較高低點。盤中最高價嚴格超過前高即觸發，收盤跌回仍計入。
          箱型與20EMA採收盤嚴格大於參考價；EMA20 包含當日收盤，與圖表使用相同起算方式。
        </li>
        <li>
          <b>去重</b>
          波段法每個已確認高點只記第一次突破，不設10日冷卻；不同波段在同一窗口可各自入選，因此訊號筆數可能多於股票檔數。
          箱型與20EMA維持原本 {params.cooldownDays ?? '—'} 日冷卻與同一檔／方法／窗口只取第一次新突破。
        </li>
        <li>
          <b>資料處理</b>
          成交量為 0 的日子視為無效日，缺日不補；週末 K 線視為資料錯誤（本次 {count(data.weekendBars)} 根）。單日收盤漲跌幅超過 {params.jumpLimitPct ?? '—'}%
          視為疑似分割、減資或資料錯誤，只用異常日之後的 K 線（本次 {count(data.anomalies.stocks)} 檔）。
        </li>
        <li>
          <b>偏誤</b>
          名單是「現在」的上市股票，含存活者偏差；快取最多保留 320 根日 K。各筆訊號持有至資料日，持有期間不同，並非固定天數或實際成交回測；未扣交易成本，股利未還原，除息可能壓低報酬。短窗口結果受單一事件影響大，僅供研究參考。
        </li>
      </ul>
    </details>
  );
}

function BreakoutBody({ data, params }: { data: BreakoutMarket; params: BreakoutParams }) {
  const windows = params.windows.filter((entry) => data.windows[entry.key]);
  const [selected, setSelected] = useState(DEFAULT_WINDOW);
  const active = windows.find((entry) => entry.key === selected) ?? windows[0];
  if (!active) return <p className="muted-text">沒有可顯示的統計窗口。</p>;
  const current = data.windows[active.key]!;
  const method = (key: string, label: string): BreakoutStrategyMeta => ({
    ...(params.strategies.find((meta) => meta.key === key) ?? DEFAULT_BREAKOUT_PARAMS.strategies.find((meta) => meta.key === key)!),
    label,
  });
  const main = [method('swingHigh', '突破波段前高進場法'), method('box20', '箱型突破進場法'), method('ema20', '突破20EMA進場法')];
  return (
    <>
      <div className="bo-toolbar">
        <div className="segmented compact" role="group" aria-label="統計窗口">
          {windows.map((entry) => (
            <button
              key={entry.key}
              type="button"
              className={entry.key === active.key ? 'active' : ''}
              aria-pressed={entry.key === active.key}
              onClick={() => setSelected(entry.key)}
            >
              {entry.label}
            </button>
          ))}
        </div>
      </div>
      <p className="bo-sub">前高採已確認的波段高點；盤中突破即觸發，不要求收盤站穩或放量。</p>
      <p className="bo-sub">全市場訊號成功率＝成功樣本／成熟訊號樣本。成功：進場後至少滿 {params.matureDays} 個交易日，資料日收盤高於進場價。</p>
      <StrategyList strategies={main} current={current} params={params} />
      <Methodology data={data} current={current} params={params} windowLabel={active.label} />
      <p className="bo-foot">
        {data.universe}。全市場名單 {count(data.universeCount)} 檔，資料日有效日線 {count(data.evaluated)} 檔；歷史缺漏 {count(data.coverage?.missing ?? 0)}、未成交／停更 {count(data.stale)} 檔。
      </p>
    </>
  );
}

/** Conditional entry success across the full stock universe; legacy partial-universe results stay hidden. */
export function BreakoutCard({ market }: { market: BreakoutMarketKey }) {
  const [result, setResult] = useState<BreakoutsLoadResult | null>(null);
  useEffect(() => {
    let active = true;
    void loadBreakouts().then((next) => {
      if (active) setResult(next);
    });
    return () => {
      active = false;
    };
  }, []);

  const name = MARKET_NAMES[market];
  const data = result?.status === 'ok' ? result.data.markets[market] : undefined;
  const params = result?.status === 'ok' ? result.data.params : DEFAULT_BREAKOUT_PARAMS;
  return (
    <section className="card breakout-card" aria-label={`${name}全市場進場成功率`} aria-busy={result === null}>
      <header className="card-head">
        <h2>
          <Rocket size={16} aria-hidden="true" />
          全市場進場成功率
          <small>{name}</small>
        </h2>
        {data?.scope === 'full' && data.asOf ? <span className="bo-asof">資料日 {formatDateTime(data.asOf, false)}</span> : null}
      </header>
      <div className="bo-body">
        {result === null ? (
          <p className="muted-text" role="status">
            載入中…
          </p>
        ) : data?.scope === 'full' ? (
          <BreakoutBody data={data} params={params} />
        ) : data ? (
          <p className="muted-text" role="status">全市場進場成功率尚未產生；舊的部分市場樣本不列入。</p>
        ) : (
          <p className="muted-text" role="status">
            {result.status === 'ok' ? `${name}的突破勝率尚未產生。` : result.message}
          </p>
        )}
      </div>
    </section>
  );
}
