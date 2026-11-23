import { useEffect, useState, type FormEvent } from 'react';
import type { OpenChartOptions } from '../app/Site';
import type { Route } from '../app/routes';
import { getChartStore, initializeChartStore } from '../app/chartStore';
import { chartList } from '../app/listContext';
import { parameterStorageMessage } from '../cloud/settingsApi';
import { parameterTickerKey, stockParameterMap } from '../cloud/stockParameters';
import { createList, deleteList, renameList, setListMembership, useUnifiedLists } from '../lists/listActions';
import { DEFAULT_LIST_MAS, listAvailability, type UnifiedList } from '../lists/unifiedLists';
import { FixedStockParameters } from './FixedStockParameters';
import { ALERT_KIND_LABELS, canNotify, listAlertFlags, useAlertSettings, withAllLists, withListAlert, type AlertKind, type AlertSettings } from '../lists/alertSettings';
import { HelpDisclosure } from './HelpDisclosure';
import { displayTicker } from './format';
import './listPicker.css';

const KIND_LABEL: Record<UnifiedList['kind'], string> = { favorites: '圖表收藏', watchlist: '自選清單', rules: '條件清單', group: '分組' };

/**
 * 我的 › 清單: groups (提醒／日報 with cloud storage), saved watchlists and chart favourites in one place.
 * Reads and writes go through the existing stores and APIs; nothing is migrated or copied.
 */
export function ListManager({ openChart, navigate }: { openChart: (options: OpenChartOptions) => void; navigate: (route: Route) => void }) {
  const chartStore = getChartStore();
  useEffect(() => {
    void initializeChartStore(chartStore);
  }, [chartStore]);
  const data = useUnifiedLists(true, chartStore);
  const alerts = useAlertSettings(data.cloud);
  const notify: NotifyProps | null = data.cloud && alerts.loaded
    ? { settings: alerts.settings, onChange: (list, kind, value) => void alerts.save(withListAlert(alerts.settings, data.lists, list, kind, value)) }
    : null;
  const fixed = stockParameterMap(data.stocks);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [name, setName] = useState('');
  const [type, setType] = useState<'watchlist' | 'group'>('watchlist');
  const [market, setMarket] = useState<'TW' | 'US'>('TW');
  const [editing, setEditing] = useState<string | null>(null);
  const run = async (action: () => Promise<string | void>) => {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await action();
      if (result) setMessage(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '清單操作失敗');
    } finally {
      await data.reload().catch(() => undefined);
      setBusy(false);
    }
  };
  const create = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      const created = await createList({ name, type, market });
      setName('');
      return created.message ?? `已建立「${name.trim()}」。`;
    });
  };
  return (
    <section className="list-manager" aria-label="清單管理">
      <p className="muted list-manager-intro">
        自選清單、分組與圖表收藏都在這裡。在個股圖表按星號即可加入或移出。
        {data.cloud ? '分組會用於每日報告與突破提醒。' : '分組在允許雲端保存後才會用於每日報告與提醒。'}
      </p>
      <form className="list-manager-create console-card" onSubmit={create} aria-label="新增清單">
        <input aria-label="清單名稱" placeholder="清單名稱" value={name} maxLength={type === 'group' ? 30 : 40} required onChange={(event) => setName(event.target.value)} />
        <select aria-label="清單類型" value={type} onChange={(event) => setType(event.target.value as 'watchlist' | 'group')}>
          <option value="watchlist">自選清單（不提醒）</option>
          <option value="group">分組（提醒／日報）</option>
        </select>
        {type === 'watchlist' && (
          <select aria-label="清單市場" value={market} onChange={(event) => setMarket(event.target.value as 'TW' | 'US')}>
            <option value="TW">台股</option>
            <option value="US">美股</option>
          </select>
        )}
        <button type="submit" className="primary-button" disabled={busy || !name.trim()}>建立清單</button>
      </form>
      {data.error && <p role="alert" className="console-message">分組暫時無法載入：{data.error}</p>}
      {error && <p role="alert" className="console-message">{error}</p>}
      {message && <p role="status" className="console-message">{message}</p>}
      <section className="list-notify console-card" aria-label="清單通知">
        <h3>通知</h3>
        {data.cloud ? (
          <>
            <label className="list-notify-all">
              <input type="checkbox" role="switch" checked={alerts.settings.mode === 'all'} disabled={!alerts.loaded}
                onChange={(event) => void alerts.save(withAllLists(alerts.settings, data.lists, event.target.checked))} />
              所有清單都通知
            </label>
            <p className="muted">
              {alerts.settings.mode === 'all'
                ? '分組的均線與所有畫線突破／跌破都會通知（自選清單的均線依各自的監控開關）。關閉後可在每份清單選擇均線、畫線或兩者。'
                : '只通知下方各清單勾選的類型；同一檔股票在多份清單時，任一清單開啟就通知，每次只送一則。Discord 與手機通知都適用。'}
            </p>
            {alerts.error && <p role="alert" className="console-message">{alerts.error}</p>}
          </>
        ) : (
          <p className="muted">伺服器通知需登入並允許雲端保存；目前的清單只在這台裝置使用，不會送出通知。</p>
        )}
      </section>
      {data.loading && <p role="status" className="muted">載入清單…</p>}
      <ul className="list-manager-lists">
        {data.lists.map((list) => (
          <ListCard key={list.key} list={list} busy={busy} fixed={fixed} run={run} openChart={openChart} navigate={navigate} onEditMas={setEditing} notify={notify} />
        ))}
      </ul>
      <HelpDisclosure label="清單與分組說明">
        一檔股票只在一個分組；移出分組或刪除清單都不會刪除個股均線，均線可按「均線」修改（所有清單共用）。從星號加入分組的新股票，均線採用該股票的建議均線，沒有建議時用 {DEFAULT_LIST_MAS.join(' / ')}。條件清單由選股條件決定，在「自選」查看。
      </HelpDisclosure>
      {editing && (
        <FixedStockParameters
          ticker={editing}
          defaults={fixed[parameterTickerKey(editing)] ?? [...DEFAULT_LIST_MAS]}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setMessage(parameterStorageMessage(`${editing} 個股均線`));
            setEditing(null);
            void data.reload();
          }}
        />
      )}
    </section>
  );
}

type NotifyProps = { settings: AlertSettings; onChange: (list: UnifiedList, kind: AlertKind, value: boolean) => void };

function ListCard({ list, busy, fixed, run, openChart, navigate, onEditMas, notify }: {
  notify: NotifyProps | null;
  list: UnifiedList;
  busy: boolean;
  fixed: Record<string, number[]>;
  run: (action: () => Promise<string | void>) => Promise<void>;
  openChart: (options: OpenChartOptions) => void;
  navigate: (route: Route) => void;
  onEditMas: (ticker: string) => void;
}) {
  const [symbol, setSymbol] = useState('');
  const [rename, setRename] = useState(list.name);
  const renamable = list.watchlistId !== undefined || list.customGroupId !== undefined;
  const add = (event: FormEvent) => {
    event.preventDefault();
    const ticker = symbol.trim().toUpperCase();
    const reason = listAvailability(list, ticker);
    void run(async () => {
      if (reason) throw new Error(`${ticker}：${reason}`);
      const result = await setListMembership(list, ticker, true, getChartStore());
      setSymbol('');
      return result ?? `已將 ${displayTicker(ticker)} 加入「${list.name}」。`;
    });
  };
  return (
    <li className="list-card console-card">
      <details>
        <summary>
          <span className="list-card-name">{list.name}</span>
          {list.kind !== 'favorites' && <span className="list-card-kind">{KIND_LABEL[list.kind]}</span>}
          {list.feedsAlerts && <span className="list-badge">提醒／日報</span>}
          <span className="list-card-count"><span className="num">{list.symbols.length}</span> 檔</span>
        </summary>
        <div className="list-card-body">
          {list.editable && (
            <form className="list-card-add" onSubmit={add}>
              <input aria-label={`加入股票到 ${list.name}`} placeholder={list.market === 'TW' ? '2330.TW' : list.market === 'US' ? 'NVDA' : '2330.TW 或 NVDA'}
                value={symbol} maxLength={32} required onChange={(event) => setSymbol(event.target.value)} />
              <button type="submit" className="ghost-button" disabled={busy || !symbol.trim()}>加入</button>
            </form>
          )}
          {list.symbols.length ? (
            <ul className="list-card-members" aria-label={`${list.name} 的股票`}>
              {list.symbols.map((ticker) => {
                const mas = fixed[parameterTickerKey(ticker)];
                return (
                  <li key={ticker}>
                    <button type="button" className="list-card-symbol" onClick={() => openChart({ symbol: ticker, list: chartList(list.name, list.symbols.map((entry) => ({ symbol: entry }))) })}>
                      {displayTicker(ticker)}
                    </button>
                    {list.kind === 'group' && <span className="list-card-mas">{mas ? mas.join(' / ') : '—'}</span>}
                    {list.kind === 'group' && (
                      <button type="button" className="text-button" disabled={busy} aria-label={`修改 ${ticker} 均線`} onClick={() => onEditMas(ticker)}>均線</button>
                    )}
                    {list.editable && (
                      <button type="button" className="text-button" disabled={busy} aria-label={`從 ${list.name} 移除 ${ticker}`}
                        onClick={() => void run(async () => (await setListMembership(list, ticker, false, getChartStore())) ?? `已從「${list.name}」移除 ${displayTicker(ticker)}${list.kind === 'group' ? '，個股均線保留' : ''}。`)}>
                        移除
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="muted">{list.kind === 'rules' ? '尚未開啟過此條件清單，到「自選」查看入選結果。' : '還沒有股票；在個股圖表按星號，或在上方輸入代號加入。'}</p>
          )}
          {notify && canNotify(list) && (() => {
            const flags = listAlertFlags(notify.settings, list);
            const locked = notify.settings.mode === 'all';
            return (
              <fieldset className="list-card-notify" aria-label={`${list.name} 的通知`}>
                <legend>通知{locked ? '（所有清單都通知）' : ''}</legend>
                {(['ma', 'drawing'] as const).map((kind) => (
                  <label key={kind}>
                    <input type="checkbox" checked={flags[kind]} disabled={locked} onChange={(event) => notify.onChange(list, kind, event.target.checked)} />
                    {ALERT_KIND_LABELS[kind]}
                  </label>
                ))}
              </fieldset>
            );
          })()}
          <div className="list-card-actions">
            {list.watchlistId && (
              <button type="button" className="ghost-button" onClick={() => navigate({ page: 'watchlist', list: list.watchlistId })}>在自選查看</button>
            )}
            {renamable && (
              <form className="list-card-rename" onSubmit={(event) => {
                event.preventDefault();
                void run(async () => (await renameList(list, rename)) ?? '名稱已更新。');
              }}>
                <input aria-label={`重新命名 ${list.name}`} value={rename} maxLength={list.kind === 'group' ? 30 : 40} required onChange={(event) => setRename(event.target.value)} />
                <button type="submit" className="ghost-button" disabled={busy || !rename.trim() || rename.trim() === list.name}>改名</button>
              </form>
            )}
            {renamable && (
              <button type="button" className="text-button danger-text" disabled={busy || (list.kind === 'group' && list.symbols.length > 0)}
                title={list.kind === 'group' && list.symbols.length ? '請先移除分組內的股票（個股均線會保留）' : undefined}
                onClick={() => {
                  if (!confirm(`確定刪除「${list.name}」？${list.kind === 'group' ? '' : '已同步的雲端清單也會一起刪除；'}個股均線會保留。`)) return;
                  void run(async () => {
                    await deleteList(list);
                    return `已刪除「${list.name}」。`;
                  });
                }}>
                刪除清單
              </button>
            )}
          </div>
        </div>
      </details>
    </li>
  );
}
