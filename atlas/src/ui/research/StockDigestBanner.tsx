import { useEffect, useState } from 'react';
import { marketCalendar } from '../../market-data/MarketCalendar';
import { closedMarketDates, loadMarketClosures } from '../../market-data/MarketClosures';
import { FLOW_MAX_AGE_SESSIONS, digestText, fetchStockDigest, visibleDigest, type StockDigest } from '../../report/stockDigest';

export function StockDigestBanner({ symbol }: { symbol: string }) {
  const [data, setData] = useState<StockDigest | null>(null);
  const [sessions, setSessions] = useState<string[]>([]);
  const today = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit' }).format(Date.now());
  useEffect(() => {
    const abort = new AbortController();
    void fetchStockDigest(abort.signal).then(result => {
      if (!abort.signal.aborted) setData(result);
    }).catch(() => {
      if (!abort.signal.aborted) setData(null);
    });
    return () => abort.abort();
  }, []);
  useEffect(() => {
    let active = true;
    setSessions([]);
    if (!symbol.endsWith('.TW') && !symbol.endsWith('.TWO')) return;
    const now = Date.now();
    void Promise.all([marketCalendar.ensure(now), loadMarketClosures(fetch, now)]).then(([, policy]) => {
      if (active) setSessions(marketCalendar.recentSessions('TW', today, FLOW_MAX_AGE_SESSIONS, closedMarketDates(symbol, policy)));
    });
    return () => { active = false; };
  }, [symbol, today]);
  const rows = data ? visibleDigest(data, symbol, today, sessions) : [];
  if (!rows.length) return null;
  return <section className="console-card" aria-label="近期異動摘要">
    <b>近期異動摘要</b>
    <ul style={{ margin: '6px 0 0', paddingInlineStart: '20px', overflowWrap: 'anywhere' }}>
      {rows.map((row, i) => <li key={i}><time dateTime={row.date}>{row.date}</time> · {digestText(row)}</li>)}
    </ul>
  </section>;
}
