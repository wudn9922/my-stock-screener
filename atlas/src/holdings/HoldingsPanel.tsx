import { getStorageContextKey } from '../auth/storage';
import { SinopacPanel } from './SinopacPanel';
import { STATEMENT_BROKERS, StatementPanel, applyText, marketLabel, type StatementStatus } from './StatementPanel';
import { holdingsContextApi } from './contextApi';
import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { chartList } from '../app/listContext';
import type { PageProps } from '../pages/pageProps';
import { accessState } from '../auth/access';
import { ImportHoldings } from './ImportHoldings';
import { holdingsStore } from './store';
import { loadHoldingQuotes, loadHoldingsDirectory } from './quotes';
import { useHoldingLiveQuotes } from './useHoldingLiveQuotes';
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
import { holdingChartTarget } from './chartTarget';
import { ALL_ACCOUNTS, holdingsViewPreference, resolveView } from './viewPreference';
import { DividendCashflowCard } from './DividendCashflowCard';
import { HoldingsTotals } from './HoldingsTotals';
import { accountPrincipal, principalByCurrency } from './principal';
import { PrincipalSummary, PrincipalGroupSummary, PrincipalSettings } from './PrincipalSummary';
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
  const viewStore = holdingsViewPreference();
  const viewPreference = useSyncExternalStore(viewStore.subscribe, viewStore.getSnapshot);
  const [sessionView, setSessionView] = useState<string | undefined>(),
    [draft, setDraft] = useState<HoldingAccount | null>(null),
    [creating, setCreating] = useState(false);
  const [method, setMethod] = useState('csv');
  const [statementBroker, setStatementBroker] = useState('cathay');
  const [statementStatus, setStatementStatus] = useState<StatementStatus | null>(null),
    [statementRefresh, setStatementRefresh] = useState(0);
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
  const view = resolveView(
    document.accounts.map((entry) => entry.account.id),
    viewPreference.view,
    sessionView,
  );
  // `selected` is the single account being viewed; undefined in 全部帳號, where account-level tools are hidden.
  const selected = document.accounts.find((entry) => entry.account.id === view);
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
  const context = getStorageContextKey();
  const [closingBook, setClosingBook] = useState<{ context: string; quotes: Record<string, HoldingQuote> }>({ context, quotes: {} });
  const updateClose = useCallback((symbol: string, close: HoldingQuote) => {
    setClosingBook(current => {
      const quotes = current.context === context ? current.quotes : {};
      const previous = quotes[symbol];
      if (previous && (previous.asOf > close.asOf || previous.asOf === close.asOf && previous.price === close.price
        && previous.previous === close.previous && previous.source === close.source)) return current;
      return { context, quotes: { ...quotes, [symbol]: close } };
    });
  }, [context]);
  const confirmedCloses = closingBook.context === context ? closingBook.quotes : {};
  const closeQuote = (holding: Holding): HoldingQuote | undefined => {
    const connection = mexcSnapshot.state.connections.find((entry) => entry.accountId === holding.accountId);
    const fallback = connection ? connection.quotes[holding.symbol] : quotes[holding.symbol];
    const close = confirmedCloses[holding.symbol];
    return holding.market !== 'CRYPTO' && close && (!fallback || close.asOf > fallback.asOf) ? close : fallback;
  };
  const closingRows = document.accounts.flatMap(entry => entry.holdings.map(holding => {
    const quote = closeQuote(holding);
    const result = valueHolding(holding, quote, costs);
    return { holding, quote, result, hidden: hiddenReason(holding, entry.account, result.value, visibilityPreferences) };
  }));
  // Determine subscriptions from the existing close-based visibility rules. Keeping this membership
  // stable during a live tick prevents a dust holding from oscillating between subscribe/unsubscribe.
  const liveSymbols = [...new Set(closingRows.filter(({ holding, hidden }) => !hidden && holding.market !== 'CRYPTO'
    && (!selected || holding.accountId === selected.account.id)).map(row => row.holding.symbol))].sort().join(',');
  const live = useHoldingLiveQuotes(liveSymbols, `${context}|${view}`, updateClose);
  const evaluated = closingRows.map(row => {
    const quote = !row.hidden && live[row.holding.symbol];
    return quote ? { ...row, quote, result: valueHolding(row.holding, quote, costs) } : row;
  });
  // Hidden holdings are a view preference: every total, subtotal and note below uses `all` (visible only).
  const all = evaluated.filter((row) => row.hidden === null);
  const principalGroups = principalByCurrency(document.accounts.map((entry) => entry.account), all, pendingAccounts);
  const hiddenRows = evaluated.filter(
    (row): row is (typeof evaluated)[number] & { hidden: HiddenReason } => row.hidden !== null,
  );
  const byValue = (a: (typeof all)[number], b: (typeof all)[number]) => {
    const av = a.result[sort],
      bv = b.result[sort];
    return av === null
      ? bv === null
        ? a.holding.symbol.localeCompare(b.holding.symbol)
        : 1
      : bv === null
        ? -1
        : new Money(bv).cmp(av);
  };
  const shownAccounts = selected ? [selected] : document.accounts;
  const rowsOf = (accountKey: string) => all.filter((row) => row.holding.accountId === accountKey).sort(byValue);
  const selectedHidden = hiddenRows
    .filter((row) => row.holding.accountId === selected?.account.id)
    .sort((a, b) => a.holding.symbol.localeCompare(b.holding.symbol));
  // Chart prev/next follows what is on screen: the viewed account, or every account in 全部帳號.
  const chartRows = shownAccounts.flatMap((entry) => rowsOf(entry.account.id));
  const chartListName = selected ? `${selected.account.name} 持股` : '全部帳號持股';
  const blocked = snapshot.loading || !!snapshot.error || busy || mexcSnapshot.busy;
  const importAccount = draft ?? selected?.account;
  const [sinopacManaged, setSinopacManaged] = useState<{
    key: string;
    id: string | null;
  }>();
  const sinopacKey = getStorageContextKey() + ':' + selected?.account.id;
  const statementOwner = access.isAdmin && access.storageMode === 'cloud';
  useEffect(() => {
    if (!statementOwner) return setStatementStatus(null);
    let active = true;
    const context = getStorageContextKey();
    void holdingsContextApi<StatementStatus>(context, 'holdings.statement.status')
      .then((value) => active && setStatementStatus(Array.isArray(value?.links) && Array.isArray(value.messages) ? value : null))
      .catch(() => active && setStatementStatus(null));
    return () => {
      active = false;
    };
  }, [statementOwner, statementRefresh, access.userId]);
  // SinoPac sync is the owner's own service: only the server-verified administrator sees its panel
  // (the server refuses everyone else anyway). Others keep editing a legacy CSV SinoPac account.
  const sinopacAdmin = access.isAdmin;
  // An account fed by e-mail statements is edited like any other (corrections, costs).
  const statementLinked = !!selected && !!statementStatus?.links.some((l) => l.accountId === selected.account.id);
  // One account per broker: a broker already linked to an existing account cannot be linked again.
  const linkedAccountOf = (broker: string) => {
    const link = statementStatus?.links.find((l) => l.broker === broker);
    return link && document.accounts.find((entry) => entry.account.id === link.accountId)?.account;
  };
  const statementTaken = linkedAccountOf(statementBroker);
  const sinopacReadOnly =
    !statementLinked &&
    selected?.account.broker === 'sinopac' &&
    (selected.holdings.some((h) => h.source === 'api') ||
      (sinopacAdmin && (sinopacManaged?.key !== sinopacKey || sinopacManaged.id === selected.account.id)));
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
                value={view}
                disabled={editing || creating || connecting || mexcSnapshot.busy}
                onChange={(event) => {
                  setSessionView(event.target.value);
                  setDraft(null);
                }}
              >
                <option value={ALL_ACCOUNTS}>全部帳號</option>
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
      {quotesBusy && shownAccounts.length > 0 && (
        <p role="status" className="holdings-muted">
          讀取報價中，保留上次數值…
        </p>
      )}
      {shownAccounts.map((entry) => {
        const accountRows = rowsOf(entry.account.id);
        const accountHidden = hiddenRows.filter((row) => row.holding.accountId === entry.account.id);
        const principal = accountPrincipal(entry.account, accountRows, pendingAccounts);
        return (
          <section
            className="holdings-account"
            key={entry.account.id}
            aria-label={selected ? '帳戶損益' : `${entry.account.name} 帳戶損益`}
          >
            <h3 className={selected ? 'holdings-sr-only' : 'holdings-account-title'}>
              {entry.account.name} · {entry.account.currency}
            </h3>
            {principal && <PrincipalSummary account={entry.account} metrics={principal} />}
            {entry.account.broker === 'mexc' &&
              mexcSnapshot.state.connections.some((c) => c.accountId === entry.account.id) && (
                <MexcFuturesPanel
                  futures={mexcSnapshot.accountFutures[entry.account.id]}
                  loading={mexcSnapshot.futuresLoading[entry.account.id]}
                  failed={mexcSnapshot.futuresErrors[entry.account.id]}
                  updatedAt={mexcSnapshot.futuresUpdatedAt[entry.account.id]}
                />
              )}
            <div className="holdings-cards">
              {accountRows.map(({ holding, result, quote }) => (
                <HoldingPosition
                  key={holding.id}
                  holding={holding}
                  result={result}
                  quote={quote}
                  name={directory?.find((item) => item.symbol === holding.symbol)?.name}
                  pending={pendingAccounts.has(holding.accountId)}
                  net={net && validDiscount}
                  retained={!quote?.liveState && quote !== confirmedCloses[holding.symbol] && (mexcSnapshot.accountRetainedQuotes[holding.accountId] ?? retainedQuotes).includes(
                    holding.symbol,
                  )}
                  onOpen={() => {
                    const target = holdingChartTarget(holding);
                    if (target?.kind === 'world') navigate({ page: 'world', view: 'commodities' });
                    else if (target)
                      openChart({
                        symbol: target.symbol,
                        list: chartList(
                          chartListName,
                          chartRows
                            .filter((row) => holdingChartTarget(row.holding)?.kind === 'chart')
                            .map((row) => ({ symbol: row.holding.symbol })),
                        ),
                      });
                  }}
                  onHide={() =>
                    visibility.update((current) => hide(current, holdingKey(holding.accountId, holding.symbol)))
                  }
                />
              ))}
            </div>
            {!accountRows.length && (
              <p>
                {pendingAccounts.has(entry.account.id)
                  ? '請先確認 MEXC 餘額與平均成本，再計算持股損益。'
                  : accountHidden.length
                    ? '此帳戶的持股都已隱藏，可切換到該帳戶，在下方「已隱藏」取消隱藏。'
                    : '此帳戶目前沒有持股，可匯入庫存表格。'}
              </p>
            )}
          </section>
        );
      })}
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
          principalGroups={principalGroups}
        >
          <details data-testid="holdings-spot-subtotals">
            <summary>市場小計</summary>
            {(['TW', 'US', 'CRYPTO'] as const)
              .map((market) => ({ market, currency: ({ TW: 'TWD', US: 'USD', CRYPTO: 'USDT' } as const)[market] }))
              .filter(({ market, currency }) => all.some((row) => row.holding.market === market) || principalGroups.has(currency))
              .map(({ market, currency }) => (
                <div key={market}>
                  <h4>{{ TW: '台股', US: '美股', CRYPTO: '加密貨幣' }[market]}</h4>
                  {subtotal(all.filter((row) => row.holding.market === market))}
                  {principalGroups.has(currency) && <PrincipalGroupSummary currency={currency} metrics={principalGroups.get(currency)!} />}
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
      {!snapshot.loading && !snapshot.error && document.accounts.some(entry => entry.holdings.some(h => h.market === 'TW')) && (
        <DividendCashflowCard holdings={document.accounts.flatMap(entry => entry.holdings)} />
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
            const chosen = CSV_MARKETS.find((entry) => entry.key === marketKey)!;
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
            <select aria-label="新增方式" value={method} onChange={(event) => setMethod(event.target.value)}>
              <option value="api">API 自動同步</option>
              <option value="csv">匯入 CSV</option>
              {statementOwner && <option value="email">Email 對帳單</option>}
            </select>
          </label>
          <p className="holdings-muted">永豐美股請用匯入 CSV</p>
          <div className="holdings-fields">
            {method === 'email' ? (
              <label>
                券商
                <select aria-label="對帳單券商" value={statementBroker} onChange={(event) => setStatementBroker(event.target.value)}>
                  {STATEMENT_BROKERS.map((broker) => (
                    <option key={broker.key} value={broker.key} disabled={!broker.market || !!linkedAccountOf(broker.key)}>
                      {broker.label}
                      {broker.market ? `（${marketLabel(broker.market)}）` : ''}
                      {linkedAccountOf(broker.key) ? ' · 已連結' : ''}
                    </option>
                  ))}
                </select>
              </label>
            ) : method === 'api' ? (
              <label>
                API 服務
                <select
                  aria-label="API 服務"
                  value={brokerKey}
                  onChange={(event) => setBrokerKey(event.target.value)}
                >
                  <option value="mexc">MEXC（加密貨幣）</option>
                  {sinopacAdmin && <option value="sinopac">永豐證券（台股）</option>}
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
                required={method === 'csv' || method === 'email'}
                placeholder={
                  method === 'email' ? (statementBroker === 'sinopac' ? '例如：永豐台股' : '例如：國泰美股') : method === 'csv' ? '例如：永豐美股帳戶' : brokerKey === 'sinopac' ? '永豐證券' : 'MEXC'
                }
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
            {sinopacAdmin && method === 'api' && brokerKey === 'sinopac' && (
              <p>由維護者設定永豐服務後，使用下方同步按鈕建立擁有者帳戶。</p>
            )}
            {method === 'email' && (
              <button
                type="button"
                className="primary-button"
                disabled={!name.trim() || blocked || !statementStatus || !!statementTaken}
                onClick={() => {
                  const entry = STATEMENT_BROKERS.find((b) => b.key === statementBroker);
                  const market = entry?.market;
                  if (!entry || !market || busy || linkedAccountOf(entry.key)) return;
                  const account: HoldingAccount = {
                    id: crypto.randomUUID(),
                    name: name.trim(),
                    broker: entry.key,
                    kind: market === 'US' ? 'us-broker' : 'tw-broker',
                    currency: market === 'US' ? 'USD' : 'TWD',
                  };
                  setBusy(true);
                  setError('');
                  void store
                    .replace(account, [])
                    .then(() =>
                      holdingsContextApi<Parameters<typeof applyText>[0]>(getStorageContextKey(), 'holdings.statement.link', {
                        accountId: account.id,
                        broker: entry.key,
                        market,
                      }).catch(async (reason: unknown) => {
                        // Linking failed: remove the empty account again so a retry never leaves duplicates.
                        await store.removeAccount(account.id).catch(() => undefined);
                        throw reason;
                      }),
                    )
                    .then(async (result) => {
                      await store.reload();
                      setSessionView(account.id);
                      setCreating(false);
                      setMessage('已建立並連結 Email 對帳單。' + applyText(result));
                    })
                    .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : '建立未完成，請重新載入持股後重試。'))
                    .finally(() => {
                      setBusy(false);
                      setStatementRefresh((n) => n + 1);
                    });
                }}
              >
                建立並連結
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
            <button type="button" className="ghost-button" onClick={() => setCreating(false)}>
              取消
            </button>
          </div>
          {method === 'email' && statementTaken && (
            <p role="note">這家券商已連結「{statementTaken.name}」，一家券商只能連結一個帳戶；要換帳戶請先刪除原帳戶。</p>
          )}
          {method === 'email' && (
            <p className="holdings-muted">
              帳戶從空持股開始，之後每天讀 Gmail 裡的 PDF 對帳單加減買賣。已有持股請在建立後用「修正持股」補上期初持股。
              {statementStatus === null && ' 對帳單同步服務尚未就緒，請由維護者完成設定。'}
            </p>
          )}
          {method === 'api' && brokerKey === 'mexc' && access.storageMode !== 'cloud' && (
            <p className="holdings-muted">自動讀取 MEXC 需要雲端儲存授權；目前仍可上傳或貼上持股。</p>
          )}
          {method === 'api' &&
            brokerKey === 'mexc' &&
            access.storageMode === 'cloud' &&
            !mexcSnapshot.state.configured && (
              <p role="note">MEXC API 服務尚未啟用，維護者完成設定後即可連線；目前可先匯入持股。</p>
            )}
        </form>
      )}
      {sinopacAdmin && creating && method === 'api' && brokerKey === 'sinopac' && (
        <SinopacPanel onCreated={() => setCreating(false)} />
      )}
      {statementOwner && statementStatus && selected && (
        <StatementPanel
          accountId={selected.account.id}
          status={statementStatus}
          onChanged={(text) => {
            setMessage(text);
            setStatementRefresh((n) => n + 1);
          }}
        />
      )}
      {sinopacAdmin && selected?.account.broker === 'sinopac' && !statementLinked && (
        <SinopacPanel
          key={selected.account.id}
          accountId={selected.account.id}
          onManaged={(id) => setSinopacManaged({ key: sinopacKey, id })}
        />
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
      {selected && (
        <>
          <div className="holdings-toolbar">
            <button
              type="button"
              className="primary-button"
              disabled={sinopacReadOnly || blocked || !directory?.length || editing || creating || connecting}
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
              disabled={sinopacReadOnly || blocked || !directory?.length || editing || creating || connecting}
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
              disabled={blocked || editing || connecting}
              onClick={() => {
                if (!selected || !confirm('刪除此帳戶與全部持股？此操作無法復原。')) return;
                setBusy(true);
                const removed = selected.account.id;
                const unlink = statementLinked
                  ? holdingsContextApi(getStorageContextKey(), 'holdings.statement.unlink', { accountId: removed })
                  : Promise.resolve();
                void unlink
                  .then(() => store.removeAccount(removed))
                  .then(() => {
                    visibility.update((current) => forgetAccount(current, removed));
                    setEditing(false);
                    setDraft(null);
                  })
                  .catch(() => setError('刪除未完成，請重新載入持股後重試。'))
                  .finally(() => {
                    setBusy(false);
                    if (statementLinked) setStatementRefresh((n) => n + 1);
                  });
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
            setSessionView(draft.id);
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
      {editing && importAccount && directory && (draft || !sinopacReadOnly) && (
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
            setSessionView(account.id);
            setDraft(null);
            setEditing(false);
            setError('');
            setMessage('已取代此帳戶持股。');
          }}
        />
      )}
      {document.accounts.length > 0 && !selected && (
        <p className="holdings-muted">目前顯示全部帳號。要匯入、修正、刪除或調整隱藏規則，請先在「持股帳戶」選擇單一帳戶。</p>
      )}
      {selected && (
        <section className="console-card" aria-label="帳戶設定">
          <PrincipalSettings key={selected.account.id + ':' + (selected.account.principal ?? '')} account={selected.account}
            disabled={blocked || editing || creating || connecting || mexcSnapshot.loading}
            onSave={async (principal) => {
              setBusy(true);
              try {
                await store.setPrincipal(selected.account.id, principal);
                if (selected.account.broker === 'mexc' && access.storageMode === 'cloud') await mexc.load();
                setMessage(principal === null ? '已清除帳戶本金。' : '已更新帳戶本金。');
              } finally { setBusy(false); }
            }} />
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
      {document.accounts.length > 0 && (
        <div className="holdings-fields holdings-default-view">
            <label>
          預設開啟
          <select
            aria-label="預設開啟帳戶"
            value={resolveView(
              document.accounts.map((entry) => entry.account.id),
              viewPreference.view,
            )}
            onChange={(event) => viewStore.set(event.target.value)}
          >
            <option value={ALL_ACCOUNTS}>全部帳號</option>
            {document.accounts.map((entry) => (
              <option key={entry.account.id} value={entry.account.id}>
                {entry.account.name} · {entry.account.currency}
              </option>
            ))}
          </select>
        </label>
        </div>
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
