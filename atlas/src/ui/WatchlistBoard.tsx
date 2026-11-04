import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { OpenChartOptions } from '../app/Site';
import type { Route } from '../app/routes';
import { getDailyBars, searchStocks } from '../app/dataSources';
import type { Bar } from '../market-data/MarketDataProvider';
import { formatLiveTime, liveQuotes, mergeDailyBars } from '../market-data/LiveQuotes';
import { fetchTrendingStocks, type TrendingStocksResult } from '../report/trendingStocks';
import type { SavedWatchlist } from '../watchlists/model';
import { displayTicker, formatDateTime, formatPercent, formatPrice, toneClass } from './format';
import './watchlistBoard.css';
import { MonitorSignals } from './MonitorSignals';
import { PageFooterNote } from './PageFooterNote';
import { loadFailureReason } from '../market-data/loadFailureReason';
import { StockChartList } from './StockChartList';
import type { ParameterStock } from '../cloud/settingsApi';

type BoardTab = 'list' | 'trending' | 'signals';
interface BoardRow {
  symbol: string;
  name: string;
}

const FIRST_ROWS = 20;
const SPARK_BARS = 30;

/**
 * Read-only rows of a saved list: the last recorded result (manual lists keep their own order).
 * Never evaluates or visits the list, so the 新入選／出榜 baseline below is untouched.
 */
export function savedListRows(list: SavedWatchlist | undefined): BoardRow[] {
  if (!list) return [];
  const members = (list.lastViewed ?? list.history.at(-1))?.members ?? [];
  if (list.kind !== 'manual') return members;
  const names = new Map(members.map((member) => [member.symbol, member.name]));
  return list.symbols.map((symbol) => ({ symbol, name: names.get(symbol) ?? symbol }));
}

/** Last close, change vs the previous session and a short closing-price path. */
export function rowQuote(bars: readonly Bar[]) {
  const last = bars.at(-1),
    previous = bars.at(-2);
  const changePct = last && previous && previous.close > 0 ? (last.close / previous.close - 1) * 100 : null;
  const closes = bars.slice(-SPARK_BARS).map((bar) => bar.close);
  const low = Math.min(...closes),
    high = Math.max(...closes);
  const points = closes.length > 1
    ? closes.map((close, i) => `${((i / (closes.length - 1)) * 72).toFixed(1)},${(22 - (high > low ? (close - low) / (high - low) : 0.5) * 20).toFixed(1)}`).join(' ')
    : '';
  return { close: last?.close ?? null, changePct, time: last?.time ?? null, points, trend: closes.length > 1 ? closes.at(-1)! - closes[0]! : null };
}

/**
 * `near`: the row has come near the viewport once (starts the bar request; long lists stay cheap).
 * `visible`: it is near the viewport now (only those rows take part in the live refresh).
 */
function useNear<T extends Element>() {
  const ref = useRef<T>(null);
  const [near, setNear] = useState(false);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    if (typeof IntersectionObserver === 'undefined') {
      setNear(true);
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      const shown = entries.some((entry) => entry.isIntersecting);
      setVisible(shown);
      if (shown) setNear(true);
    }, { rootMargin: '200px' });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return { ref, near, visible };
}

function QuoteRow({ row, onOpen }: { row: BoardRow; onOpen: (row: BoardRow) => void }) {
  const { ref, near, visible } = useNear<HTMLLIElement>();
  const [bars, setBars] = useState<readonly Bar[] | null>(null);
  const [resolvedName, setResolvedName] = useState<{ symbol: string; name: string }>();
  useEffect(() => {
    if (!near || row.name !== row.symbol) return;
    let live = true;
    void searchStocks(row.symbol, 20).then(entries => {
      const entry = entries.find(item => item.symbol === row.symbol);
      if (live && entry) setResolvedName({ symbol: row.symbol, name: entry.name });
    }).catch(() => undefined);
    return () => { live = false; };
  }, [near, row.symbol, row.name]);
  const [failure, setFailure] = useState<string | null>(null);
  useEffect(() => {
    if (!near) return;
    let live = true;
    getDailyBars(row.symbol).then(
      (result) => { if (live) { setBars((current) => (current ? mergeDailyBars(result, current.slice(-30)) : result)); setFailure(result.length ? null : '來源沒有有效的日 K 資料'); } },
      (error: unknown) => { if (live) setFailure(loadFailureReason(error)); },
    );
    return () => {
      live = false;
    };
  }, [near, row.symbol]);
  // While on screen: a fresh price right away, then once a minute during the session.
  useEffect(() => {
    if (!visible) return;
    return liveQuotes().subscribe(row.symbol, (value) =>
      { setFailure(null); setBars((current) => (current ? mergeDailyBars(current, value.bars) : value.bars)); });
  }, [visible, row.symbol]);
  const quote = Array.isArray(bars) ? rowQuote(bars) : null;
  const ticker = displayTicker(row.symbol);
  const fullName = row.name !== row.symbol ? row.name : resolvedName?.symbol === row.symbol ? resolvedName.name : null;
  const tone = toneClass(quote?.changePct);
  return (
    <li ref={ref}>
      <a
        className="wb-row"
        href={`?page=chart&symbol=${encodeURIComponent(row.symbol)}`}
        aria-label={`${row.name === row.symbol ? ticker : `${row.name} ${ticker}`} 行情，開啟個股`}
        onClick={(event) => {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          onOpen(row);
        }}
      >
        <span className="wb-name">
          <b>{ticker}</b>
          {fullName && <small title={fullName}>{fullName}</small>}
        </span>
        <svg className={`wb-spark ${toneClass(quote?.trend)}`} viewBox="0 0 72 24" aria-hidden="true">
          {quote?.points && <polyline points={quote.points} />}
        </svg>
        <span className="wb-quote">
          <b>{formatPrice(quote?.close)}</b>
          <small className={`wb-change ${tone}`}>
            {failure && !bars ? '載入失敗' : quote?.changePct == null ? '…' : `${tone === 'up' ? '▲ ' : tone === 'down' ? '▼ ' : ''}${formatPercent(quote.changePct)}`}
          </small>
          {failure && <small className="wb-error" role="status">{failure}</small>}
        </span>
      </a>
    </li>
  );
}

/** Receive time of the latest live batch, while this board is mounted. */
function useLiveUpdatedAt(): number {
  const [at, setAt] = useState(() => liveQuotes().lastUpdatedAt);
  useEffect(() => liveQuotes().onUpdate(setAt), []);
  return at;
}

function useTrending(wanted: boolean) {
  const [state, setState] = useState<TrendingStocksResult>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!wanted) return;
    const abort = new AbortController();
    setState(undefined);
    void fetchTrendingStocks(abort.signal).then((result) => !abort.signal.aborted && setState(result));
    return () => abort.abort();
  }, [wanted, attempt]);
  return { state, retry: () => setAttempt((n) => n + 1) };
}

/**
 * 自選 home: a quote board (saved list / LINE 熱門 / monitor signals with daily change and a
 * 30-day path) above the unchanged list management. Display only: it writes nothing to storage.
 */
export function WatchlistBoard({ viewSwitch, list, lists, parameters, parameterError, onSelectList, openChart, navigate }: {
  viewSwitch?: ReactNode;
  parameters: readonly ParameterStock[];
  parameterError?: string | null;
  list: SavedWatchlist | undefined;
  lists: readonly SavedWatchlist[];
  onSelectList: (id: string) => void;
  openChart: (options: OpenChartOptions) => void;
  navigate: (route: Route) => void;
}) {
  const hasLists = lists.length > 0;
  const [tab, setTab] = useState<BoardTab>(hasLists ? 'list' : 'trending');
  const [expanded, setExpanded] = useState(false);
  const [charts, setCharts] = useState(false);
  const { state: trending, retry } = useTrending(tab === 'trending');
  const liveAt = useLiveUpdatedAt();
  useEffect(() => { setExpanded(false); setCharts(false); }, [tab, list?.id]);
  // The first saved list becomes the default source.
  useEffect(() => {
    if (hasLists) setTab('list');
  }, [hasLists]);

  const rows: BoardRow[] = tab === 'list'
    ? savedListRows(list)
    : trending?.status === 'ok'
        ? trending.data.symbols.map((symbol) => ({ symbol, name: symbol }))
        : [];
  const shown = expanded ? rows : rows.slice(0, FIRST_ROWS);
  const listDate = list && tab === 'list' ? (list.lastViewed ?? list.history.at(-1))?.date : undefined;
  const open = (row: BoardRow) => openChart({ symbol: row.symbol, ...(row.name !== row.symbol ? { name: row.name } : {}) });

  let empty: ReactNode = null;
  if (!rows.length)
    empty = tab === 'list'
      ? hasLists
        ? <p>這份清單還沒有入選股票；下方「清單追蹤結果」載入後會自動更新。</p>
        : <><p>還沒有自選清單。可以在選股頁用篩選條件建立，或在下方建立手動清單。</p>
          <button type="button" className="ghost-button" onClick={() => navigate({ page: 'screener' })}>到選股建立清單</button></>
      : trending?.status === 'error'
          ? <><p>{trending.message}</p><button type="button" className="ghost-button" onClick={retry}>重試</button></>
          : <p>載入 LINE 熱門美股…</p>;

  return (
    <section className="watchlist-board" aria-label="自選行情">
      <div className="wb-toolbar">{viewSwitch}<div className="wb-tabs" role="group" aria-label="行情來源">
        {([['list', '我的清單'], ['trending', 'LINE 熱門'], ['signals', '突破／跌破']] as const).map(([key, label]) => (
          <button key={key} type="button" aria-pressed={tab === key} className={tab === key ? 'active' : ''} onClick={() => {
            setTab(key);
            if (key === 'list' && lists[0] && list?.id !== lists[0].id) onSelectList(lists[0].id);
          }}>{label}</button>
        ))}
      </div>
      </div>
      {tab === 'signals' ? <MonitorSignals openChart={openChart} /> : <>
      <div className="wb-list-toolbar">
      {tab === 'list' && hasLists && <div className="wb-list-picker" role="group" aria-label="我的自選清單">
        <label><span className="sr-only">自選清單</span><select aria-label="選擇自選清單" value={list?.id ?? lists[0]!.id} onChange={event => onSelectList(event.target.value)}>
          {lists.map(entry => <option key={entry.id} value={entry.id}>{entry.name}（{entry.market === 'TW' ? '台股' : '美股'}）</option>)}
        </select></label>
      </div>}
      {rows.length > 0 && <button type="button" className="ghost-button wb-chart-toggle" aria-label={charts ? '返回行情清單' : `連續看全部股票圖表（${rows.length} 檔）`} title={charts ? '返回行情清單' : `連續看全部股票圖表（${rows.length} 檔）`} aria-pressed={charts} onClick={() => setCharts(value => !value)}>
        {charts ? '清單' : '圖表'}
      </button>}
      {liveAt > 0 && <time className="wb-live-time" data-testid="board-live-time" dateTime={new Date(liveAt).toISOString()}>更新於 {formatLiveTime(liveAt)}</time>}
      </div>
      <PageFooterNote><p className="wb-caption">
        <span>{tab === 'list' ? list ? `清單：${list.name}` : '我的清單' : 'LINE 熱門美股'}</span>
        <span>{listDate ? `名單日 ${formatDateTime(listDate, false)} · ` : ''}盤中每分鐘更新（Yahoo 延遲報價）</span>
      </p></PageFooterNote>
      {charts && parameterError && <p role="status">{parameterError}</p>}
      {empty ? <div className="wb-empty" role="status">{empty}</div> : charts ? <StockChartList key={`${tab}:${list?.id ?? ''}`} rows={rows}
        parameters={parameters} groupPeriods={tab === 'list' ? list?.maList : undefined} openChart={openChart} /> : (
        <ul className="wb-rows" aria-label="自選行情列表">
          {shown.map((row) => <QuoteRow key={row.symbol} row={row} onOpen={open} />)}
        </ul>
      )}
      {!charts && rows.length > shown.length && (
        <button type="button" className="text-button wb-more" onClick={() => setExpanded(true)}>顯示全部 {rows.length} 檔</button>
      )}
      </>}
    </section>
  );
}
