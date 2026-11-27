import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { usePageActive } from '../app/pageActivity';
import type { PageProps } from './pageProps';
import { SymbolSearch } from '../ui/SymbolSearch';
import { marketProxyAccess } from '../market-data/marketProxyAccess';
import { filterWarrants, loadWarrants, loadWarrantQuotes, sortWarrants, spreadPercent, underlyingPattern, WARRANT_BATCH, type WarrantFile, type WarrantQuotes, type WarrantQuote } from '../report/warrants';
import './warrants.css';

const todayTaipei = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
const amount = (value: number | null | undefined) => value == null ? '—' : String(value);
const timeText = (value: number | null | undefined) => value == null ? '—' : new Date(value * 1000).toLocaleString('zh-TW', { timeZone: 'Asia/Taipei', hour12: false });
const statusText: Record<WarrantQuote['status'], string> = {
  available: '有掛單', no_book: '無掛單', no_quote: '無報價', stale: '過期報價', source_unavailable: '來源暫不可用',
};

export default function WarrantsPage({ symbol = '2330.TW', navigate }: PageProps & { symbol?: string }) {
  const active = usePageActive();
  const access = useSyncExternalStore(marketProxyAccess.subscribe, marketProxyAccess.getSnapshot);
  const [loaded, setLoaded] = useState<{ symbol: string; file: WarrantFile | null; error?: string } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [type, setType] = useState<'' | 'call' | 'put'>('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [offset, setOffset] = useState(0);
  const [quoteState, setQuoteState] = useState<{ key: string; data?: WarrantQuotes; error?: string } | null>(null);
  const [inputError, setInputError] = useState('');
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    void loadWarrants(symbol, controller.signal).then(file => {
      if (!controller.signal.aborted) setLoaded({ symbol, file });
    }).catch(error => {
      if (!controller.signal.aborted) setLoaded({ symbol, file: null, error: error instanceof Error ? error.message : '權證清單載入失敗' });
    });
    return () => controller.abort();
  }, [symbol, active, attempt]);
  const file = loaded?.symbol === symbol ? loaded.file : null;
  const today = todayTaipei();
  const filtered = useMemo(() => filterWarrants(file?.warrants ?? [], type, from, to, today), [file, type, from, to, today]);
  const pageOffset = Math.min(offset, Math.max(0, Math.ceil(filtered.length / WARRANT_BATCH) - 1));
  const batch = filtered.slice(pageOffset * WARRANT_BATCH, (pageOffset + 1) * WARRANT_BATCH);
  const key = batch.map(row => row.symbol).join(',');
  useEffect(() => {
    if (!active || !key || access.status !== 'ready') return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    const refresh = async () => {
      if (!alive || document.hidden || controller || !marketProxyAccess.canRequest()) return;
      controller = new AbortController();
      const current = controller;
      const timeout = setTimeout(() => current.abort(), 15_000);
      try {
        const data = await loadWarrantQuotes(key.split(','), current.signal);
        if (alive) setQuoteState({ key, data });
      } catch (error) {
        if (alive) setQuoteState({ key, error: error instanceof Error ? error.message : '報價暫不可用' });
      } finally {
        clearTimeout(timeout);
        controller = undefined;
        if (alive && !document.hidden) timer = setTimeout(() => void refresh(), 30_000);
      }
    };
    const visibility = () => {
      if (timer) clearTimeout(timer);
      if (document.hidden) { controller?.abort(); return; }
      void refresh();
    };
    document.addEventListener('visibilitychange', visibility);
    void refresh();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      controller?.abort();
      document.removeEventListener('visibilitychange', visibility);
    };
  }, [key, active, access.status]);
  const data = access.status === 'ready' && quoteState?.key === key ? quoteState.data : undefined;
  const rows = sortWarrants(batch, data?.results ?? {});
  return <div className="page warrants-page">
    <h1>權證查詢</h1>
    <section className="card warrants-controls" aria-label="權證篩選">
      <p>標的：<b>{symbol}</b></p>
      <SymbolSearch mode="dropdown" localSymbols={[{ symbol: '^TWII', name: '加權指數' }]} onSelect={result => {
        if (!underlyingPattern.test(result.symbol)) { setInputError('請選擇上市、上櫃或台灣指數標的'); return; }
        setInputError(''); setOffset(0); navigate({ page: 'warrants', symbol: result.symbol });
      }} />
      {inputError && <p role="alert">{inputError}</p>}
      <div className="warrant-filters">
        <label>認購／認售<select value={type} onChange={event => { setType(event.target.value as typeof type); setOffset(0); }}>
          <option value="">全部</option><option value="call">認購</option><option value="put">認售</option>
        </select></label>
        <label>到期日自<input type="date" value={from} onChange={event => { setFrom(event.target.value); setOffset(0); }} /></label>
        <label>到期日至<input type="date" value={to} onChange={event => { setTo(event.target.value); setOffset(0); }} /></label>
      </div>
    </section>
    {loaded?.symbol !== symbol ? <p role="status">載入官方權證清單…</p> : loaded.error ? <p role="alert">{loaded.error} <button onClick={() => setAttempt(value => value + 1)}>重試</button></p> : !file ? <p>此標的沒有官方權證清單。</p> : <>
      <p className="muted">主檔：上市 {file.asOf.TWSE} · 上櫃 {file.asOf.TPEx}；已排除最後交易日已過的權證。</p>
      {from && to && from > to && <p role="alert">開始日期不可晚於結束日期。</p>}
      <p>符合 {filtered.length.toLocaleString()} 檔；每批 {WARRANT_BATCH} 檔，以本批有效買賣價差率由小至大排序，缺價置末。縮小到期日範圍可比較同一批內所有符合的權證。</p>
      {key && <p role="status">{access.status === 'signed-out' ? '登入 LINE 後可查看買賣價' : access.status === 'quota' ? access.message ?? '行情額度暫不可用' : data ? `證交所 MIS · ${data.session === 'regular' ? '交易時段' : '非交易時段／收盤快照'} · 取得時間 ${timeText(data.asOf)}（每 30 秒刷新）` : quoteState?.key === key && quoteState.error ? quoteState.error : '載入最佳買賣價…'}</p>}
      <div className="warrant-paging">
        <button className="ghost-button" disabled={pageOffset === 0} onClick={() => setOffset(pageOffset - 1)}>上一批</button>
        <span>{filtered.length ? pageOffset + 1 : 0} / {Math.ceil(filtered.length / WARRANT_BATCH)}</span>
        <button className="ghost-button" disabled={(pageOffset + 1) * WARRANT_BATCH >= filtered.length} onClick={() => setOffset(pageOffset + 1)}>下一批</button>
      </div>
      {!rows.length && <p>沒有符合條件的權證。</p>}
      <div className="warrant-list" aria-label="權證清單">
        {rows.map(row => {
          const q = data?.results[row.symbol];
          const book = q?.status === 'available' ? q : undefined;
          const spread = spreadPercent(q);
          return <article className="card warrant-row" key={row.symbol} aria-label={row.symbol}>
            <header><b>{row.code} {row.name}</b><span>{row.type === 'call' ? '認購' : '認售'} · {row.market === 'TWSE' ? '上市' : '上櫃'}</span></header>
            <dl className="warrant-book">
              <div><dt>最佳買價</dt><dd>{amount(book?.bid)}</dd></div><div><dt>最佳賣價</dt><dd>{amount(book?.ask)}</dd></div>
              <div><dt>價差率</dt><dd>{spread === null ? '—' : spread.toFixed(2) + '%'}</dd></div>
            </dl>
            <dl className="warrant-details">
              <div><dt>履約價</dt><dd>{amount(row.strikePrice)}</dd></div><div><dt>行使比例</dt><dd>{amount(row.exerciseRatio)}</dd></div>
              <div><dt>到期日</dt><dd>{row.expiryDate}</dd></div><div><dt>最後交易日</dt><dd>{row.lastTradingDate}</dd></div>
              <div><dt>發行商</dt><dd>{row.issuer ?? '—'}</dd></div><div><dt>買量／賣量</dt><dd>{amount(book?.bidSize)}／{amount(book?.askSize)}</dd></div>
            </dl>
            <p className="muted">{q ? statusText[q.status] : '尚未取得報價'} · {timeText(q?.quoteTime)}</p>
          </article>;
        })}
      </div>
      <p className="muted">價差率 =（最佳賣價 − 最佳買價）÷ 買賣中間價 × 100%。單邊或過期掛單不可計算。報價不是成交保證。</p>
    </>}
  </div>;
}
