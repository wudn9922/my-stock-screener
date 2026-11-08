import type { ReactNode } from 'react';
import { formatDateTime } from '../ui/format';
import { formatAmount, type Holding, type HoldingValue, type FxRate } from './model';
import { portfolioTotals, type PortfolioAmount, type FuturesTotalInput } from './portfolioTotals';
export function HoldingsTotals({
  rows,
  futures,
  rates,
  pending,
  hidden,
  children,
}: {
  rows: { holding: Holding; result: HoldingValue }[];
  futures: FuturesTotalInput[];
  rates: FxRate[];
  pending: boolean;
  hidden: number;
  children?: ReactNode;
}) {
  const total = portfolioTotals(rows, futures, rates);
  const metrics = (value: PortfolioAmount, currency: string) => (
    <dl className="portfolio-total-values">
      <div>
        <dt>{value.missing ? '市值／權益（有報價部分）' : '總市值／權益'}</dt>
        <dd title={value.value}>
          {formatAmount(value.value)} {currency}
        </dd>
      </div>
      <div>
        <dt>總成本／權益本金</dt>
        <dd title={value.cost}>
          {formatAmount(value.cost)} {currency}
        </dd>
      </div>
      <div>
        <dt>{value.missing ? '未實現盈虧（有報價部分）' : '未實現盈虧'}</dt>
        <dd title={value.profit} className={value.profit.startsWith('-') ? 'mexc-futures-loss' : 'mexc-futures-profit'}>
          {formatAmount(value.profit)} {currency}{' '}
          <small title={value.percent ?? undefined}>（{value.percent === null ? '—' : formatAmount(value.percent) + '%'}）</small>
        </dd>
      </div>
    </dl>
  );
  return (
    <section className="console-card portfolio-totals" aria-label="持股總計">
      <h3>持股總計</h3>
      <div className="portfolio-total-groups">
        <section aria-label="美股總計">
          <h4>
            美股 <small>含加密資產</small>
          </h4>
          {metrics(total.foreign, 'USD')}
          {!total.factor && (
            <div aria-label="USDT 分列總計">
              <p className="holdings-muted">
                目前無 USDT/TWD 匯率或 USD/TWD 匯率，USDT 部位另列，未假設 USDT 與 USD 等值。
              </p>
              {metrics(total.usdt, 'USDT')}
            </div>
          )}
          {total.factor && (
            <p className="holdings-muted">
              換算基準：1 USDT＝{formatAmount(total.factor, 6)} USD，依同頁 USDT/TWD ÷ USD/TWD。
            </p>
          )}
          {total.excludedFutures > 0 && (
            <p role="note">
              不含合約（暫時無法讀取）：{total.excludedFutures} 個帳戶；無權限或尚在載入亦不計入。
            </p>
          )}
        </section>
        <section aria-label="台股總計">
          <h4>台股</h4>
          {metrics(total.tw, 'TWD')}
        </section>
      </div>
      {(pending || total.tw.missing + total.foreign.missing > 0) && (
        <p role="note">{pending ? '總計尚未完整：部分 MEXC 餘額與成本尚未確認，暫不納入總計。' : '總計尚未完整：缺少報價的持股未納入。'}</p>
      )}
      {hidden > 0 && (
        <p className="holdings-muted" data-testid="holdings-hidden-total-note">
          已隱藏 {hidden} 檔持股，未計入總計與小計。
        </p>
      )}
      {rates.map((rate) => (
        <p className="holdings-muted" key={rate.currency}>
          1 {rate.currency}＝{formatAmount(rate.twd, 4)} TWD · 匯率日期 {rate.asOf}
        </p>
      ))}
      <details>
        <summary>加總與換算說明</summary>
        <p className="holdings-muted">
          美股區包含美股券商、加密現貨與 MEXC 合約帳戶權益。合約以 equity 加入一次，權益本金為 equity
          減帳戶未實現盈虧；不再加保證金、名目部位或各筆持倉損益。沒有 USDT
          換算匯率時分列，不混加幣別。未讀到或讀取失敗的合約不計入總計，持倉區仍可查看註明時間的上次資料。
        </p>
      </details>
      {futures.filter(f => f.at).map((f, index) => <p className="holdings-muted" key={f.id}>合約帳戶 {index + 1} 資料時間 {formatDateTime(f.at!)}{f.failed ? '（讀取失敗，未計入）' : ''}</p>)}
      {children}
    </section>
  );
}
