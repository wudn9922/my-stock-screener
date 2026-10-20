import { useEffect, useState, type FormEvent } from 'react';
import {
  FIXED_PARAMETER_GROUPS,
  parseMovingAverages,
  settingsApi,
  type IndexParameter,
  type ParameterGroup,
  type ParameterStock,
} from '../cloud/settingsApi';
import { DEFAULT_LIFF_ID } from '../cloud/lineIdentity';

/** The original control panel's authenticated API, rendered inside the Atlas shell. */
export function ParameterConsole() {
  const [groups, setGroups] = useState<ParameterGroup[]>([]);
  const [stocks, setStocks] = useState<ParameterStock[]>([]);
  const [indices, setIndices] = useState<IndexParameter[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [ticker, setTicker] = useState('');
  const [groupId, setGroupId] = useState('tw_g1');
  const [mas, setMas] = useState('20, 50, 200');
  const [editing, setEditing] = useState<number | null>(null);
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
      await settingsApi('stocks.save', {
        ...(editing ? { id: editing } : {}),
        ticker: ticker.trim().toUpperCase(),
        groupId,
        mas: parseMovingAverages(mas),
      });
      await reload();
      setTicker('');
      setEditing(null);
      setMessage('股票與均線參數已儲存；每日報告於下次排程更新。');
    });
  };
  const names: Record<string, string> = {
    ...FIXED_PARAMETER_GROUPS,
    ...Object.fromEntries(groups.map((group) => [`custom_${group.id}`, group.name])),
  };
  return (
    <section className="parameter-console" aria-label="原有均線參數控制台">
      <h2>均線參數與雲端分組</h2>
      <p className="muted">沿用原有股票、分組與大盤參數；修改後由既有每日掃描與突破提醒服務使用。</p>
      {message && (
        <p role="status" className="console-message">
          {message}
        </p>
      )}
      <div className="console-actions">
        <button type="button" className="ghost-button" disabled={busy} onClick={() => void run(reload)}>
          重新載入雲端參數
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
          {editing ? '更新股票參數' : '新增股票參數'}
        </button>
        {editing && (
          <button
            type="button"
            className="ghost-button"
            onClick={() => {
              setEditing(null);
              setTicker('');
            }}
          >
            取消編輯
          </button>
        )}
      </form>
      <div className="research-table-scroll">
        <table className="research-table" aria-label="雲端股票參數">
          <thead>
            <tr>
              <th>股票</th>
              <th>分組</th>
              <th>均線</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {stocks.map((stock) => (
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
                      setTicker(stock.ticker);
                      setGroupId(stock.group_id);
                      setMas([stock.ma1, stock.ma2, stock.ma3, stock.ma4].filter((n) => n > 0).join(', '));
                      setEditing(stock.id);
                    }}
                  >
                    編輯
                  </button>
                  <button
                    type="button"
                    className="text-button"
                    disabled={busy}
                    onClick={() => {
                      if (confirm(`確定刪除 ${stock.ticker} 的這組參數？`))
                        void run(async () => {
                          await settingsApi('stocks.delete', { id: stock.id });
                          await reload();
                        });
                    }}
                  >
                    刪除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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
              aria-label="新增雲端分組名稱"
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
                  await settingsApi('admin.index.save', { ticker: index.ticker, name: index.name, mas: parseMovingAverages(values) });
                  setMessage(`${index.ticker} 大盤參數已儲存`);
                })
              }
            />
          ))}
        </div>
      </details>
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
