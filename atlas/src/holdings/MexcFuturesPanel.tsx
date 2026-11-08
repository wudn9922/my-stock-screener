import type { MexcFutures } from '../../../supabase/functions/user-settings/mexcFuturesContract';
import { Money } from './model';
import { formatDateTime } from '../ui/format';
import './mexcFutures.css';

const text = (value: string) => {
  const [whole, fraction] = value.split('.');
  return whole!.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (fraction ? '.' + fraction : '');
};
const signed = (value: string) => {
  const rounded = new Money(value).toDecimalPlaces(8, Money.ROUND_HALF_UP).toFixed();
  return (rounded.startsWith('-') ? '' : '+') + text(rounded);
};
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
        <small>僅供查看，未計入持股總值</small>
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
          <dl className="mexc-futures-values" aria-label="合約帳戶摘要">
            <div>
              <dt>合約帳戶權益</dt>
              <dd>{text(futures.account.equity)} USDT</dd>
            </div>
            <div>
              <dt>可用</dt>
              <dd>{text(futures.account.available)} USDT</dd>
            </div>
            <div>
              <dt>已用保證金</dt>
              <dd>{text(futures.account.positionMargin)} USDT</dd>
            </div>
            <div>
              <dt>未實現盈虧合計</dt>
              <dd className={tone(futures.account.unrealized)}>{signed(futures.account.unrealized)} USDT</dd>
            </div>
          </dl>
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
                  <h5>
                    {p.symbol}{' '}
                    <span className={`mexc-futures-${p.side}`}>{p.side === 'long' ? '多' : '空'}</span>
                  </h5>
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
                      <dt>開倉均價</dt>
                      <dd>{text(p.entryPrice)} USDT</dd>
                    </div>
                    <div>
                      <dt>標記價</dt>
                      <dd>{text(p.markPrice)} USDT</dd>
                    </div>
                    <div>
                      <dt>槓桿與保證金模式</dt>
                      <dd>
                        {text(p.leverage)}× · {p.marginMode === 'isolated' ? '逐倉' : '全倉'}
                      </dd>
                    </div>
                    <div>
                      <dt>保證金</dt>
                      <dd>{text(p.margin)} USDT</dd>
                    </div>
                    <div>
                      <dt>未實現盈虧／報酬率</dt>
                      <dd className={tone(p.unrealized)}>
                        {signed(p.unrealized)} USDT · {futuresReturn(p.unrealized, p.margin)}
                      </dd>
                    </div>
                    <div>
                      <dt>強平價</dt>
                      <dd>{p.liquidationPrice === null ? '—' : text(p.liquidationPrice) + ' USDT'}</dd>
                    </div>
                  </dl>
                </article>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
