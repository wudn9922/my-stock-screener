import { useEffect, useState, useSyncExternalStore, type FormEvent } from 'react';
import { accessState, refreshAccess, type StorageMode } from '../auth/access';
import { settingsApi } from '../cloud/settingsApi';
import { showLegacyImportAgain } from '../storage/legacyImport';

interface StorageGrant {
  userId: string;
  label: string;
  storageMode: StorageMode;
  revision: string;
}
const MODE_LABELS: Record<StorageMode, string> = {
  none: '不保存（僅本次瀏覽）',
  device: '只保存在本人裝置',
  cloud: '允許 Supabase 雲端保存',
};

export function StorageModeNotice() {
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  return (
    <aside className="console-card" aria-label="目前儲存權限">
      <b>{MODE_LABELS[access.storageMode]}</b>
      <p className="muted">
        {access.storageMode === 'none'
          ? '可以正常操作與畫線，但設定只在本頁記憶體；重新載入後清除，不保存到裝置、Supabase 或 GitHub。'
          : access.storageMode === 'device'
            ? '研究設定、清單與畫線只保存在這個帳號的裝置空間，不會上傳到 Supabase 或 GitHub。'
            : '已獲管理員允許，可使用自己的雲端參數、清單與畫線；不會與其他使用者共用。'}
      </p>
      {access.userId && (
        <p className="muted">
          你的 LINE 帳號 ID：
          <code style={{ overflowWrap: 'anywhere' }}>{access.userId}</code>
        </p>
      )}
      {access.error && <p role="status">{access.error}</p>}
      {access.ready && access.userId && (access.storageMode === 'device' || access.storageMode === 'cloud') && (
        <button className="ghost-button" type="button" data-testid="legacy-import-reopen" onClick={() => void showLegacyImportAgain()}>
          重新檢查舊版資料
        </button>
      )}
    </aside>
  );
}

/** This entry is rendered only after the server has verified the owner role. */
export function StorageAdminPanel() {
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  const [rows, setRows] = useState<StorageGrant[]>([]);
  const [userId, setUserId] = useState('');
  const [label, setLabel] = useState('');
  const [storageMode, setStorageMode] = useState<StorageMode>('none');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const load = async () => setRows(await settingsApi<StorageGrant[]>('admin.access.list'));
  useEffect(() => {
    if (!access.isAdmin) return;
    let active = true;
    void settingsApi<StorageGrant[]>('admin.access.list').then(
      (next) => {
        if (active) setRows(next);
      },
      (caught) => {
        if (active) setError(caught instanceof Error ? caught.message : '授權名單載入失敗。');
      },
    );
    return () => {
      active = false;
    };
  }, [access.isAdmin]);
  if (!access.ready || !access.isAdmin) return null;
  const selected = rows.find((row) => row.userId === userId.trim());
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    setMessage('');
    try {
      if (!/^U[0-9a-f]{32}$/.test(userId.trim())) throw new Error('請填入完整 LINE 帳號 ID（U 開頭、32 位十六進位字元）。');
      await settingsApi('admin.access.save', {
        userId: userId.trim(),
        label: label.trim(),
        storageMode,
        ...(selected ? { expectedRevision: selected.revision } : {}),
      });
      await load();
      setMessage(`${label.trim() || userId.trim()} 已設定為「${MODE_LABELS[storageMode]}」。`);
      if (userId.trim() === access.userId) await refreshAccess();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '儲存授權失敗。');
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="console-card" aria-label="管理員儲存權限">
      <h2>管理員：帳號儲存權限</h2>
      <p className="muted">只有你能授權。沒有出現在名單的帳號一律不保存；開啟網頁不會自動新增使用者資料列。</p>
      <p className="muted">「只保存在本人裝置」不會上傳個人研究資料；這份授權名單本身仍由伺服器保存，才能阻止未授權寫入。</p>
      <form className="console-form" onSubmit={(event) => void save(event)}>
        <label className="wide">
          LINE 帳號 ID
          <input
            aria-label="授權 LINE 帳號 ID"
            value={userId}
            onChange={(event) => {
              setUserId(event.target.value);
              setLabel('');
            }}
            maxLength={33}
            required
            placeholder="U…"
            autoComplete="off"
          />
        </label>
        <label>
          辨識名稱
          <input
            aria-label="授權帳號辨識名稱"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            maxLength={60}
            autoComplete="off"
          />
        </label>
        <label>
          保存位置
          <select aria-label="帳號保存位置" value={storageMode} onChange={(event) => setStorageMode(event.target.value as StorageMode)}>
            {Object.entries(MODE_LABELS).map(([mode, text]) => (
              <option key={mode} value={mode}>
                {text}
              </option>
            ))}
          </select>
        </label>
        <button className="primary-button" type="submit" disabled={busy}>
          {busy ? '設定中…' : '設定此帳號權限'}
        </button>
      </form>
      {error && (
        <p role="alert" className="console-message">
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="console-message">
          {message}
        </p>
      )}
      <button
        className="ghost-button"
        type="button"
        disabled={busy}
        onClick={() => {
          setError('');
          void load().catch((caught) => setError(caught instanceof Error ? caught.message : '載入失敗。'));
        }}
      >
        重新載入授權名單
      </button>
      <ul className="saved-member-list" aria-label="已設定儲存權限的帳號">
        {rows.map((row) => (
          <li key={row.userId}>
            <div style={{ minWidth: 0 }}>
              <b>{row.label || (row.userId === access.userId ? '我的帳號' : '已設定帳號')}</b>
              <p className="muted" style={{ overflowWrap: 'anywhere' }}>
                {row.userId}
              </p>
              <span>{MODE_LABELS[row.storageMode]}</span>
            </div>
            <button
              className="ghost-button"
              type="button"
              disabled={busy}
              aria-label={`編輯 ${row.userId} 儲存權限`}
              onClick={() => {
                setUserId(row.userId);
                setLabel(row.label ?? '');
                setStorageMode(row.storageMode);
                setMessage('');
              }}
            >
              編輯權限
            </button>
          </li>
        ))}
      </ul>
      <p className="muted">授權變更不會自動搬移或刪除舊資料；改成不保存或裝置模式後，個人雲端資料的讀寫會被伺服器拒絕。</p>
    </section>
  );
}
