import { useEffect, useMemo, useState } from 'react';
import { usePageActive } from '../app/pageActivity';
import type { PageProps } from './pageProps';
import { SymbolSearch } from '../ui/SymbolSearch';
import { currentWarrantQuotes, filterWarrants, loadWarrants, sortWarrants, spreadPercent, underlyingPattern, warrantDate, WARRANT_BATCH, type WarrantFile, type WarrantQuote } from '../report/warrants';
import { useWarrantQuotes } from './useWarrantQuotes';
import './warrants.css';

const amount = (value: number | null | undefined) => value == null ? '—' : String(value);
const timeText = (value: number | null | undefined) => value == null ? '—' : new Date(value * 1000).toLocaleString('zh-TW', { timeZone: 'Asia/Taipei', hour12: false });
const statusText: Record<WarrantQuote['status'], string> = {
  available: '有掛單', no_book: '無掛單', no_quote: '無報價', stale: '過期報價', source_unavailable: '來源暫不可用',
};

export default function WarrantsPage({ symbol = '2330.TW', navigate }: PageProps & { symbol?: string }) {
  const active = usePageActive();
  const [loaded, setLoaded] = useState<{ symbol: string; file: WarrantFile | null; error?: string } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [type, setType] = useState<'' | 'call' | 'put'>('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [offset, setOffset] = useState(0);
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
  const today = warrantDate(Date.now());
  const filtered = useMemo(() => filterWarrants(file?.warrants ?? [], type, from, to, today), [file, type, from, to, today]);
  const key = useMemo(() => filtered.map(row => row.symbol).join(','), [filtered]);
  const { access, now, scan, refresh } = useWarrantQuotes(active, key);
  const quotes = useMemo(() => currentWarrantQuotes(scan?.samples ?? {}, now), [scan, now]);
  const comparable = useMemo(() => Object.values(quotes).filter(quote => spreadPercent(quote) !== null).length, [quotes]);
  const ranked = useMemo(() => sortWarrants(filtered, quotes), [filtered, quotes]);
  const pageOffset = Math.min(offset, Math.max(0, Math.ceil(filtered.length / WARRANT_BATCH) - 1));
  const rows = ranked.slice(pageOffset * WARRANT_BATCH, (pageOffset + 1) * WARRANT_BATCH);
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
      <p className="muted">主檔 上市 {file.asOf.TWSE} · 上櫃 {file.asOf.TPEx}。只列最後交易日為今天或之後的權證。</p>
      {from && to && from > to && <p role="alert">開始日期不可晚於結束日期。</p>}
      <p>符合 {filtered.length.toLocaleString()} 檔。有效雙邊價差率由小至大排序，缺價置末。</p>
      {key && <div className="warrant-coverage">
        <div role="status">
          {access.status === 'signed-out' ? '登入 LINE 後可查看買賣價' : <>
            {scan ? <>
              <p>已查詢 {scan.attempted.toLocaleString()}／{filtered.length.toLocaleString()} 檔 · {scan.received.toLocaleString()} 檔有來源結果 · {comparable.toLocaleString()} 檔可比較</p>
              <p>{scan.loading ? '分批載入中，排序尚未完成' : scan.received < filtered.length ? '部分報價暫不可用，排序涵蓋已取得的結果' : '全部結果已查詢，依目前可用價差率排序'}</p>
            </> : access.status !== 'quota' && '載入最佳買賣價…'}
          </>}
        </div>
        {scan?.lastAsOf && <p className="muted">證交所 MIS · {scan.session === 'regular' ? '末批交易時段' : '末批非交易時段／收盤快照'} · 取得時間 {timeText(scan.firstAsOf)}{scan.firstAsOf !== scan.lastAsOf ? ` 至 ${timeText(scan.lastAsOf)}` : ''}</p>}
        {access.status === 'ready' && scan && !scan.loading && <button className="ghost-button" onClick={refresh}>重新整理報價</button>}
      </div>}
      <div className="warrant-paging">
        <button className="ghost-button" disabled={pageOffset === 0} onClick={() => setOffset(pageOffset - 1)}>上一頁</button>
        <span>{filtered.length ? pageOffset + 1 : 0} / {Math.ceil(filtered.length / WARRANT_BATCH)}</span>
        <button className="ghost-button" disabled={(pageOffset + 1) * WARRANT_BATCH >= filtered.length} onClick={() => setOffset(pageOffset + 1)}>下一頁</button>
      </div>
      {!rows.length && <p>沒有符合條件的權證。</p>}
      <div className="warrant-list" aria-label="權證清單">
        {rows.map(row => {
          const q = quotes[row.symbol];
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
      <p className="muted">價差率 =（最佳賣價 − 最佳買價）÷ 買賣中間價 × 100%。單邊或過期掛單不計入比較。</p>
    </>}
  </div>;
}
