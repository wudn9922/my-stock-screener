import { useEffect, useState, useSyncExternalStore } from 'react';
import type { PageProps } from '../pages/pageProps';
import { accessState } from '../auth/access';
import { ImportHoldings } from './ImportHoldings';
import { holdingsStore } from './store';
import { loadHoldingQuotes, loadHoldingsDirectory } from './quotes';
import {
  Money,
  decimalText,
  summarizeHoldings,
  valueHolding,
  type Broker,
  type FxRate,
  type Holding,
  type HoldingAccount,
  type HoldingQuote,
} from './model';
import type { ImportSymbol } from './import';
import { formatDateTime, toneClass } from '../ui/format';
import './holdings.css';

const BROKERS: { id: Broker; name: string; kind: HoldingAccount['kind']; currency: HoldingAccount['currency'] }[] = [
  { id: 'cathay', name: '國泰證券', kind: 'tw-broker', currency: 'TWD' },
  { id: 'sinopac', name: '永豐金證券', kind: 'tw-broker', currency: 'TWD' },
  { id: 'kgi', name: '凱基證券', kind: 'tw-broker', currency: 'TWD' },
  { id: 'pocket', name: '口袋證券', kind: 'tw-broker', currency: 'TWD' },
  { id: 'mexc', name: 'MEXC', kind: 'crypto-exchange', currency: 'USDT' },
  { id: 'firstrade', name: 'Firstrade', kind: 'us-broker', currency: 'USD' },
];
function amount(value: string | null | undefined, digits = 2) {
  if (value == null) return '—';
  const [integer, fraction] = new Money(value).toFixed(digits).split('.');
  return integer!.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (fraction === undefined ? '' : `.${fraction}`);
}
function Signed({ value, percent = false }: { value: string | null; percent?: boolean }) {
  return (
    <span className={toneClass(value === null ? null : Number(value))}>
      {value !== null && new Money(value).gt(0) ? '+' : ''}
      {amount(value)}
      {percent && value !== null ? '%' : ''}
    </span>
  );
}
export function HoldingsPanel({ openChart, navigate }: Pick<PageProps, 'openChart' | 'navigate'>) {
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  const store = holdingsStore();
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const [directory, setDirectory] = useState<ImportSymbol[] | null>(null),
    [directoryError, setDirectoryError] = useState(false),
    [directoryAttempt, setDirectoryAttempt] = useState(0);
  const [accountId, setAccountId] = useState(''),
    [draft, setDraft] = useState<HoldingAccount | null>(null),
    [creating, setCreating] = useState(false);
  const [broker, setBroker] = useState<Broker>('cathay'),
    [name, setName] = useState(''),
    [editing, setEditing] = useState(false),
    [initial, setInitial] = useState<Holding[] | undefined>();
  const [message, setMessage] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const [quotes, setQuotes] = useState<Record<string, HoldingQuote>>({}),
    [rates, setRates] = useState<FxRate[]>([]),
    [quotesBusy, setQuotesBusy] = useState(false);
  const [net, setNet] = useState(false),
    [discount, setDiscount] = useState('1'),
    [sort, setSort] = useState<'value' | 'profit' | 'percent'>('value');
  useEffect(() => {
    void store.reload();
  }, [store]);
  useEffect(() => {
    let active = true;
    void loadHoldingsDirectory()
      .then((rows) => {
        if (active) {
          setDirectory(rows);
          setDirectoryError(!rows.length);
        }
      })
      .catch(() => {
        if (active) setDirectoryError(true);
      });
    return () => {
      active = false;
    };
  }, [directoryAttempt]);
  const { document } = snapshot;
  const selected = document.accounts.find((entry) => entry.account.id === accountId) ?? document.accounts[0];
  const symbols = [...new Set(document.accounts.flatMap((entry) => entry.holdings.map((h) => h.symbol)))]
    .sort()
    .join(',');
  useEffect(() => {
    const controller = new AbortController();
    if (!symbols) {
      setQuotes({});
      setRates([]);
      setQuotesBusy(false);
      return;
    }
    setQuotesBusy(true);
    void loadHoldingQuotes(symbols.split(','), controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setQuotes(result.quotes);
          setRates(result.rates);
        }
      })
      .catch(() => {
        /* Missing quotes remain visibly unavailable. */
      })
      .finally(() => {
        if (!controller.signal.aborted) setQuotesBusy(false);
      });
    return () => controller.abort();
  }, [symbols]);
  const validDiscount = /^(?:0(?:\.\d{1,4})?|1(?:\.0{1,4})?)$/.test(discount);
  const costs = {
    enabled: net && validDiscount,
    discount: validDiscount ? discount : '1',
    etfs: new Set(directory?.filter((entry) => entry.etf).map((entry) => entry.symbol)),
  };
  const all = document.accounts.flatMap((entry) =>
    entry.holdings.map((holding) => ({ holding, result: valueHolding(holding, quotes[holding.symbol], costs) })),
  );
  const summary = summarizeHoldings(all, rates);
  const rows = all
    .filter((row) => row.holding.accountId === selected?.account.id)
    .sort((a, b) => {
      const av = a.result[sort],
        bv = b.result[sort];
      return av === null
        ? bv === null
          ? a.holding.symbol.localeCompare(b.holding.symbol)
          : 1
        : bv === null
          ? -1
          : new Money(bv).cmp(av);
    });
  const blocked = snapshot.loading || !!snapshot.error || busy;
  const importAccount = draft ?? selected?.account;
  const subtotal = (items: typeof all) => (
    <>
      {[...summarizeHoldings(items, rates).currencies].map(([currency, value]) => (
        <div key={currency} className="holdings-subtotal">
          <span>
            {currency} 市值 {amount(decimalText(value.value))}
          </span>
          <span>
            未實現損益 <Signed value={decimalText(value.profit)} />
          </span>
          <span>
            今日損益 <Signed value={value.missingToday ? null : decimalText(value.today)} />
          </span>
          {value.missing > 0 && <span>部分持股無報價，損益僅含有報價項目</span>}
        </div>
      ))}
    </>
  );
  return (
    <section className="holdings-panel" aria-label="持股">
      <p role="note" className="holdings-storage">
        {access.storageMode === 'cloud'
          ? '持股儲存於你的雲端帳號，不在此裝置留下持股副本。'
          : access.storageMode === 'device'
            ? '持股只保存在此裝置的帳號空間，不會上傳。'
            : '訪客持股只存在記憶體，重新開啟就會消失。'}
      </p>
      {snapshot.error && <p role="alert">{snapshot.error}</p>}
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      <div className="holdings-toolbar">
        <button
          className="primary-button"
          type="button"
          disabled={blocked || editing || creating}
          onClick={() => {
            setCreating(true);
            setEditing(false);
            setDraft(null);
            setName('');
          }}
        >
          新增帳戶
        </button>
        {access.storageMode === 'cloud' && (
          <button
            type="button"
            className="ghost-button"
            disabled={snapshot.loading || busy || editing || creating}
            onClick={() => void store.reload()}
          >
            重新載入持股
          </button>
        )}
      </div>
      {snapshot.loading && <p role="status">載入持股中…</p>}
      {creating && (
        <form
          className="console-card"
          aria-label="新增持股帳戶"
          onSubmit={(event) => {
            event.preventDefault();
            const chosen = BROKERS.find((entry) => entry.id === broker)!;
            setDraft({
              id: crypto.randomUUID(),
              name: name.trim() || chosen.name,
              broker,
              kind: chosen.kind,
              currency: chosen.currency,
            });
            setCreating(false);
            setEditing(true);
            setInitial(undefined);
          }}
        >
          <h3>選擇帳戶</h3>
          <div className="holdings-fields">
            <label>
              券商／交易所
              <select
                aria-label="券商／交易所"
                value={broker}
                onChange={(event) => setBroker(event.target.value as Broker)}
              >
                {BROKERS.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.name} · {entry.currency}
                  </option>
                ))}
              </select>
            </label>
            <label>
              帳戶名稱
              <input
                aria-label="帳戶名稱"
                maxLength={40}
                autoComplete="off"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={BROKERS.find((entry) => entry.id === broker)?.name}
              />
            </label>
          </div>
          <div className="holdings-toolbar">
            <button type="submit" className="primary-button" disabled={!directory?.length || blocked}>
              繼續匯入
            </button>
            <button type="button" className="ghost-button" onClick={() => setCreating(false)}>
              取消
            </button>
          </div>
        </form>
      )}
      {directoryError && (
        <p role="alert">
          股票目錄無法完整讀取，請重試；找不到的代號不會自動匯入。
          <button
            type="button"
            className="ghost-button"
            onClick={() => {
              setDirectoryError(false);
              setDirectoryAttempt((attempt) => attempt + 1);
            }}
          >
            重試目錄
          </button>
        </p>
      )}
      {!directory && !directoryError && <p role="status">載入股票目錄中…</p>}
      {document.accounts.length > 0 && (
        <>
          <section className="console-card" aria-label="持股總計">
            <h3>持股總計</h3>
            <div className="holdings-metrics">
              <div>
                <span>台幣{summary.partial ? '可換算部分' : '總市值'}</span>
                <strong>{amount(summary.twd)} TWD</strong>
              </div>
              <div>
                <span>未實現損益{net ? '（預估淨額）' : ''}</span>
                <strong>
                  <Signed value={summary.profitTwd} /> TWD
                </strong>
              </div>
            </div>
            {summary.partial && <p role="note">總計尚未完整：缺少報價或換算匯率的持股未納入。</p>}
            {all.some((row) => row.holding.costCurrency === 'USD') &&
              !rates.some((rate) => rate.currency === 'USD') &&
              !quotesBusy && <p className="holdings-muted">目前無 USD/TWD 匯率，USD 部位另列，尚未納入台幣總計。</p>}
            {rates.map((rate) => (
              <p className="holdings-muted" key={rate.currency}>
                1 {rate.currency}＝{amount(rate.twd, 4)} TWD · 匯率日期 {formatDateTime(rate.asOf, false)}
              </p>
            ))}
            {all.some((row) => row.holding.costCurrency === 'USDT') &&
              !rates.some((rate) => rate.currency === 'USDT') && (
                <p className="holdings-muted">目前無 USDT/TWD 匯率，USDT 部位另列，未假設 USDT 與 USD 等值。</p>
              )}
            <details>
              <summary>市場小計</summary>
              {(['TW', 'US', 'CRYPTO'] as const)
                .filter((market) => all.some((row) => row.holding.market === market))
                .map((market) => (
                  <div key={market}>
                    <h4>{{ TW: '台股', US: '美股', CRYPTO: '加密貨幣' }[market]}</h4>
                    {subtotal(all.filter((row) => row.holding.market === market))}
                  </div>
                ))}
            </details>
            <details>
              <summary>台股賣出成本試算</summary>
              <label className="holdings-check">
                <input type="checkbox" checked={net} onChange={(event) => setNet(event.target.checked)} />
                扣除預估賣出成本
              </label>
              <label>
                手續費折扣（1＝原價，0.28＝二八折）
                <input
                  aria-label="手續費折扣"
                  inputMode="decimal"
                  value={discount}
                  onChange={(event) => setDiscount(event.target.value)}
                />
              </label>
              {!validDiscount && <p role="alert">折扣請填 0～1，修正前暫停淨額試算。</p>}
              <p className="holdings-muted">
                手續費 0.1425% × 折扣；證交稅：股票 0.3%、ETF 0.1%。未計最低手續費、日沖或特殊商品減免，實際以券商為準。
              </p>
            </details>
          </section>
          <div className="holdings-fields">
            <label>
              持股帳戶
              <select
                aria-label="持股帳戶"
                value={selected?.account.id ?? ''}
                disabled={editing || creating}
                onChange={(event) => {
                  setAccountId(event.target.value);
                  setDraft(null);
                }}
              >
                {document.accounts.map((entry) => (
                  <option key={entry.account.id} value={entry.account.id}>
                    {entry.account.name} · {entry.account.currency}
                  </option>
                ))}
              </select>
            </label>
            <label>
              排序
              <select
                aria-label="持股排序"
                value={sort}
                onChange={(event) => setSort(event.target.value as typeof sort)}
              >
                <option value="value">市值</option>
                <option value="profit">損益金額</option>
                <option value="percent">損益 %</option>
              </select>
            </label>
          </div>
          <div className="holdings-toolbar">
            <button
              type="button"
              className="primary-button"
              disabled={blocked || !directory?.length || editing || creating}
              onClick={() => {
                setDraft(null);
                setEditing(true);
                setInitial(undefined);
              }}
            >
              匯入持股
            </button>
            <button
              type="button"
              className="ghost-button"
              disabled={blocked || !directory?.length || editing || creating}
              onClick={() => {
                setDraft(null);
                setEditing(true);
                setInitial(selected?.holdings);
              }}
            >
              修正持股
            </button>
            <button
              type="button"
              className="text-button"
              disabled={blocked || editing || creating}
              onClick={() => {
                if (!selected || !confirm('刪除此帳戶與全部持股？此操作無法復原。')) return;
                setBusy(true);
                void store
                  .removeAccount(selected.account.id)
                  .then(() => {
                    setEditing(false);
                    setDraft(null);
                  })
                  .catch(() => setError('刪除未完成，請重新載入持股後重試。'))
                  .finally(() => setBusy(false));
              }}
            >
              刪除帳戶
            </button>
          </div>
        </>
      )}
      {editing && importAccount && directory && (
        <ImportHoldings
          key={importAccount.id}
          account={importAccount}
          directory={directory}
          initial={initial}
          onCancel={() => {
            setEditing(false);
            setDraft(null);
          }}
          onSave={async (account, holdings) => {
            await store.replace(account, holdings);
            setAccountId(account.id);
            setDraft(null);
            setEditing(false);
            setError('');
            setMessage('已取代此帳戶持股。');
          }}
        />
      )}
      {selected && (
        <section className="console-card" aria-label="帳戶損益">
          <h3>
            {selected.account.name} · {selected.account.currency}
          </h3>
          {subtotal(all.filter((row) => row.holding.accountId === selected.account.id))}
          {quotesBusy && <p role="status">讀取報價中…</p>}
          <div className="holdings-cards">
            {rows.map(({ holding, result }) => {
              const quote = quotes[holding.symbol],
                entry = directory?.find((entry) => entry.symbol === holding.symbol);
              return (
                <article className="holdings-position" key={holding.id} aria-label={`${holding.symbol} 持股`}>
                  <button
                    type="button"
                    className="text-button holdings-symbol"
                    disabled={holding.market === 'CRYPTO' && holding.symbol !== 'BTC/USDT'}
                    onClick={() => {
                      if (holding.market === 'CRYPTO') navigate({ page: 'world', view: 'commodities' });
                      else openChart({ symbol: holding.symbol });
                    }}
                  >
                    {holding.symbol} {entry?.name ?? ''}
                  </button>
                  <dl>
                    <div>
                      <dt>現價</dt>
                      <dd>{quote ? amount(quote.price, holding.market === 'CRYPTO' ? 4 : 2) : '無報價'}</dd>
                    </div>
                    <div>
                      <dt>{holding.market === 'TW' ? '持有張數' : holding.market === 'US' ? '股數' : '幣數'}</dt>
                      <dd>
                        {holding.market === 'TW'
                          ? decimalText(new Money(holding.quantity).div(1000)) + ' 張'
                          : holding.quantity + (holding.market === 'US' ? ' 股' : '')}
                      </dd>
                    </div>
                    <div>
                      <dt>平均成本／{holding.market === 'CRYPTO' ? '幣' : '股'}</dt>
                      <dd>{amount(holding.averageCost, 4)}</dd>
                    </div>
                    <div>
                      <dt>市值</dt>
                      <dd>{amount(result.value)}</dd>
                    </div>
                    <div>
                      <dt>未實現損益{net && validDiscount && holding.market === 'TW' ? '（淨額）' : ''}</dt>
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
                        <dd>{amount(result.fee)}</dd>
                      </div>
                    )}
                  </dl>
                  <p className="holdings-muted">
                    {holding.costCurrency} ·{' '}
                    {quote ? `${quote.asOf} · ${quote.source}（非即時）` : '目前來源無可用報價'} · 持股更新{' '}
                    {formatDateTime(holding.updatedAt)}
                  </p>
                </article>
              );
            })}
          </div>
          {!rows.length && <p>此帳戶目前沒有持股，可匯入庫存表格。</p>}
        </section>
      )}
      {!document.accounts.length && !editing && !creating && !snapshot.loading && (
        <p>尚未匯入持股。先新增帳戶，再上傳檔案或貼上庫存表格。</p>
      )}
    </section>
  );
}
