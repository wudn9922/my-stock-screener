import {
  Money,
  decimalText,
  formatAmount,
  type Holding,
  type HoldingQuote,
  type HoldingValue,
} from './model';
import { formatHoldingPrice } from './displayFormat';
import { formatDateTime, toneClass } from '../ui/format';
function Signed({ value, percent = false }: { value: string | null; percent?: boolean }) {
  return (
    <span title={value ?? undefined} className={toneClass(value === null ? null : Number(value))}>
      {value !== null && new Money(value).gt(0) ? '+' : ''}
      {formatAmount(value)}
      {percent && value !== null ? '%' : ''}
    </span>
  );
}
export function HoldingPosition({
  holding,
  result,
  quote,
  name,
  pending,
  retained,
  net,
  onOpen,
  onHide,
}: {
  holding: Holding;
  result: HoldingValue;
  quote?: HoldingQuote;
  name?: string;
  pending: boolean;
  retained: boolean;
  net: boolean;
  onOpen: () => void;
  onHide: () => void;
}) {
  const quantity =
    holding.market === 'TW'
      ? decimalText(new Money(holding.quantity).div(1000)) + ' 張'
      : holding.quantity + (holding.market === 'US' ? ' 股' : '');
  return (
    <article className="holdings-position" aria-label={`${holding.symbol} 持股`}>
      <details>
        <summary aria-label={`${holding.symbol} 持股明細`} className="holding-compact-row">
          <span className="holding-identity">
            <button
              type="button"
              className="text-button holdings-symbol"
              disabled={holding.market === 'CRYPTO' && holding.symbol !== 'BTC/USDT'}
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                onOpen();
              }}
            >
              {holding.symbol}
            </button>
            <small>{name ?? '持股明細 ▾'}</small>
          </span>
          <span className="holding-number">
            <small>數量</small>
            <span>{quantity}</span>
          </span>
          <span className="holding-number">
            <small>市值</small>
            <span title={result.value ?? undefined}>{formatAmount(result.value)}</span>
          </span>
          <span className="holding-number">
            <small>未實現盈虧</small>
            <span>
              <Signed value={result.profit} />
            </span>
            <small>
              <Signed value={result.percent} percent />
            </small>
          </span>
        </summary>
        <dl>
          <div>
            <dt>現價</dt>
            <dd title={quote?.price}>
              {quote
                ? formatHoldingPrice(quote.price)
                : pending
                  ? '等候確認同步成本'
                  : '無報價'}
            </dd>
          </div>
          <div>
            <dt>
              {holding.market === 'TW'
                ? '持有張數'
                : holding.market === 'US'
                  ? '股數'
                  : pending
                    ? '上次確認幣數'
                    : '幣數'}
            </dt>
            <dd>{quantity}</dd>
          </div>
          <div>
            <dt>平均成本／{holding.market === 'CRYPTO' ? '幣' : '股'}</dt>
            <dd title={holding.averageCost}>{formatHoldingPrice(holding.averageCost)}</dd>
          </div>
          <div>
            <dt>市值</dt>
            <dd title={result.value ?? undefined}>{formatAmount(result.value)}</dd>
          </div>
          <div>
            <dt>未實現損益{net && holding.market === 'TW' ? '（淨額）' : ''}</dt>
            <dd>
              <Signed value={result.profit} />
            </dd>
          </div>
          <div>
            <dt>損益 %</dt>
            <dd>
              <Signed value={result.percent} percent />
            </dd>
          </div>
          <div>
            <dt>今日損益</dt>
            <dd>
              <Signed value={result.today} />
            </dd>
          </div>
          {net && holding.market === 'TW' && (
            <div>
              <dt>預估賣出成本</dt>
              <dd title={result.fee ?? undefined}>{formatAmount(result.fee)}</dd>
            </div>
          )}
        </dl>
        <details><summary>完整精度</summary><p className="holdings-muted">現價 {quote?.price ?? '—'} · 平均成本 {holding.averageCost} · 市值 {result.value ?? '—'} · 未實現盈虧 {result.profit ?? '—'} · 報酬率 {result.percent ?? '—'}% · 今日損益 {result.today ?? '—'} · 預估賣出成本 {result.fee ?? '—'}</p></details>
        <p className="holdings-muted">
          {holding.costCurrency} ·{' '}
          {quote ? `${quote.asOf} · ${quote.source}（非即時）` : '目前來源無可用報價'} · 持股更新{' '}
          {formatDateTime(holding.updatedAt)}
        </p>
        <button type="button" className="text-button" title="不顯示，也不計入總計" onClick={onHide}>
          隱藏
        </button>
      </details>
      {retained && quote && (
        <p className="holdings-retained" role="status">
          沿用上次報價 · {formatDateTime(quote.asOf, quote.asOf.length > 10)}
        </p>
      )}
      {pending && <p className="holdings-retained">上次確認持股；新餘額與成本待確認，暫不列入總計。</p>}
    </article>
  );
}
