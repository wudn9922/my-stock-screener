import { useEffect, useState } from 'react';
import { fetchCashDividends, type CashDividends } from './twCashDividends';
import { dividendCashflow, taipeiDay } from './dividendCashflow';
import { formatAmount, type Holding } from './model';
import '../ui/upcoming-events.css';

export function DividendCashflowCard({ holdings }: { holdings: readonly Holding[] }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<CashDividends | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    if (!open) return;
    const abort = new AbortController();
    // One public all-market file; no symbols, account IDs, costs or quantities in the request.
    void fetchCashDividends(abort.signal).then(result => {
      if (!abort.signal.aborted) {
        setData(result);
        setError(false);
      }
    }).catch(() => {
      if (!abort.signal.aborted) { setData(null); setError(true); }
    });
    return () => abort.abort();
  }, [open]);
  const result = data && dividendCashflow(holdings, data, taipeiDay(Date.now()));
  const symbols = [...new Set(holdings.filter(h => h.market === 'TW').map(h => h.symbol))];
  const covered = data ? symbols.filter(symbol => Object.hasOwn(data, symbol)).length : 0;
  return <details className="console-card upcoming-events" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>台股股息現金流 · 未來 90 天</summary>
    <div className="upcoming-events-body">
      <p className="muted">合計所有台股帳戶（含已隱藏持股），同一股票合併股數與成本。以已公告除息日篩選，不代表 90 天內一定入帳。</p>
      {error ? <p role="status">股利資料暫時無法取得</p> : !result ? <p role="status">載入中…</p> : <>
        {covered ? <p>已收錄預估現金股利 NT$ {formatAmount(result.total)}</p> : <p role="status">目前持股尚無可用現金股利資料。</p>}
        {covered < symbols.length && <p className="muted">現金股利資料涵蓋 {covered}/{symbols.length} 檔持股；未涵蓋不代表零股利。</p>}
        {covered > 0 && !result.upcoming.length && <p>已收錄資料中無符合的除息事件。</p>}
        <section className="upcoming-event-day" aria-label="預估股息">
          <ul>{result.upcoming.map((row, index) => <li key={index}>
            <div className="upcoming-event-head"><b>{row.symbol} · 除息 {row.exDate}</b><span>{row.paymentDate ? '發放 ' + row.paymentDate : '發放日未公告'}</span></div>
            <p>每股 NT$ {row.perShare} × {row.shares} 股 · 預估入帳 NT$ {formatAmount(row.amount)}</p>
            {row.premium !== '0' && <p>達補充保費門檻 · 預估 NT$ {formatAmount(row.premium)}</p>}
          </li>)}</ul>
        </section>
        <section className="upcoming-event-day" aria-label="個人殖利率">
          <h3>個人殖利率（Yield on Cost）</h3>
          <p className="muted">近 12 個月已除息每股現金股利合計 ÷ 加權平均成本；資料不足或過期顯示 —。</p>
          <ul>{result.yields.map(row => <li key={row.symbol}>{row.symbol} · {row.yieldPct === null ? '—' : formatAmount(row.yieldPct) + '%'} · 資料檢查 {row.checked ?? '未知'}</li>)}</ul>
        </section>
      </>}
      <p className="muted">FinMind 約 30 天逐檔更新，可能尚未涵蓋新公告。單筆現金股利達 NT$20,000 按 2.11% 試算，單次計費上限 NT$10,000,000；未區分所得類別及個人免扣資格。試算，實際以扣繳憑單為準；以目前持股估算，除息前異動不反映。</p>
    </div>
  </details>;
}
