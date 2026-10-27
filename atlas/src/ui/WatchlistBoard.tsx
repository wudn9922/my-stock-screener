import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import type { OpenChartOptions } from '../app/Site';
import type { Route } from '../app/routes';
import { getDailyBars } from '../app/dataSources';
import { sitePreferences } from '../app/sitePreferences';
import type { Bar } from '../market-data/MarketDataProvider';
import type { ReportState } from '../report/useReport';
import { fetchTrendingStocks, type TrendingStocksResult } from '../report/trendingStocks';
import type { SavedWatchlist } from '../watchlists/model';
import { displayTicker, formatDateTime, formatPercent, formatPrice, toneClass } from './format';
import './watchlistBoard.css';

type BoardTab = 'list' | 'trending' | 'recent';
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

/** Starts the bar request only once the row is near the viewport (long lists stay cheap). */
function useNear<T extends Element>() {
  const ref = useRef<T>(null);
  const [near, setNear] = useState(false);
  useEffect(() => {
    const element = ref.current;
    if (!element || near) return;
    if (typeof IntersectionObserver === 'undefined') return setNear(true);
    const observer = new IntersectionObserver((entries) => entries.some((entry) => entry.isIntersecting) && setNear(true), { rootMargin: '200px' });
    observer.observe(element);
    return () => observer.disconnect();
  }, [near]);
  return { ref, near };
}

function QuoteRow({ row, onOpen }: { row: BoardRow; onOpen: (row: BoardRow) => void }) {
  const { ref, near } = useNear<HTMLLIElement>();
  const [bars, setBars] = useState<readonly Bar[] | 'error' | null>(null);
  useEffect(() => {
    if (!near) return;
    let live = true;
    getDailyBars(row.symbol).then(
      (result) => live && setBars(result),
      () => live && setBars('error'),
    );
    return () => {
      live = false;
    };
  }, [near, row.symbol]);
  const quote = Array.isArray(bars) ? rowQuote(bars) : null;
  const ticker = displayTicker(row.symbol);
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
          <b>{row.name === row.symbol ? ticker : row.name}</b>
          {row.name !== row.symbol && <small>{ticker}</small>}
        </span>
        <svg className={`wb-spark ${toneClass(quote?.trend)}`} viewBox="0 0 72 24" aria-hidden="true">
          {quote?.points && <polyline points={quote.points} />}
        </svg>
        <span className="wb-quote">
          <b>{bars === 'error' ? '—' : formatPrice(quote?.close)}</b>
          <small className={`wb-change ${tone}`}>
            {bars === 'error' ? '暫無行情' : quote?.changePct == null ? '…' : `${tone === 'up' ? '▲ ' : tone === 'down' ? '▼ ' : ''}${formatPercent(quote.changePct)}`}
          </small>
        </span>
      </a>
    </li>
  );
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
 * 自選 home: a quote board (index strip, saved list / LINE 熱門 / recent searches with daily change and a
 * 30-day path) above the unchanged list management. Display only: it writes nothing to storage.
 */
export function WatchlistBoard({ report, list, hasLists, openChart, navigate }: {
  report: ReportState;
  list: SavedWatchlist | undefined;
  hasLists: boolean;
  openChart: (options: OpenChartOptions) => void;
  navigate: (route: Route) => void;
}) {
  const [tab, setTab] = useState<BoardTab>(hasLists ? 'list' : 'trending');
  const [expanded, setExpanded] = useState(false);
  const recent = useSyncExternalStore(sitePreferences.subscribe, () => sitePreferences.get().recent);
  const { state: trending, retry } = useTrending(tab === 'trending');
  useEffect(() => setExpanded(false), [tab, list?.id]);
  // The first saved list becomes the default source.
  useEffect(() => {
    if (hasLists) setTab('list');
  }, [hasLists]);

  const indices = report.status === 'ok'
    ? (['tw', 'us'] as const).flatMap((key) => report.report.markets.find((market) => market.key === key)?.indices.slice(0, 2) ?? [])
    : [];
  const rows: BoardRow[] = tab === 'list'
    ? savedListRows(list)
    : tab === 'recent'
      ? recent
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
      : tab === 'recent'
        ? <p>還沒有搜尋紀錄；用上方搜尋找股票後會列在這裡。</p>
        : trending?.status === 'error'
          ? <><p>{trending.message}</p><button type="button" className="ghost-button" onClick={retry}>重試</button></>
          : <p>載入 LINE 熱門美股…</p>;

  return (
    <section className="watchlist-board" aria-label="自選行情">
      {indices.length > 0 && (
        <div className="wb-indices" role="group" aria-label="主要指數">
          {indices.map((index) => (
            <button key={index.symbol} type="button" className="wb-index" onClick={() => openChart({ symbol: index.symbol, name: index.name })}>
              <small>{index.name}</small>
              <b>{formatPrice(index.close)}</b>
              <small className={toneClass(index.changePct)}>{formatPercent(index.changePct)}</small>
            </button>
          ))}
        </div>
      )}
      <div className="wb-tabs" role="group" aria-label="行情來源">
        {([['list', '我的清單'], ['trending', 'LINE 熱門'], ['recent', '最近搜尋']] as const).map(([key, label]) => (
          <button key={key} type="button" aria-pressed={tab === key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>
      <p className="wb-caption">
        <span>{tab === 'list' ? list ? `清單：${list.name}（在下方切換）` : '我的清單' : tab === 'trending' ? 'LINE 熱門美股' : '最近搜尋的股票'}</span>
        <span>{listDate ? `名單日 ${formatDateTime(listDate, false)} · ` : ''}延遲報價</span>
      </p>
      {empty ? <div className="wb-empty" role="status">{empty}</div> : (
        <ul className="wb-rows" aria-label="自選行情列表">
          {shown.map((row) => <QuoteRow key={row.symbol} row={row} onOpen={open} />)}
        </ul>
      )}
      {rows.length > shown.length && (
        <button type="button" className="text-button wb-more" onClick={() => setExpanded(true)}>顯示全部 {rows.length} 檔</button>
      )}
    </section>
  );
}
