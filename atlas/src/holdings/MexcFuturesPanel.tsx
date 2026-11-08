import type { MexcFutures } from '../../../supabase/functions/user-settings/mexcFuturesContract';
import { Money, formatAmount } from './model';
import { formatHoldingPrice, formatHoldingPnl } from './displayFormat';
import { formatDateTime } from '../ui/format';
import './mexcFutures.css';

const text = (value: string) => {
  const [whole, fraction] = value.split('.');
  return whole!.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (fraction ? '.' + fraction : '');
};
const signed = formatHoldingPnl;
const tone = (value: string) => (value.startsWith('-') ? 'mexc-futures-loss' : 'mexc-futures-profit');

export function futuresReturn(unrealized: string, margin: string): string {
  if (new Money(margin).isZero()) return '—';
  const percent = new Money(unrealized).div(margin).times(100);
  return (percent.isNegative() ? '' : '+') + percent.toFixed(2) + '%';
}

export function MexcFuturesPanel({
  futures,
  loading,
  failed,
  updatedAt,
}: {
  futures?: MexcFutures;
  loading?: boolean;
  failed?: boolean;
  updatedAt?: string;
}) {
  return (
    <section className="mexc-futures" aria-label="合約部位">
      <header>
        <h4>合約部位</h4>
        <small>合約權益計入美股總計</small>
      </header>
      {loading && <p role="status">合約部位讀取中…</p>}
      {updatedAt && (
        <p className="holdings-muted" data-testid="mexc-futures-time">
          合約資料時間 {formatDateTime(updatedAt)}
        </p>
      )}
      {failed && futures?.status !== 'unavailable' && (
        <p role="status">
          合約資料暫時無法讀取
          {futures?.status === 'ok' ? '，保留上次成功資料。' : '。'}
        </p>
      )}
      {!futures ? (
        !loading && !failed && <p role="status">等待讀取合約資料。</p>
      ) : futures.status === 'no-permission' ? (
        <p role="status">
          這把 API Key 沒有合約讀取權限。請到 MEXC 編輯 Key，勾選『合約－讀取』（不要勾交易）後重新同步。
        </p>
      ) : futures.status === 'unavailable' ? (
        <p role="status">合約資料暫時無法讀取。</p>
      ) : (
        <>
          {futures.skippedPositions > 0 && (
            <p role="note">有 {futures.skippedPositions} 筆合約資料格式不支援，未顯示。</p>
          )}
          {!futures.positions.length ? (
            <p role="status">
              {futures.skippedPositions ? '目前沒有可顯示的合約持倉。' : '目前沒有合約持倉。'}
            </p>
          ) : (
            <div className="mexc-futures-positions">
              {futures.positions.map((p, index) => (
                <article
                  key={`${p.symbol}:${p.side}:${index}`}
                  aria-label={`${p.symbol} ${p.side === 'long' ? '多單' : '空單'} 合約部位`}
                >
                  <div className="mexc-position-head">
                    <h5>
                      {p.symbol}{' '}
                      <span className={`mexc-futures-${p.side}`}>{p.side === 'long' ? '多' : '空'}</span>
                      <small>
                        {text(p.leverage)}× · {p.marginMode === 'isolated' ? '逐倉' : '全倉'}
                      </small>
                    </h5>
                    <div className={tone(p.unrealized)} aria-label="未實現盈虧／報酬率">
                      <strong title={p.unrealized}>
                        {signed(p.unrealized)} <small>USDT</small>
                      </strong>
                      <small>{futuresReturn(p.unrealized, p.margin)}</small>
                    </div>
                  </div>
                  <dl className="mexc-position-grid">
                    <div>
                      <dt>數量（{p.symbol.slice(0, -5)}）</dt>
                      <dd>{text(p.size)}</dd>
                    </div>
                    <div>
                      <dt>開倉均價</dt>
                      <dd title={p.entryPrice}>{formatHoldingPrice(p.entryPrice)}</dd>
                    </div>
                    <div>
                      <dt>標記價</dt>
                      <dd title={p.markPrice}>{formatHoldingPrice(p.markPrice)}</dd>
                    </div>
                    <div>
                      <dt>強平價</dt>
                      <dd title={p.liquidationPrice ?? undefined}>{p.liquidationPrice === null ? '—' : formatHoldingPrice(p.liquidationPrice)}</dd>
                    </div>
                  </dl>
                  <details className="mexc-position-more">
                    <summary>更多</summary>
                    <p className="holdings-muted">原始精度：未實現盈虧 {p.unrealized} USDT · 保證金 {p.margin} USDT；下列價格保留完整精度。</p>
                    <dl className="mexc-futures-values">
                      <div>
                        <dt>張數</dt>
                        <dd>{text(p.contracts)} 張</dd>
                      </div>
                      <div>
                        <dt>幣數</dt>
                        <dd>
                          {text(p.size)} {p.symbol.slice(0, -5)}
                        </dd>
                      </div>
                      <div>
                        <dt>槓桿與保證金模式</dt>
                        <dd>
                          {text(p.leverage)}× · {p.marginMode === 'isolated' ? '逐倉' : '全倉'}
                        </dd>
                      </div>
                      <div>
                        <dt>保證金</dt>
                        <dd title={p.margin}>{formatAmount(p.margin)} USDT</dd>
                      </div>
                      <div>
                        <dt>開倉均價</dt>
                        <dd>{text(p.entryPrice)} USDT</dd>
                      </div>
                      <div>
                        <dt>標記價</dt>
                        <dd>{text(p.markPrice)} USDT</dd>
                      </div>
                      <div>
                        <dt>強平價</dt>
                        <dd>{p.liquidationPrice === null ? '—' : text(p.liquidationPrice) + ' USDT'}</dd>
                      </div>
                    </dl>
                  </details>
                </article>
              ))}
            </div>
          )}
          <details>
            <summary>合約帳戶摘要</summary>
            <details><summary>完整精度</summary><p className="holdings-muted">權益 {futures.account.equity} · 可用 {futures.account.available} · 保證金 {futures.account.positionMargin} · 未實現盈虧 {futures.account.unrealized}</p></details>
            <dl className="mexc-futures-values" aria-label="合約帳戶摘要">
              <div>
                <dt>合約帳戶權益</dt>
                <dd title={futures.account.equity}>{formatAmount(futures.account.equity)} USDT</dd>
              </div>
              <div>
                <dt>可用</dt>
                <dd title={futures.account.available}>{formatAmount(futures.account.available)} USDT</dd>
              </div>
              <div>
                <dt>已用保證金</dt>
                <dd title={futures.account.positionMargin}>{formatAmount(futures.account.positionMargin)} USDT</dd>
              </div>
              <div>
                <dt>未實現盈虧合計</dt>
                <dd title={futures.account.unrealized} className={tone(futures.account.unrealized)}>
                  {signed(futures.account.unrealized)} USDT
                </dd>
              </div>
            </dl>
          </details>
        </>
      )}
    </section>
  );
}
