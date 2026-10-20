import { useEffect, useRef, useState, useSyncExternalStore, type FormEvent } from 'react';
import type { PageProps } from './pageProps';
import { ParameterConsole } from '../ui/ParameterConsole';
import { watchlistStore } from '../watchlists/store';
import { createWatchlist, evaluateWatchlist, visitWatchlist, watchlistSchema, type WatchlistVisit } from '../watchlists/model';
import { refreshCloudWatchlist } from '../watchlists/cloud';
import { watchlistCloudSync } from '../watchlists/sync';
import { startLineIdentity } from '../cloud/lineIdentity';
import { activeChips, needsGrowth, parseFilters } from './screenerFilters';
import { loadUniverse, UNIVERSE_GROUP_KEYS, universeMarketOf } from '../report/universe';
import { loadMarketGrowth } from '../fundamentals/GrowthProvider';
import { FIXED_STOCK_GROUP, parameterMAs, parseMovingAverages, settingsApi, type ParameterStock } from '../cloud/settingsApi';
import { FixedStockParameters } from '../ui/FixedStockParameters';
import { formatDateTime, displayTicker } from '../ui/format';
import type { GroupItem } from '../report/schema';
import '../ui/research.css';

type CloudList = { id: string; definition: unknown; updated_at: string };

export default function ConsolePage({ report, navigate, openChart, list: requested }: PageProps & { list?: string }) {
  const store = watchlistStore();
  const sync = watchlistCloudSync(store);
  const { document, error: storageError } = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const selected = document.lists.find((entry) => entry.id === requested) ?? document.lists[0];
  const [tab, setTab] = useState<'lists' | 'parameters'>('lists');
  const [visit, setVisit] = useState<WatchlistVisit | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [visibleCount, setVisibleCount] = useState(100);
  const [attempt, setAttempt] = useState(0);
  const [cloudBusy, setCloudBusy] = useState(false);
  const [cloudMessage, setCloudMessage] = useState('');
  const [cloudLists, setCloudLists] = useState<CloudList[]>([]);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [manual, setManual] = useState('');
  const [market, setMarket] = useState<'TW' | 'US'>('TW');
  const [renaming, setRenaming] = useState('');
  const imported = useRef<HTMLInputElement>(null);
  const [editingStock, setEditingStock] = useState<string | null>(null);
  const [fixedParameters, setFixedParameters] = useState<Record<string, number[]>>({});
  const [syncCount, setSyncCount] = useState(0);
  const seenSettings = useRef(new Map<string, string>());
  const settingsKey = selected
    ? JSON.stringify([
        selected.id,
        selected.updatedAt,
        selected.name,
        selected.group,
        selected.filters,
        selected.symbols,
        selected.maList,
        selected.monitorBreakouts,
      ])
    : '';
  useEffect(() => {
    if (!selected) return;
    const previous = seenSettings.current.get(selected.id);
    seenSettings.current.set(selected.id, settingsKey);
    // Opening a linked list must read the daily collector before writing anything.
    if (previous === settingsKey || (previous === undefined && selected.cloud)) return;
    let active = true;
    setSyncCount((count) => count + 1);
    void sync
      .sync(selected.id)
      .then((saved) => {
        if (active) setCloudMessage(saved ? '清單設定已自動同步到 Supabase。' : '清單已保存在本機；從 LINE 登入後，儲存設定即可自動同步。');
      })
      .catch((caught) => {
        if (active)
          setCloudMessage(
            `設定已保存在本機，自動同步未完成：${caught instanceof Error ? caught.message : '雲端讀寫失敗'}。可按「重試同步」再次儲存。`,
          );
      })
      .finally(() => {
        setSyncCount((count) => count - 1);
      });
    return () => {
      active = false;
    };
  }, [settingsKey, sync]);
  useEffect(() => {
    let active = true;
    void startLineIdentity()
      .then(async (identity) => {
        if (!identity) return;
        const stocks = await settingsApi<ParameterStock[]>('stocks.list');
        if (active)
          setFixedParameters(
            Object.fromEntries(
              stocks.filter((stock) => stock.group_id === FIXED_STOCK_GROUP).map((stock) => [stock.ticker, parameterMAs(stock)]),
            ),
          );
      })
      .catch(() => {
        /* Individual editing reports authenticated load failures in its dialog. */
      });
    return () => {
      active = false;
    };
  }, [tab]);
  useEffect(() => {
    setRenaming(selected?.name ?? '');
  }, [selected?.id, selected?.name]);
  const reportKey = report.status === 'ok' ? (report.report.generatedAt ?? report.report.reportDate) : report.status;
  useEffect(() => {
    if (!selected || tab !== 'lists') return;
    let active = true;
    setLoading(true);
    setError(null);
    setVisit(null);
    setVisibleCount(100);
    void (async () => {
      let base = selected;
      try {
        base = await refreshCloudWatchlist(selected);
      } catch (caught) {
        if (active) setCloudMessage(`雲端暫時無法更新，改用本機清單：${caught instanceof Error ? caught.message : '同步失敗'}`);
      }
      let items: GroupItem[], dataDate: string | null;
      if (universeMarketOf(selected.group) || selected.kind === 'manual') {
        const result = await loadUniverse(attempt > 0);
        if (result.status !== 'ok') throw new Error(result.message);
        const source = result.universe.markets[selected.market];
        if (!source) throw new Error('此市場的全市場資料尚未產生。');
        items = source.items;
        dataDate = source.asOf;
      } else {
        if (report.status !== 'ok') throw new Error(report.status === 'loading' ? '每日報告載入中，完成後會重新計算。' : report.message);
        const group = report.report.groups.find((entry) => entry.key === selected.group);
        if (!group) throw new Error('原選股群組不在本次報告中；沒有改動上次名單。');
        items = group.items;
        dataDate = group.items.find((entry) => entry.asOf)?.asOf ?? report.report.reportDate;
      }
      if (!dataDate) throw new Error('來源缺少資料日期；沒有改動上次名單。');
      const growth = needsGrowth(parseFilters(selected.filters)) ? await loadMarketGrowth(selected.market, attempt > 0) : undefined;
      const members = evaluateWatchlist(selected, items, growth);
      if (!active) return;
      const latest = store.getSnapshot().document.lists.find((entry) => entry.id === selected.id);
      if (!latest || latest.filters !== selected.filters || latest.group !== selected.group) return;
      const baseline = (latest.lastOpenedAt ?? 0) > (base.lastOpenedAt ?? 0) ? latest : base;
      const history = [...new Map([...base.history, ...latest.history].map((row) => [row.date, row])).values()]
        .sort((a, b) => a.date.localeCompare(b.date))
        .slice(-90);
      const result = visitWatchlist(
        {
          ...latest,
          history,
          lastViewed: baseline.lastViewed,
          lastOpenedAt: baseline.lastOpenedAt,
          cloud: latest.cloud?.version === selected.cloud?.version ? base.cloud : latest.cloud,
        },
        members,
        dataDate.slice(0, 10),
      );
      store.save(result.list);
      setVisit(result);
      try {
        // Also queue an observation behind a not-yet-finished first cloud create.
        await sync.sync(result.list.id);
      } catch (caught) {
        if (active) setCloudMessage(`自動同步未完成：${caught instanceof Error ? caught.message : '同步失敗'}。本機清單仍保留，已完成的比較結果未變。`);
      }
    })()
      .catch((caught) => {
        if (active) setError(caught instanceof Error ? caught.message : '清單讀取失敗');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
    // An opening is selected-id / explicit refresh / new report, never a storage-notification loop.
  }, [selected?.id, reportKey, attempt, tab]);

  const action = (fn: () => void) => {
    try {
      fn();
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '清單操作失敗');
    }
  };
  const cloud = async (fn: () => Promise<void>) => {
    setCloudBusy(true);
    setCloudMessage('');
    try {
      await fn();
    } catch (caught) {
      setCloudMessage(`${caught instanceof Error ? caught.message : '雲端同步失敗'} 本機清單仍保留。`);
    } finally {
      setCloudBusy(false);
    }
  };
  const saveCloud = () =>
    void cloud(async () => {
      if (!selected) return;
      const saved = await sync.sync(selected.id);
      setCloudMessage(
        saved
          ? '此清單與突破監控選項已同步到 Supabase；服務端監控需啟用新版突破提醒部署。'
          : '請從 LINE 開啟 Atlas 並登入後同步。本機清單仍保留。',
      );
    });
  const importCloud = (row: CloudList) =>
    void cloud(async () => {
      const identity = await startLineIdentity();
      if (!identity) throw new Error('請從 LINE 開啟 Atlas 並登入。');
      const definition = watchlistSchema.parse(row.definition);
      store.save({
        ...definition,
        cloud: { userId: identity.userId, version: row.updated_at },
      });
      navigate({ page: 'console', list: row.id });
      setAttempt((value) => value + 1);
      setCloudMessage('雲端清單已匯入；之後開啟會先讀取每日紀錄，再比較上次開啟時的名單。');
    });
  const download = () => {
    const url = URL.createObjectURL(new Blob([store.exportRaw()], { type: 'application/json' }));
    const anchor = globalThis.document.createElement('a');
    anchor.href = url;
    anchor.download = 'atlas-watchlists.json';
    anchor.click();
    URL.revokeObjectURL(url);
  };
  const create = (event: FormEvent) => {
    event.preventDefault();
    action(() => {
      const symbols = [
        ...new Set(
          manual
            .split(/[\s,，]+/)
            .filter(Boolean)
            .map((symbol) => symbol.toUpperCase()),
        ),
      ];
      if (!symbols.length) throw new Error('請至少輸入一個完整代號，例如 2330.TW 或 NVDA。');
      if (symbols.some((symbol) => (market === 'TW' ? !/\.(TW|TWO)$/.test(symbol) : /\.(TW|TWO)$/.test(symbol))))
        throw new Error('請使用所選市場的完整代號；台股包含 .TW 或 .TWO。');
      const next = createWatchlist({
        name,
        market,
        group: UNIVERSE_GROUP_KEYS[market],
        kind: 'manual',
        filters: '',
        symbols,
      });
      store.save(next);
      navigate({ page: 'console', list: next.id });
      setCreating(false);
      setName('');
      setManual('');
    });
  };
  const activeVisit = visit?.list.id === selected?.id ? visit : null;
  const sourceName = selected
    ? universeMarketOf(selected.group)
      ? `${selected.market === 'TW' ? '台股' : '美股'}全市場`
      : report.status === 'ok'
        ? (report.report.groups.find((group) => group.key === selected.group)?.name ?? '已儲存的選股群組')
        : '已儲存的選股群組'
    : '';

  return (
    <div className="page console-page">
      <div className="page-head">
        <div>
          <h2 className="page-title">參數控制台與自選清單</h2>
          <p className="page-sub">保存選股條件、追蹤名單變化，沿用原有均線與突破提醒。</p>
        </div>
      </div>
      <div className="segmented console-tabs" role="group" aria-label="控制台分頁">
        <button type="button" aria-pressed={tab === 'lists'} className={tab === 'lists' ? 'active' : ''} onClick={() => setTab('lists')}>
          自選清單
        </button>
        <button
          type="button"
          aria-pressed={tab === 'parameters'}
          className={tab === 'parameters' ? 'active' : ''}
          onClick={() => setTab('parameters')}
        >
          均線參數
        </button>
      </div>
      {tab === 'parameters' ? (
        <ParameterConsole />
      ) : (
        <>
          {(error || storageError) && (
            <p role="alert" className="console-message">
              {error || storageError}
            </p>
          )}
          <div className="console-actions">
            <button type="button" className="primary-button" onClick={() => navigate({ page: 'screener', group: 'universe-tw' })}>
              以篩選條件建立清單
            </button>
            <button type="button" className="ghost-button" onClick={() => setCreating((value) => !value)}>
              建立手動清單
            </button>
            <button type="button" className="ghost-button" onClick={download}>
              匯出清單備份
            </button>
            <button type="button" className="ghost-button" onClick={() => imported.current?.click()}>
              匯入備份
            </button>
            <input
              type="file"
              ref={imported}
              accept="application/json,.json"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file)
                  void file.text().then((raw) =>
                    action(() => {
                      if (confirm('匯入會取代目前的條件清單，建議先匯出備份。是否繼續？')) {
                        store.importJson(raw);
                        setAttempt((value) => value + 1);
                      }
                    }),
                  );
                event.target.value = '';
              }}
            />
          </div>
          {creating && (
            <form className="console-form console-card" onSubmit={create}>
              <label>
                清單名稱
                <input aria-label="手動清單名稱" value={name} onChange={(event) => setName(event.target.value)} maxLength={40} required />
              </label>
              <label>
                市場
                <select aria-label="手動清單市場" value={market} onChange={(event) => setMarket(event.target.value as 'TW' | 'US')}>
                  <option value="TW">台股</option>
                  <option value="US">美股</option>
                </select>
              </label>
              <label className="wide">
                股票代號
                <textarea
                  aria-label="手動清單股票"
                  value={manual}
                  onChange={(event) => setManual(event.target.value)}
                  required
                  placeholder="2330.TW, 2317.TW"
                />
              </label>
              <button type="submit" className="primary-button">
                儲存手動清單
              </button>
            </form>
          )}
          <div className="watchlist-tabs" role="group" aria-label="我的自選清單">
            {document.lists.map((entry) => (
              <button
                type="button"
                key={entry.id}
                className={`chip ${selected?.id === entry.id ? 'active' : ''}`}
                aria-pressed={selected?.id === entry.id}
                onClick={() => {
                  if (selected?.id === entry.id) setAttempt((value) => value + 1);
                  else navigate({ page: 'console', list: entry.id });
                }}
              >
                {entry.name}
                <small>{entry.market === 'TW' ? '台股' : '美股'}</small>
              </button>
            ))}
          </div>
          {!selected ? (
            <div className="page-state empty">
              <b>還沒有條件清單</b>
              <p>到選股頁套用技術或財務條件後，按「存成自選清單」；可以保存多組不同策略。</p>
            </div>
          ) : (
            <section className="console-card" aria-label="清單追蹤結果">
              <div className="console-actions">
                <h2>{selected.name}</h2>
                <button type="button" className="ghost-button" disabled={loading} onClick={() => setAttempt((value) => value + 1)}>
                  重新開啟並比較
                </button>
                <button
                  type="button"
                  className="text-button"
                  disabled={cloudBusy}
                  onClick={() => {
                    if (confirm(`確定刪除清單「${selected.name}」？已連結的 Supabase 清單也會一起刪除；個股固定參數會保留。`))
                      void cloud(async () => {
                        await sync.remove(selected.id);
                        navigate({ page: 'console' });
                        setCloudMessage('清單已刪除；個股固定參數仍保留。');
                      });
                  }}
                >
                  刪除清單
                </button>
              </div>
              <p className="muted">
                {selected.kind === 'manual'
                  ? `手動固定 ${selected.symbols.length} 檔`
                  : `來源：${sourceName} · ${
                      activeChips(parseFilters(selected.filters))
                        .map((chip) => chip.label)
                        .join('、') || '不限制條件'
                    }`}
              </p>
              <div className="console-actions">
                <label className="console-check">
                  <input
                    type="checkbox"
                    role="switch"
                    aria-label="監控此清單的突破提醒"
                    checked={selected.monitorBreakouts}
                    onChange={(event) =>
                      action(() => {
                        store.save({
                          ...selected,
                          monitorBreakouts: event.target.checked,
                          updatedAt: Date.now(),
                        });
                      })
                    }
                  />
                  監控此清單的突破提醒
                </label>
                <label>
                  監控均線
                  <input
                    aria-label="清單突破監控均線"
                    defaultValue={selected.maList.join(', ')}
                    key={`${selected.id}-${selected.maList.join(',')}`}
                    onBlur={(event) =>
                      action(() => {
                        const values = parseMovingAverages(event.target.value);
                        store.save(
                          watchlistSchema.parse({
                            ...selected,
                            maList: values,
                            updatedAt: Date.now(),
                          }),
                        );
                      })
                    }
                  />
                </label>
              </div>
              {loading && <p role="status">正在以固定條件重新計算名單…</p>}
              {!loading && error && !activeVisit && !!selected.history.length && (
                <div>
                  <h3>上次成功名單（{selected.history.at(-1)?.date}）</h3>
                  <p className="muted">本次沒有取得完整資料，未判定新入選或出榜。</p>
                  <ul className="saved-member-list">
                    {selected.history
                      .at(-1)
                      ?.members.slice(0, 100)
                      .map((entry) => (
                        <li key={entry.symbol}>
                          <button
                            type="button"
                            onClick={() =>
                              openChart({
                                symbol: entry.symbol,
                                name: entry.name,
                              })
                            }
                          >
                            {displayTicker(entry.symbol)} {entry.name}
                          </button>
                        </li>
                      ))}
                  </ul>
                </div>
              )}
              {activeVisit && (
                <>
                  <p className="muted">
                    資料日 {activeVisit.list.history.at(-1)?.date} ·{' '}
                    {activeVisit.firstVisit
                      ? '首次開啟，已建立比較基準。'
                      : `相較上次開啟 ${formatDateTime(new Date(activeVisit.previousOpenedAt!).toISOString())}（資料日 ${activeVisit.previousDate}）`}
                  </p>
                  <div className="watchlist-counts" aria-live="polite">
                    <div>
                      <b>{activeVisit.entered.length}</b>
                      <span>今天新入選</span>
                    </div>
                    <div>
                      <b>{activeVisit.exited.length}</b>
                      <span>今天出榜</span>
                    </div>
                    <div>
                      <b>{activeVisit.members.length}</b>
                      <span>目前入選</span>
                    </div>
                  </div>
                  <div className="watchlist-changes">
                    <div>
                      <h3>新入選</h3>
                      <p>
                        {activeVisit.entered.map((entry) => `${displayTicker(entry.symbol)} ${entry.name}`).join('、') ||
                          (activeVisit.firstVisit ? '首次建立基準' : '無')}
                      </p>
                    </div>
                    <div>
                      <h3>出榜</h3>
                      <p>{activeVisit.exited.map((entry) => `${displayTicker(entry.symbol)} ${entry.name}`).join('、') || '無'}</p>
                    </div>
                  </div>
                  <ul className="saved-member-list">
                    {activeVisit.members.slice(0, visibleCount).map((entry) => (
                      <li key={entry.symbol}>
                        <button
                          type="button"
                          className="member-chart-button"
                          onClick={() =>
                            openChart({
                              symbol: entry.symbol,
                              name: entry.name,
                              tf: '1D',
                              maList: fixedParameters[entry.symbol] ?? selected.maList,
                            })
                          }
                        >
                          <span>
                            <b>{displayTicker(entry.symbol)}</b> {entry.name}
                          </span>
                          <small>連續入選 {entry.streak} 個已確認交易日</small>
                        </button>
                        <button
                          type="button"
                          className="text-button member-parameter-button"
                          onClick={() => setEditingStock(entry.symbol)}
                          aria-label={`設定 ${entry.symbol} 固定均線參數`}
                        >
                          {fixedParameters[entry.symbol] ? `固定均線 ${fixedParameters[entry.symbol].join(' / ')}` : '設定個股均線'}
                        </button>
                      </li>
                    ))}
                  </ul>
                  {activeVisit.members.length > visibleCount && (
                    <button type="button" className="ghost-button" onClick={() => setVisibleCount((value) => value + 100)}>
                      再顯示 100 檔（尚有 {activeVisit.members.length - visibleCount} 檔）
                    </button>
                  )}
                  {!activeVisit.members.length && <p>目前沒有符合這組條件的股票。</p>}
                </>
              )}
              <p className="muted">
                比較基準是上次開啟此清單的結果。同一資料日重開不增加天數；已同步且啟用每日收集時，會累計每日實際入選紀錄。本機模式只計算已觀察的連續資料日；沒有紀錄的期間不推算，遇資料缺漏保留原基準。
              </p>
              <details className="research-disclosure">
                <summary>清單設定與 Supabase 同步</summary>
                <div className="research-disclosure-body">
                  <form
                    className="console-actions"
                    onSubmit={(event) => {
                      event.preventDefault();
                      action(() =>
                        store.save({
                          ...selected,
                          name: renaming,
                          updatedAt: Date.now(),
                        }),
                      );
                    }}
                  >
                    <input
                      aria-label="重新命名清單"
                      value={renaming}
                      onChange={(event) => setRenaming(event.target.value)}
                      required
                      maxLength={40}
                    />
                    <button type="submit" className="ghost-button">
                      儲存名稱
                    </button>
                  </form>
                  <button type="button" className="primary-button" disabled={cloudBusy} onClick={saveCloud}>
                    重試同步
                  </button>
                  <p className="muted">
                    登入 LINE 後，建立清單、儲存名稱與修改監控設定會自動同步；刪除時一併刪除雲端清單。雲端清單需先部署資料表與新版
                    user-settings 函式；失敗時保留本機並顯示重試提示。
                  </p>
                </div>
              </details>
            </section>
          )}
          <details className="research-disclosure">
            <summary>載入與管理雲端清單</summary>
            <div className="research-disclosure-body">
              <button
                type="button"
                className="ghost-button"
                disabled={cloudBusy}
                onClick={() =>
                  void cloud(async () => {
                    const rows = await settingsApi<CloudList[]>('watchlists.list');
                    setCloudLists(rows);
                    setCloudMessage(`已載入 ${rows.length} 組雲端清單；選擇匯入後才會更新本機。`);
                  })
                }
              >
                載入雲端清單
              </button>
              {cloudLists.map((row) => {
                const parsed = watchlistSchema.safeParse(row.definition);
                return (
                  <div className="console-actions" key={row.id}>
                    <span>{parsed.success ? parsed.data.name : '格式無法辨識的清單'}</span>
                    <button
                      type="button"
                      className="ghost-button"
                      disabled={!parsed.success || cloudBusy}
                      onClick={() => {
                        if (parsed.success && confirm('匯入會更新本機同 ID 的清單，是否繼續？'))
                          action(() => {
                            importCloud(row);
                          });
                      }}
                    >
                      匯入本機
                    </button>
                    <button
                      type="button"
                      className="text-button"
                      disabled={cloudBusy}
                      onClick={() => {
                        if (confirm('確定刪除這組清單並停止其突破監控？同 ID 的本機清單也會刪除，個股固定參數會保留。'))
                          void cloud(async () => {
                            const local = store.getSnapshot().document.lists.find((entry) => entry.id === row.id);
                            if (local?.cloud) await sync.remove(row.id);
                            else {
                              await settingsApi('watchlists.delete', {
                                id: row.id,
                                expectedUpdatedAt: row.updated_at,
                              });
                              if (local) store.remove(row.id);
                            }
                            setCloudLists((previous) => previous.filter((entry) => entry.id !== row.id));
                          });
                      }}
                    >
                      刪除雲端清單
                    </button>
                  </div>
                );
              })}
            </div>
          </details>
          {syncCount > 0 && <p role="status">正在自動同步清單設定…</p>}
          {editingStock && selected && (
            <FixedStockParameters
              ticker={editingStock}
              defaults={fixedParameters[editingStock] ?? selected.maList}
              onClose={() => setEditingStock(null)}
              onSaved={(values) => {
                setFixedParameters((previous) => ({
                  ...previous,
                  [editingStock]: values,
                }));
                setEditingStock(null);
                setCloudMessage(`${editingStock} 固定均線已永久保存到 Supabase，出榜後仍保留。`);
              }}
            />
          )}
          {cloudMessage && (
            <p role="status" className="console-message">
              {cloudMessage}
            </p>
          )}
        </>
      )}
    </div>
  );
}
