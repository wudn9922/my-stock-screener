import { useEffect, useState, useSyncExternalStore } from 'react';
import { chartList } from '../app/listContext';
import type { PageProps } from '../pages/pageProps';
import { accessState } from '../auth/access';
import { ImportHoldings } from './ImportHoldings';
import { holdingsStore } from './store';
import { loadHoldingQuotes, loadHoldingsDirectory } from './quotes';
import {
  Money,
  decimalText,
  formatAmount,
  summarizeHoldings,
  valueHolding,
  type FxRate,
  type Holding,
  type HoldingAccount,
  type HoldingQuote,
} from './model';
import type { ImportSymbol } from './import';
import { toneClass } from '../ui/format';
import './holdings.css';
import { MexcPanel } from './MexcPanel';
import { MexcFuturesPanel } from './MexcFuturesPanel';
import { HoldingPosition } from './HoldingPosition';
import { HoldingsTotals } from './HoldingsTotals';
import { retainQuotes, retainRates } from './quoteRetention';
import { useFuturesRefresh } from './useFuturesRefresh';
import { mexcStore } from './mexc';
import { HiddenHoldings, VisibilitySettings } from './HoldingsVisibility';
import {
  forgetAccount,
  hiddenReason,
  hide,
  holdingKey,
  holdingsVisibility,
  unhide,
  type HiddenReason,
} from './visibility';

const CSV_MARKETS: {
  key: string;
  label: string;
  kind: HoldingAccount['kind'];
  currency: HoldingAccount['currency'];
}[] = [
  { key: 'tw', label: '台股', kind: 'tw-broker', currency: 'TWD' },
  { key: 'us', label: '美股', kind: 'us-broker', currency: 'USD' },
  {
    key: 'crypto',
    label: '加密貨幣',
    kind: 'crypto-exchange',
    currency: 'USDT',
  },
];
const amount = formatAmount;
function Signed({ value, percent = false }: { value: string | null; percent?: boolean }) {
  return (
    <span title={value ?? undefined} className={toneClass(value === null ? null : Number(value))}>
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
  const mexc = mexcStore();
  const mexcSnapshot = useSyncExternalStore(mexc.subscribe, mexc.getSnapshot);
  useFuturesRefresh(
    mexc,
    mexcSnapshot.state.connections
      .map((c) => c.accountId)
      .sort()
      .join(','),
    snapshot.loading || mexcSnapshot.loading || !!snapshot.error,
    Object.values(mexcSnapshot.futuresUpdatedAt).join(','),
  );
  const visibility = holdingsVisibility();
  const visibilityPreferences = useSyncExternalStore(visibility.subscribe, visibility.getSnapshot);
  const [directory, setDirectory] = useState<ImportSymbol[] | null>(null),
    [directoryError, setDirectoryError] = useState(false),
    [directoryAttempt, setDirectoryAttempt] = useState(0);
  const [accountId, setAccountId] = useState(''),
    [draft, setDraft] = useState<HoldingAccount | null>(null),
    [creating, setCreating] = useState(false);
  const [method, setMethod] = useState('csv');
  const [brokerKey, setBrokerKey] = useState('mexc');
  const [marketKey, setMarketKey] = useState('tw'),
    [name, setName] = useState(''),
    [editing, setEditing] = useState(false),
    [initial, setInitial] = useState<Holding[] | undefined>(),
    [connecting, setConnecting] = useState(false);
  const [message, setMessage] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const [{ quotes, retained: retainedQuotes }, setQuoteBook] = useState<{
      quotes: Record<string, HoldingQuote>;
      retained: string[];
    }>({ quotes: {}, retained: [] }),
    [rates, setRates] = useState<FxRate[]>([]),
    [quotesBusy, setQuotesBusy] = useState(false);
  const [net, setNet] = useState(false),
    [discount, setDiscount] = useState('1'),
    [sort, setSort] = useState<'value' | 'profit' | 'percent'>('value');
  useEffect(() => {
    void store.reload();
  }, [store]);
  useEffect(() => {
    void mexc.load();
  }, [mexc]);
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
  useEffect(() => {
    if (selected && accountId !== selected.account.id) setAccountId(selected.account.id);
  }, [selected?.account.id, accountId]);
  const symbols = [...new Set(document.accounts.flatMap((entry) => entry.holdings.map((h) => h.symbol)))]
    .sort()
    .join(',');
  useEffect(() => {
    const controller = new AbortController();
    if (!symbols) {
      setQuoteBook({ quotes: {}, retained: [] });
      setRates([]);
      setQuotesBusy(false);
      return;
    }
    setQuotesBusy(true);
    void loadHoldingQuotes(symbols.split(','), controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setQuoteBook((previous) => retainQuotes(previous.quotes, result.quotes));
          setRates((previous) => retainRates(previous, result.rates));
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
  const pendingAccounts = new Set(
    mexcSnapshot.state.connections
      .filter((connection) => {
        const current = document.accounts.find((entry) => entry.account.id === connection.accountId);
        return current && (connection.needsReview || connection.holdingRevision !== current.revision);
      })
      .map((connection) => connection.accountId),
  );
  const holdingQuote = (holding: Holding) => {
    const connection = mexcSnapshot.state.connections.find((entry) => entry.accountId === holding.accountId);
    return connection ? connection.quotes[holding.symbol] : quotes[holding.symbol];
  };
  const evaluated = document.accounts.flatMap((entry) =>
    entry.holdings.map((holding) => {
      const result = valueHolding(holding, holdingQuote(holding), costs);
      return {
        holding,
        result,
        hidden: hiddenReason(holding, entry.account, result.value, visibilityPreferences),
      };
    }),
  );
  // Hidden holdings are a view preference: every total, subtotal and note below uses `all` (visible only).
  const all = evaluated.filter((row) => row.hidden === null);
  const hiddenRows = evaluated.filter(
    (row): row is (typeof evaluated)[number] & { hidden: HiddenReason } => row.hidden !== null,
  );
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
  const selectedHidden = hiddenRows
    .filter((row) => row.holding.accountId === selected?.account.id)
    .sort((a, b) => a.holding.symbol.localeCompare(b.holding.symbol));
  const blocked = snapshot.loading || !!snapshot.error || busy || mexcSnapshot.busy;
  const importAccount = draft ?? selected?.account;
  const subtotal = (items: typeof all) => (
    <>
      {[...summarizeHoldings(items, rates).currencies].map(([currency, value]) => (
        <div key={currency} className="holdings-subtotal">
          <span title={decimalText(value.value)}>
            {currency} 市值 {amount(decimalText(value.value))}
          </span>
          <span>
            未實現損益 <Signed value={decimalText(value.profit)} />
          </span>
          <span>
            今日損益 <Signed value={value.missingToday ? null : decimalText(value.today)} />
          </span>
          {value.missing > 0 && (
            <span>
              {items.some((item) => pendingAccounts.has(item.holding.accountId))
                ? '等候確認 MEXC 餘額與成本，暫不估值'
                : '部分持股無報價，損益僅含有報價項目'}
            </span>
          )}
        </div>
      ))}
    </>
  );
  return (
    <section className="holdings-panel" aria-label="持股">
      {document.accounts.length > 0 && (
        <>
          <div className="holdings-fields holdings-selectors">
            <label>
              持股帳戶
              <select
                aria-label="持股帳戶"
                value={selected?.account.id ?? ''}
                disabled={editing || creating || connecting || mexcSnapshot.busy}
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
        </>
      )}
      {selected && (
        <section className="holdings-account" aria-label="帳戶損益">
          <h3 className="holdings-sr-only">
            {selected.account.name} · {selected.account.currency}
          </h3>
          {selected.account.broker === 'mexc' &&
            mexcSnapshot.state.connections.some((c) => c.accountId === selected.account.id) && (
              <MexcFuturesPanel
                futures={mexcSnapshot.accountFutures[selected.account.id]}
                loading={mexcSnapshot.futuresLoading[selected.account.id]}
                failed={mexcSnapshot.futuresErrors[selected.account.id]}
                updatedAt={mexcSnapshot.futuresUpdatedAt[selected.account.id]}
              />
            )}
          {quotesBusy && (
            <p role="status" className="holdings-muted">
              讀取報價中，保留上次數值…
            </p>
          )}
          <div className="holdings-cards">
            {rows.map(({ holding, result }) => (
              <HoldingPosition
                key={holding.id}
                holding={holding}
                result={result}
                quote={holdingQuote(holding)}
                name={directory?.find((entry) => entry.symbol === holding.symbol)?.name}
                pending={pendingAccounts.has(holding.accountId)}
                net={net && validDiscount}
                retained={(mexcSnapshot.accountRetainedQuotes[holding.accountId] ?? retainedQuotes).includes(
                  holding.symbol,
                )}
                onOpen={() =>
                  holding.market === 'CRYPTO'
                    ? navigate({ page: 'world', view: 'commodities' })
                    : openChart({
                        symbol: holding.symbol,
                        list: chartList(
                          `${selected.account.name} 持股`,
                          rows
                            .filter((row) => row.holding.market !== 'CRYPTO')
                            .map((row) => ({ symbol: row.holding.symbol })),
                        ),
                      })
                }
                onHide={() =>
                  visibility.update((current) => hide(current, holdingKey(holding.accountId, holding.symbol)))
                }
              />
            ))}
          </div>
          {!rows.length && (
            <p>
              {pendingAccounts.has(selected.account.id)
                ? '請先確認 MEXC 餘額與平均成本，再計算持股損益。'
                : selectedHidden.length
                  ? '此帳戶的持股都已隱藏，可在下方「已隱藏」取消隱藏。'
                  : '此帳戶目前沒有持股，可匯入庫存表格。'}
            </p>
          )}
        </section>
      )}
      {document.accounts.length > 0 && (
        <HoldingsTotals
          rows={all.filter((row) => !pendingAccounts.has(row.holding.accountId))}
          rates={rates}
          futures={mexcSnapshot.state.connections.map((c) => ({
            id: c.accountId,
            data: mexcSnapshot.accountFutures[c.accountId],
            failed: mexcSnapshot.futuresErrors[c.accountId],
            at: mexcSnapshot.futuresUpdatedAt[c.accountId],
          }))}
          pending={pendingAccounts.size > 0}
          hidden={hiddenRows.length}
        >
          <details data-testid="holdings-spot-subtotals">
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
              手續費 0.1425% × 折扣；證交稅：股票 0.3%、ETF
              0.1%。未計最低手續費、日沖或特殊商品減免，實際以券商為準。
            </p>
          </details>
        </HoldingsTotals>
      )}
      {snapshot.error && <p role="alert">{snapshot.error}</p>}
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      <div className="holdings-toolbar">
        <button
          className="primary-button"
          type="button"
          disabled={blocked || editing || creating || connecting}
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
            disabled={snapshot.loading || busy || editing || creating || connecting || mexcSnapshot.busy}
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
            if (method !== 'csv' || !name.trim()) return;
            const chosen = CSV_MARKETS.find(
              (entry) => entry.key === marketKey,
            )!;
            setDraft({
              id: crypto.randomUUID(),
              name: name.trim(),
              broker: 'other',
              kind: chosen.kind,
              currency: chosen.currency,
            });
            setCreating(false);
            setEditing(true);
            setInitial(undefined);
          }}
        >
          <h3>新增帳戶</h3>
          <label>
            新增方式
            <select
              aria-label="新增方式"
              value={method}
              onChange={(event) => setMethod(event.target.value)}
            >
              <option value="api">API 自動同步</option>
              <option value="csv">匯入 CSV</option>
            </select>
          </label>
          <p className="holdings-muted">永豐美股請用匯入 CSV</p>
          <div className="holdings-fields">
            {method === 'api' ? (
              <label>
                API 服務
                <select
                  aria-label="API 服務"
                  value={brokerKey}
                  onChange={(event) => setBrokerKey(event.target.value)}
                >
                  <option value="mexc">MEXC（加密貨幣）</option>
                  <option value="sinopac">永豐證券（台股）· 即將推出</option>
                </select>
              </label>
            ) : (
              <label>
                市場
                <select
                  aria-label="帳戶市場"
                  value={marketKey}
                  onChange={(event) => setMarketKey(event.target.value)}
                >
                  {CSV_MARKETS.map((entry) => (
                    <option key={entry.key} value={entry.key}>
                      {entry.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label>
              帳戶名稱
              <input
                aria-label="帳戶名稱"
                maxLength={40}
                autoComplete="off"
                value={name}
                onChange={(event) => setName(event.target.value)}
                required={method === 'csv'}
                placeholder={method === 'csv' ? '例如：永豐美股帳戶' : brokerKey === 'sinopac' ? '永豐證券' : 'MEXC'}
              />
            </label>
          </div>
          <div className="holdings-toolbar">
            {method === 'csv' && (
              <button
                type="submit"
                className="primary-button"
                disabled={!name.trim() || !directory?.length || blocked}
              >
                繼續匯入
              </button>
            )}
            {method === 'api' && brokerKey === 'sinopac' && (
              <button type="button" className="primary-button" disabled>
                即將推出
              </button>
            )}
            {method === 'api' && brokerKey === 'mexc' && (
              <button
                type="button"
                className="primary-button"
                disabled={
                  access.storageMode !== 'cloud' ||
                  blocked ||
                  mexcSnapshot.loading ||
                  !mexcSnapshot.state.configured
                }
                onClick={() => {
                  setDraft({
                    id: crypto.randomUUID(),
                    name: name.trim() || 'MEXC',
                    broker: 'mexc',
                    kind: 'crypto-exchange',
                    currency: 'USDT',
                  });
                  setCreating(false);
                  setConnecting(true);
                  setEditing(false);
                }}
              >
                使用 MEXC API 連線
              </button>
            )}
            <button
              type="button"
              className="ghost-button"
              onClick={() => setCreating(false)}
            >
              取消
            </button>
          </div>
          {method === 'api' &&
            brokerKey === 'mexc' &&
            access.storageMode !== 'cloud' && (
              <p className="holdings-muted">
                自動讀取 MEXC 需要雲端儲存授權；目前仍可上傳或貼上持股。
              </p>
            )}
          {method === 'api' &&
            brokerKey === 'mexc' &&
            access.storageMode === 'cloud' &&
            !mexcSnapshot.state.configured && (
              <p role="note">
                MEXC API
                服務尚未啟用，維護者完成設定後即可連線；目前可先匯入持股。
              </p>
            )}
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
          <div className="holdings-toolbar">
            <button
              type="button"
              className="primary-button"
              disabled={blocked || !directory?.length || editing || creating || connecting}
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
              disabled={blocked || !directory?.length || editing || creating || connecting}
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
              disabled={blocked || editing || creating || connecting}
              onClick={() => {
                if (!selected || !confirm('刪除此帳戶與全部持股？此操作無法復原。')) return;
                setBusy(true);
                const removed = selected.account.id;
                void store
                  .removeAccount(removed)
                  .then(() => {
                    visibility.update((current) => forgetAccount(current, removed));
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
      {connecting && draft && (
        <MexcPanel
          key={draft.id}
          account={draft}
          disabled={snapshot.loading || !!snapshot.error || busy}
          onCancel={() => {
            setConnecting(false);
            setDraft(null);
          }}
          onConnected={() => {
            setAccountId(draft.id);
            setConnecting(false);
            setDraft(null);
          }}
        />
      )}
      {!connecting && selected?.account.broker === 'mexc' && access.storageMode === 'cloud' && (
        <MexcPanel
          key={selected.account.id}
          account={selected.account}
          confirmed={selected}
          disabled={snapshot.loading || !!snapshot.error || busy || editing || creating}
        />
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
        <section className="console-card" aria-label="帳戶設定">
          <details>
            <summary>帳戶小計</summary>
            {subtotal(all.filter((row) => row.holding.accountId === selected.account.id))}
          </details>
          <VisibilitySettings
            key={selected.account.id}
            account={selected.account}
            rules={visibilityPreferences.accounts[selected.account.id]}
            storageMode={access.storageMode}
            onChange={(rules) =>
              visibility.update((current) => ({
                ...current,
                accounts: { ...current.accounts, [selected.account.id]: rules },
              }))
            }
          />
          <HiddenHoldings
            key={selected.account.id}
            account={selected.account}
            rows={selectedHidden}
            rules={visibilityPreferences.accounts[selected.account.id]}
            onUnhide={(holding, ruleStillHides) =>
              visibility.update((current) =>
                unhide(current, holdingKey(holding.accountId, holding.symbol), ruleStillHides),
              )
            }
          />
        </section>
      )}
      {!document.accounts.length && !editing && !creating && !connecting && !snapshot.loading && (
        <p>尚未匯入持股。先新增帳戶，再上傳檔案或貼上庫存表格。</p>
      )}
      <p role="note" className="holdings-storage">
        {access.storageMode === 'cloud'
          ? '持股儲存於你的雲端帳號，不在此裝置留下持股副本。'
          : access.storageMode === 'device'
            ? '持股只保存在此裝置的帳號空間，不會上傳。'
            : '訪客持股只存在記憶體，重新開啟就會消失。'}
      </p>
    </section>
  );
}
