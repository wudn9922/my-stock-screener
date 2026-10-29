import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { accessState } from '../auth/access';
import { getStorageContextKey } from '../auth/storage';
import { fetchMonitorSignals, type MonitorGroup, type MonitorResult, type MonitorSessions, type MonitorStock } from '../report/monitorSignals';
import { MiniChart } from './MiniChart';
import type { OpenChartOptions } from '../app/Site';
import { displayTicker, formatDateTime, formatPrice, formatRatio } from './format';
import './monitorSignals.css';

const sessionLabel = (sessions: MonitorSessions) => (['TW', 'US'] as const)
  .filter(market => sessions[market]).map(market => ` · ${market === 'TW' ? '台股' : '美股'} ${formatDateTime(sessions[market]!, false)}`).join('');

function SignalStock({ stock, openChart }: { stock: MonitorStock; openChart: (options: OpenChartOptions) => void }) {
  const ref = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!ref.current) return;
    if (typeof IntersectionObserver === 'undefined') { setVisible(true); return; }
    // Release chart canvases outside the scroll window; card height stays stable.
    const observer = new IntersectionObserver(entries => setVisible(entries.some(entry => entry.isIntersecting)), { rootMargin: '500px' });
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return <article className="monitor-stock console-card" ref={ref} aria-label={`${stock.name} ${stock.ticker} 訊號圖表`}>
    <header>
      <div><h4>{stock.name}</h4><span>{displayTicker(stock.symbol ?? stock.ticker)}</span></div>
      <button type="button" className="text-button" disabled={!stock.symbol} onClick={() => stock.symbol && openChart({ symbol: stock.symbol, name: stock.name })}>開啟個股</button>
    </header>
    <ul className="monitor-triggers" aria-label="已發送訊號">
      {stock.signals.map(signal => <li key={signal.maPeriod}>
        <b className={signal.direction === 'breakout_up' ? 'up' : 'down'}>{signal.direction === 'breakout_up' ? '突破' : '跌破'} MA{signal.maPeriod}</b>
        <span>{formatDateTime(signal.sentAt)}</span>
        <small>觸發價 {formatPrice(signal.price)} · 均線 {formatPrice(signal.maValue)}{signal.volumeRatio !== null ? ` · 量比 ${formatRatio(signal.volumeRatio)}` : ''}</small>
      </li>)}
    </ul>
    <div className="monitor-chart" aria-label={`${stock.ticker} 日 K 與成交量`}>
      {!stock.symbol ? <p>代號尚未對應行情來源，仍保留原始訊號。</p> : visible
        ? <MiniChart symbol={stock.symbol} height={280} maPeriods={stock.maPeriods} initialMonths={6} interactive showVolume wheelZoom={false} />
        : <span className="mini-chart-empty">圖表將在捲動到此處時載入</span>}
    </div>
  </article>;
}

function GroupDetail({ group, onBack, openChart }: { group: MonitorGroup; onBack: () => void; openChart: (options: OpenChartOptions) => void }) {
  const [direction, setDirection] = useState<'all' | 'breakout_up' | 'breakout_down'>('all');
  const [limit, setLimit] = useState(20);
  const more = useRef<HTMLButtonElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const stocks = group.stocks.filter(stock => direction === 'all' || stock.signals.some(signal => signal.direction === direction));
  useEffect(() => { heading.current?.focus({ preventScroll: true }); heading.current?.scrollIntoView({ block: 'start' }); }, []);
  useEffect(() => {
    if (!more.current || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting)) setLimit(value => value + 20); }, { rootMargin: '600px' });
    observer.observe(more.current);
    return () => observer.disconnect();
  }, [limit, direction, stocks.length]);
  return <section className="monitor-detail" aria-label="監控組別詳細">
    <div className="monitor-detail-head"><button type="button" className="ghost-button" onClick={onBack}>返回組別</button>
      <h3 ref={heading} tabIndex={-1}>{group.name} · {stocks.length} 檔</h3></div>
    <div className="wb-tabs" role="group" aria-label="訊號方向">
      {([['all', '全部'], ['breakout_up', '突破'], ['breakout_down', '跌破']] as const).map(([value, label]) =>
        <button key={value} type="button" aria-pressed={direction === value} className={direction === value ? 'active' : ''} onClick={() => { setDirection(value); setLimit(20); }}>{label}</button>)}
    </div>
    <div className="monitor-stock-list">{stocks.slice(0, limit).map(stock => <SignalStock key={stock.ticker} stock={stock} openChart={openChart} />)}</div>
    {stocks.length > limit && <button ref={more} type="button" className="text-button" onClick={() => setLimit(value => value + 20)}>繼續載入圖表（{limit}／{stocks.length}）</button>}
    {!stocks.length && <p role="status">這個組別目前沒有此方向的已發送訊號。</p>}
  </section>;
}

function CloudSignals({ openChart }: { openChart: (options: OpenChartOptions) => void }) {
  const heading = useRef<HTMLHeadingElement>(null);
  const [result, setResult] = useState<MonitorResult>();
  const [attempt, setAttempt] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    setResult(undefined);
    void fetchMonitorSignals(abort.signal).then(value => { if (!abort.signal.aborted) setResult(value); });
    return () => abort.abort();
  }, [attempt]);
  useEffect(() => {
    // Refresh on returning to Atlas, at most once a minute while visible.
    let last = Date.now();
    const refresh = () => { if (document.visibilityState === 'visible' && Date.now() - last >= 60_000) { last = Date.now(); setAttempt(value => value + 1); } };
    document.addEventListener('visibilitychange', refresh); window.addEventListener('focus', refresh);
    return () => { document.removeEventListener('visibilitychange', refresh); window.removeEventListener('focus', refresh); };
  }, []);
  const group = result?.status === 'ok' ? result.groups.find(group => group.key === selected) : undefined;
  return <section className="monitor-signals" aria-label="Discord 突破跌破監控">
    <div className="monitor-summary"><h3 ref={heading} tabIndex={-1}>突破／跌破</h3><button type="button" className="text-button" onClick={() => setAttempt(value => value + 1)}>重新整理</button></div>
    {!result ? <p role="status">讀取已成功發送到 Discord 的訊號…</p> : result.status === 'error'
      ? <div role="alert"><p>{result.message}</p><button type="button" className="ghost-button" onClick={() => setAttempt(value => value + 1)}>重試</button></div>
      : <>
        <div className="monitor-caption">最新一天{sessionLabel(result.sessions)} · 更新 {formatDateTime(result.data.generatedAt)}
          <details><summary>訊號範圍</summary><p>與 Discord 共用成功發送紀錄。台股、美股各自只顯示最近一個有訊號的交易日（依當地日期），下一個交易日有新訊號就換成新的一天；休市時保留最後一天。每組、每檔、每條均線只保留最後一次訊號；這不是完整訊息歷史。圖表為延遲日 K，觸發價是發送當時的價格。</p></details>
        </div>
        {result.data.truncated && <p role="status">訊號較多，目前顯示最新 5,000 筆。</p>}
        {group ? <GroupDetail key={group.key} group={group} onBack={() => { setSelected(null); heading.current?.focus({ preventScroll: true }); heading.current?.scrollIntoView({ block: 'start' }); }} openChart={openChart} />
          : !result.groups.length ? <p role="status">最近沒有成功發送的突破／跌破訊號，休市期間仍會保留最後一天的紀錄。</p>
            : <div className="monitor-groups">{result.groups.map(group => <article key={group.key} className="console-card monitor-group">
              <span>{group.market === 'TW' ? '台股' : '美股'}</span><h4>{group.name}</h4>
              <p><b className="up">突破 {group.up}</b><b className="down">跌破 {group.down}</b><span>共 {group.stocks.length} 檔</span></p>
              <small>最近訊號 {formatDateTime(group.latest)}</small>
              <button type="button" className="ghost-button" aria-label={`${group.name} 詳細`} onClick={() => setSelected(group.key)}>詳細 · 看圖表清單</button>
            </article>)}</div>}
      </>}
  </section>;
}

export function MonitorSignals({ openChart }: { openChart: (options: OpenChartOptions) => void }) {
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  const context = getStorageContextKey();
  if (!access.settled) return <p role="status">讀取監控訊號…</p>;
  if (access.storageMode !== 'cloud') return <p className="wb-empty" role="status">突破／跌破是個人雲端監控資料，僅供已獲雲端授權的帳號查看。</p>;
  return <CloudSignals key={context} openChart={openChart} />;
}
