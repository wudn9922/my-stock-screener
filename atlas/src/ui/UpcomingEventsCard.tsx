import { useEffect, useState, useSyncExternalStore } from 'react';
import { accessState } from '../auth/access';
import { usePageActive } from '../app/pageActivity';
import { holdingsStore } from '../holdings/store';
import type { SavedWatchlist } from '../watchlists/model';
import { EVENTS_UNAVAILABLE, fetchUpcomingEvents, groupUpcomingEvents, type UpcomingEvents, type UpcomingEvent } from '../events/UpcomingEvents';
import './upcoming-events.css';

const labels: Record<UpcomingEvent['kind'], string> = {
  earnings: '財報公布', 'stock-dividend': '除權', 'cash-dividend': '除息',
  'disposal-start': '處置開始', 'disposal-end': '預定處置結束', notice: '注意股',
  holiday: '休市', 'early-close': '提早收盤', fomc: 'FOMC 利率決議',
};

export function UpcomingEventsCard({ lists }: { lists: readonly SavedWatchlist[] }) {
  const active = usePageActive();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<UpcomingEvents | null>(null);
  const [error, setError] = useState(false);
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  const holdings = holdingsStore();
  const snapshot = useSyncExternalStore(holdings.subscribe, holdings.getSnapshot);
  useEffect(() => {
    if (!open || !active || access.storageMode !== 'cloud') return;
    // Reuse the existing authorized holdings read; no symbol list is sent.
    void holdings.reload().catch(() => undefined);
  }, [open, active, holdings, access.storageMode]);
  useEffect(() => {
    if (!open || !active) return;
    const abort = new AbortController();
    void fetchUpcomingEvents(abort.signal).then(value => {
      if (!abort.signal.aborted) { setData(value); setError(false); }
    }).catch(() => {
      if (!abort.signal.aborted) { setData(null); setError(true); }
    });
    return () => abort.abort();
  }, [open, active]);
  // This is a projection only: never visit, evaluate, persist or sync a list.
  const symbols = [...lists.flatMap(list => list.kind === 'manual' ? list.symbols
    : (list.history.at(-1) ?? list.lastViewed)?.members.map(member => member.symbol) ?? []),
    ...snapshot.document.accounts.flatMap(account => account.holdings.map(row => row.symbol))];
  const groups = data ? groupUpcomingEvents(data.events, symbols, Date.now()) : [];
  return <details className="console-card upcoming-events" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>未來 14 天事件</summary>
    <div className="upcoming-events-body">
      <p className="muted">自選清單及可用持股 · 市場事件另列 · 日期及倒數依交易所當地時間。</p>
      {snapshot.error && <p className="muted">持股資料暫時無法取得；仍顯示可用清單。</p>}
      {error ? <p role="status">{EVENTS_UNAVAILABLE}</p> : !data ? <p role="status">載入中…</p> : <>
        <p className="muted">更新 {data.generatedAt.slice(0, 10)} · 台股財報／法說會尚無預告來源；現金股利依既有約 30 天逐檔更新，可能尚未涵蓋新公告。</p>
        <p className="muted">財報日期涵蓋 {data.coverage.earnings.toLocaleString()} 檔 · 現金股利已讀取 {data.coverage.cashDividends.toLocaleString()} 檔。</p>
        {!groups.length && <p role="status">已收錄的事件中，未來 14 天沒有符合的項目。</p>}
        {groups.map(group => <section key={group.date} className="upcoming-event-day" aria-label={group.date}>
          <h3><time dateTime={group.date}>{group.date}</time></h3>
          <ul>{group.rows.map((row, i) => {
            const move = row.kind === 'earnings' && row.symbol ? data.moves[row.symbol] : undefined;
            return <li key={i}>
              <div className="upcoming-event-head"><b>{row.symbol ?? (row.market === 'TW' ? '台股' : '美股')} · {labels[row.kind]}</b><span>{row.days === 0 ? '今天' : row.days === 1 ? '明天' : row.days + ' 天後'}</span></div>
              <p>{row.detail} · {row.source}</p>
              {row.kind === 'earnings' && <p className="upcoming-move">{move?.samples && move.gapPct !== null && move.dayPct !== null
                ? `過去 ${move.samples} 次：開盤平均 ±${move.gapPct.toFixed(1)}% · 當日 ±${move.dayPct.toFixed(1)}%`
                : '歷史財報波動：尚無可確認樣本'}</p>}
            </li>;
          })}</ul>
        </section>)}
        <p className="muted">波動以分割調整後前一交易日收盤為基準；只計有確定盤前／盤後時間且行情齊全的最多 8 次公布，不代表預測。</p>
      </>}
    </div>
  </details>;
}
