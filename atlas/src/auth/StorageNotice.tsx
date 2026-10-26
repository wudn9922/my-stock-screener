import { useState, useSyncExternalStore } from 'react';
import { isLineInAppBrowser, lineLoginError, loginLineIdentity, logoutLineIdentity } from '../cloud/lineIdentity';
import { accessState, refreshAccess } from './access';
import { HelpDisclosure } from '../ui/HelpDisclosure';
import './storageNotice.css';

export function StorageNotice() {
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = access.storageMode === 'cloud'
    ? '此帳號已獲雲端儲存授權。'
    : access.storageMode === 'device'
      ? '僅保存在此裝置，不會同步個人資料到 Supabase。'
      : '僅本次瀏覽暫存，重新整理即清除，不會保存個人操作。';
  const login = async () => {
    setBusy(true); setError(null);
    try {
      const result = await loginLineIdentity();
      if (result === 'authenticated') { await refreshAccess(); setBusy(false); }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'LINE 登入未完成，請稍後重試。');
      setBusy(false);
    }
  };
  const logout = async () => {
    setBusy(true); setError(null);
    try { logoutLineIdentity(); }
    catch { setError('LINE 登出未能完成，請關閉此頁後再試。'); }
    finally { await refreshAccess(); setBusy(false); }
  };
  return <div className="source-note storage-policy-note">
    <p role="status" data-storage-mode={access.storageMode}>
      {access.userId ? '已登入 · ' : '訪客模式 · '}{label}{(error || access.error || lineLoginError()) && ` ${error || access.error || lineLoginError()}`}
    </p>
    <div className="pwa-login-actions">{access.userId
      ? <button type="button" className="ghost-button" disabled={busy} onClick={() => void logout()}>{isLineInAppBrowser() ? '登出 Atlas' : '登出 LINE'}</button>
      : <>
        <button type="button" className="ghost-button" disabled={busy} onClick={() => void login()}>{busy ? '正在開啟 LINE 登入…' : '使用 LINE 登入'}</button>
        <HelpDisclosure label="登入說明">點選登入後會啟用 LINE 登入所需的瀏覽器儲存；個人操作的保存仍需帳號授權。重新開啟 App 後可再點登入，以同一帳號取回已保存的畫線。</HelpDisclosure>
      </>}
    </div>
  </div>;
}
