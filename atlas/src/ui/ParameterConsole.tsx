import { HelpDisclosure } from './HelpDisclosure';
import { useEffect, useState, useSyncExternalStore, type FormEvent } from 'react';
import {
  FIXED_PARAMETER_GROUPS,
  FIXED_STOCK_GROUP,
  parameterMAs,
  parseMovingAverages,
  settingsApi,
  parameterStorageMessage,
  type IndexParameter,
  type ParameterGroup,
  type ParameterStock,
} from '../cloud/settingsApi';
import { DEFAULT_LIFF_ID } from '../cloud/lineIdentity';
import { ConsoleDialog } from './ConsoleDialog';
import { accessState } from '../auth/access';
import { stockParameterRows } from '../cloud/stockParameters';
import { saveStockParameters } from '../cloud/saveStockParameters';
import { stockParametersChanged } from '../cloud/useStockParameters';

/** The original control panel's authenticated API, rendered inside the Atlas shell. */
export function ParameterConsole() {
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  const [groups, setGroups] = useState<ParameterGroup[]>([]);
  const [stocks, setStocks] = useState<ParameterStock[]>([]);
  const [indices, setIndices] = useState<IndexParameter[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [ticker, setTicker] = useState('');
  const [groupId, setGroupId] = useState('tw_g1');
  const [mas, setMas] = useState('20, 50, 200');
  const [search, setSearch] = useState('');
  const [filterGroup, setFilterGroup] = useState('all');
  const [dialog, setDialog] = useState<{
    stock: ParameterStock;
    mode: 'edit' | 'delete';
  } | null>(null);
  const [editMas, setEditMas] = useState('');
  const [editGroup, setEditGroup] = useState('');
  const [groupName, setGroupName] = useState('');
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setMessage('');
    try {
      await action();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '設定讀寫失敗');
    } finally {
      setBusy(false);
    }
  };
  const reload = async () => {
    const [nextGroups, nextStocks] = await Promise.all([
      settingsApi<ParameterGroup[]>('groups.list'),
      settingsApi<ParameterStock[]>('stocks.list'),
    ]);
    setGroups(nextGroups);
    setStocks(nextStocks);
  };
  useEffect(() => {
    void run(reload);
  }, []);
  const save = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      if (existingForTicker) {
        setEditGroup(existingForTicker.group_id);
        setEditMas(parameterMAs(existingForTicker).join(', '));
        setDialog({ stock: existingForTicker, mode: 'edit' });
        return;
      }
      await saveStockParameters(ticker.trim().toUpperCase(), parseMovingAverages(mas), existingForTicker, groupId);
      await reload();
      setTicker('');
      setMessage(parameterStorageMessage('股票與均線參數'));
    });
  };
  const names: Record<string, string> = {
    ...FIXED_PARAMETER_GROUPS,
    [FIXED_STOCK_GROUP]: '無分組（固定參數）',
    ...Object.fromEntries(groups.map((group) => [`custom_${group.id}`, group.name])),
  };
  const existingForTicker = stockParameterRows(stocks, ticker)[0];
  const filteredStocks = stocks.filter(
    (stock) =>
      (filterGroup === 'all' || stock.group_id === filterGroup) &&
      `${stock.ticker} ${names[stock.group_id] ?? stock.group_id}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()),
  );
  return (
    <section className="parameter-console" aria-label="原有均線參數控制台">
      <h2>均線參數與個人分組</h2>
      <HelpDisclosure label="參數使用說明">各自設定股票、分組及均線。只有允許雲端保存的帳號，才會交由既有每日掃描與突破提醒服務使用。</HelpDisclosure>
      {message && (
        <p role="status" className="console-message">
          {message}
        </p>
      )}
      <div className="console-actions">
        <button type="button" className="ghost-button" disabled={busy} onClick={() => void run(reload)}>
          重新載入參數
        </button>
        <a href={`https://liff.line.me/${DEFAULT_LIFF_ID}?page=console`} className="text-button">
          在 LINE 開啟 Atlas
        </a>
      </div>
      <form className="console-form" onSubmit={save}>
        <label>
          股票代號
          <input
            aria-label="參數股票代號"
            value={ticker}
            onChange={(event) => setTicker(event.target.value)}
            required
            maxLength={30}
            placeholder="2330.TW 或 NVDA"
          />
        </label>
        <label>
          分組
          <select aria-label="參數股票分組" value={groupId} onChange={(event) => setGroupId(event.target.value)}>
            {Object.entries(names).map(([key, name]) => (
              <option key={key} value={key}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label>
          均線參數
          <input
            aria-label="股票均線參數"
            value={mas}
            onChange={(event) => setMas(event.target.value)}
            required
            placeholder="20, 50, 200"
          />
        </label>
        <button type="submit" className="primary-button" disabled={busy}>
          {existingForTicker ? '編輯已有參數' : '新增股票參數'}
        </button>
      </form>
      {existingForTicker && (
        <p className="muted">
          此股票已有均線 {parameterMAs(existingForTicker).join(' / ')}，請確認原有參數後再編輯，不會另外新增無分組參數。
        </p>
      )}
      <div className="console-actions parameter-filters">
        <label>
          搜尋已有參數股票
          <input
            type="search"
            aria-label="搜尋已有參數股票"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="輸入股票代號或分組名稱"
          />
        </label>
        <label>
          篩選組別
          <select aria-label="已有參數股票組別" value={filterGroup} onChange={(event) => setFilterGroup(event.target.value)}>
            <option value="all">全部組別</option>
            {Object.entries(names).map(([key, name]) => (
              <option key={key} value={key}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <span className="muted">{filteredStocks.length} 筆</span>
      </div>
      <HelpDisclosure label="固定參數與保存說明">
        優先使用原有分組的股票參數；沒有分組才使用無分組固定參數。依目前授權選擇暫存、裝置或雲端保存。只有允許雲端保存且啟用監控的帳號才會使用服務端提醒。
      </HelpDisclosure>
      <div className="research-table-scroll parameter-stock-scroll" role="region" aria-label="已有參數股票清單" tabIndex={0}>
        <table className="research-table" aria-label="個人股票參數">
          <thead>
            <tr>
              <th>股票</th>
              <th>分組</th>
              <th>均線</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {filteredStocks.map((stock) => (
              <tr key={stock.id}>
                <th>{stock.ticker}</th>
                <td>{names[stock.group_id] ?? stock.group_id}</td>
                <td>{[stock.ma1, stock.ma2, stock.ma3, stock.ma4].filter((n) => n > 0).join(' / ')}</td>
                <td>
                  <button
                    type="button"
                    className="text-button"
                    disabled={busy}
                    onClick={() => {
                      const editable = stock.group_id === FIXED_STOCK_GROUP ? stockParameterRows(stocks, stock.ticker)[0]! : stock;
                      setMessage('');
                      setEditGroup(editable.group_id);
                      setEditMas(parameterMAs(editable).join(', '));
                      setDialog({ stock: editable, mode: 'edit' });
                    }}
                  >
                    編輯
                  </button>
                  <button
                    type="button"
                    className="text-button"
                    disabled={busy}
                    onClick={() => {
                      setMessage('');
                      setDialog({ stock, mode: 'delete' });
                    }}
                  >
                    刪除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!filteredStocks.length && <p className="muted">沒有符合搜尋與組別的股票。</p>}
      </div>
      {dialog && (
        <ConsoleDialog
          title={dialog.mode === 'edit' ? `編輯 ${dialog.stock.ticker} 均線參數` : '確定刪除？'}
          busy={busy}
          onClose={() => setDialog(null)}
        >
          {message && (
            <p role="alert" className="console-message">
              {message}
            </p>
          )}
          {dialog.mode === 'edit' ? (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void run(async () => {
                  await saveStockParameters(dialog.stock.ticker, parseMovingAverages(editMas), dialog.stock, editGroup);
                  await reload();
                  setDialog(null);
                  setMessage(parameterStorageMessage('股票均線參數'));
                });
              }}
            >
              <label>
                組別
                <select
                  aria-label="編輯股票組別"
                  value={editGroup}
                  disabled={busy}
                  onChange={(event) => setEditGroup(event.target.value)}
                >
                  {Object.entries(names).map(([key, name]) => (
                    <option key={key} value={key}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                均線參數
                <textarea
                  aria-label="編輯股票均線參數"
                  value={editMas}
                  onChange={(event) => setEditMas(event.target.value)}
                  required
                  disabled={busy}
                />
              </label>
              <p>
                填入 1～4 個不重複的整數（1～1000），用逗號分隔。
                {dialog.stock.group_id !== FIXED_STOCK_GROUP &&
                  stockParameterRows(stocks, dialog.stock.ticker).some(stock => stock.group_id === FIXED_STOCK_GROUP) &&
                  '此股票另有無分組重複參數；確認儲存會保留此分組，並清除無分組重複筆。'}
              </p>
              <div className="console-actions">
                <button type="submit" className="primary-button" disabled={busy}>
                  確認編輯
                </button>
                <button type="button" className="ghost-button" disabled={busy} onClick={() => setDialog(null)}>
                  取消編輯
                </button>
              </div>
            </form>
          ) : (
            <>
              <p>
                確定刪除 {dialog.stock.ticker} 在「
                {names[dialog.stock.group_id] ?? dialog.stock.group_id}
                」的參數？
                {dialog.stock.group_id === FIXED_STOCK_GROUP
                  ? '刪除固定參數後，仍在分組或清單內的股票會改用該分組或清單的均線。'
                  : '其他組別與已保存的固定參數會保留。'}
              </p>
              <div className="console-actions">
                <button
                  type="button"
                  className="primary-button"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await settingsApi('stocks.delete', {
                        id: dialog.stock.id,
                      });
                      stockParametersChanged();
                      await reload();
                      setDialog(null);
                      setMessage('這筆股票參數已刪除。');
                    })
                  }
                >
                  確認刪除
                </button>
                <button type="button" className="ghost-button" disabled={busy} onClick={() => setDialog(null)}>
                  取消刪除
                </button>
              </div>
            </>
          )}
        </ConsoleDialog>
      )}
      <details className="research-disclosure">
        <summary>管理自訂分組</summary>
        <div className="research-disclosure-body">
          <form
            className="console-actions"
            onSubmit={(event) => {
              event.preventDefault();
              void run(async () => {
                await settingsApi('groups.create', { name: groupName });
                setGroupName('');
                await reload();
              });
            }}
          >
            <input
              aria-label="新增個人分組名稱"
              value={groupName}
              onChange={(event) => setGroupName(event.target.value)}
              maxLength={30}
              required
              placeholder="分組名稱"
            />
            <button type="submit" className="ghost-button" disabled={busy}>
              新增分組
            </button>
          </form>
          {groups.map((group) => (
            <GroupEditor
              key={group.id}
              group={group}
              busy={busy}
              onRename={(name) =>
                void run(async () => {
                  await settingsApi('groups.rename', { id: group.id, name });
                  await reload();
                })
              }
              onDelete={() => {
                if (confirm(`確定刪除「${group.name}」？分組內必須先移除所有股票。`))
                  void run(async () => {
                    await settingsApi('groups.delete', { id: group.id });
                    await reload();
                  });
              }}
            />
          ))}
        </div>
      </details>
      {access.isAdmin && access.storageMode === 'cloud' && (
        <details className="research-disclosure">
          <summary>大盤指數均線參數（管理員）</summary>
          <div className="research-disclosure-body">
            <button
              type="button"
              className="ghost-button"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  setIndices(await settingsApi<IndexParameter[]>('admin.index.list'));
                })
              }
            >
              載入大盤參數
            </button>
            {indices?.map((index) => (
              <IndexEditor
                key={index.ticker}
                index={index}
                busy={busy}
                onSave={(values) =>
                  void run(async () => {
                    await settingsApi('admin.index.save', {
                      ticker: index.ticker,
                      name: index.name,
                      mas: parseMovingAverages(values),
                    });
                    setMessage(`${index.ticker} 大盤參數已儲存`);
                  })
                }
              />
            ))}
          </div>
        </details>
      )}
    </section>
  );
}

function GroupEditor({
  group,
  busy,
  onRename,
  onDelete,
}: {
  group: ParameterGroup;
  busy: boolean;
  onRename: (name: string) => void;
  onDelete: () => void;
}) {
  const [name, setName] = useState(group.name);
  return (
    <form
      className="console-actions"
      onSubmit={(event) => {
        event.preventDefault();
        onRename(name);
      }}
    >
      <input
        aria-label={`分組 ${group.name} 名稱`}
        value={name}
        onChange={(event) => setName(event.target.value)}
        required
        maxLength={30}
      />
      <button type="submit" className="ghost-button" disabled={busy}>
        重新命名
      </button>
      <button type="button" className="text-button" disabled={busy} onClick={onDelete}>
        刪除分組
      </button>
    </form>
  );
}
function IndexEditor({ index, busy, onSave }: { index: IndexParameter; busy: boolean; onSave: (mas: string) => void }) {
  const [mas, setMas] = useState([index.ma1, index.ma2, index.ma3, index.ma4].filter((n) => n > 0).join(', '));
  return (
    <form
      className="console-actions"
      onSubmit={(event) => {
        event.preventDefault();
        onSave(mas);
      }}
    >
      <label>
        {index.name || index.ticker}
        <input aria-label={`${index.ticker} 均線參數`} value={mas} onChange={(event) => setMas(event.target.value)} required />
      </label>
      <button type="submit" className="ghost-button" disabled={busy}>
        儲存大盤參數
      </button>
    </form>
  );
}
