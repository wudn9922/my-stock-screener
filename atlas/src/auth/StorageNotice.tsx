import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { acceptLineHandoff, isLineInAppBrowser, lineLoginError, loginLineIdentity, logoutLineIdentity } from '../cloud/lineIdentity';
import { beginSeparateLineLogin, cancelIncomingLineHandoff, completeIncomingLineHandoff, incomingLineApproval, lineHandoffMessage, linePairingCode, needsSeparateLineLogin, type LoginCandidate } from '../cloud/lineLoginHandoff';
import { accessState, refreshAccess } from './access';
import { HelpDisclosure } from '../ui/HelpDisclosure';
import './storageNotice.css';

export function StorageNotice() {
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [candidate, setCandidate] = useState<LoginCandidate | null>(null);
  const [loginUrl, setLoginUrl] = useState<string | null>(null);
  const [senderState, setSenderState] = useState({ busy: false });
  const approval = incomingLineApproval();
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  const label = access.storageMode === 'cloud'
    ? '此帳號已獲雲端儲存授權。'
    : access.storageMode === 'device'
      ? '僅保存在此裝置，不會同步個人資料到 Supabase。'
      : '僅本次瀏覽暫存，重新整理即清除，不會保存個人操作。';
  const login = async () => {
    if (pending.current) return;
    setCandidate(null);
    setBusy(true); setError(null);
    try {
      if (needsSeparateLineLogin()) {
        const controller = new AbortController();
        pending.current = controller;
        try { setCandidate(await beginSeparateLineLogin(controller.signal, setLoginUrl)); }
        finally { pending.current = null; setBusy(false); setLoginUrl(null); }
        return;
      }
      const result = await loginLineIdentity();
      if (result === 'authenticated') { await refreshAccess(); setBusy(false); }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'LINE 登入未完成，請稍後重試。');
      setBusy(false);
    }
  };
  const confirm = async () => {
    if (!candidate) return;
    setBusy(true); setError(null);
    try { acceptLineHandoff(candidate); setCandidate(null); await refreshAccess(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'LINE 登入未完成。'); }
    finally { setBusy(false); }
  };
  const logout = async () => {
    setBusy(true); setError(null);
    try { logoutLineIdentity(); }
    catch { setError('LINE 登出未能完成，請關閉此頁後再試。'); }
    finally { await refreshAccess(); setBusy(false); }
  };
  const approveSender = async () => {
    setSenderState({ busy: true });
    try { await completeIncomingLineHandoff(); }
    finally { setSenderState({ busy: false }); }
  };
  return <div className="source-note storage-policy-note">
    <p role="status" data-storage-mode={access.storageMode}>
      {access.userId ? '已登入 · ' : '訪客模式 · '}{label}{(error || access.error || lineLoginError()) && ` ${error || access.error || lineLoginError()}`}
    </p>
    {lineHandoffMessage() && <p role="status">{lineHandoffMessage()}</p>}
    {approval && <div className="line-login-candidate" role="group" aria-label="確認 LINE 登入交接">
      <span>配對碼 {approval.code}</span>
      <button type="button" className="ghost-button" disabled={senderState.busy} onClick={() => void approveSender()}>確認登入原來的 Atlas</button>
      <button type="button" className="ghost-button" disabled={senderState.busy} onClick={() => { cancelIncomingLineHandoff(); setSenderState({ busy: false }); }}>取消交接</button>
    </div>}
    {candidate && <div className="line-login-candidate" role="group" aria-label="確認 LINE 登入帳號">
      <span>LINE 帳號：{candidate.displayName || candidate.userId.slice(-6)}</span>
      <button type="button" className="ghost-button" disabled={busy} onClick={() => void confirm()}>確認登入此帳號</button>
      <button type="button" className="ghost-button" disabled={busy} onClick={() => setCandidate(null)}>取消</button>
    </div>}
    {busy && pending.current && <p role="status">請在 LINE 完成驗證後，返回這個 Atlas 視窗。{loginUrl && <>配對碼 {linePairingCode(new URL(loginUrl).searchParams.get('atlasLineHandoff')!)} <a href={loginUrl} target="_blank" rel="noopener noreferrer">開啟 LINE 登入視窗</a></>}<button type="button" className="ghost-button" onClick={() => pending.current?.abort()}>取消登入</button></p>}
    <div className="pwa-login-actions">{access.userId
      ? <button type="button" className="ghost-button" disabled={busy} onClick={() => void logout()}>{isLineInAppBrowser() ? '登出 Atlas' : '登出 LINE'}</button>
      : <>
        <button type="button" className="ghost-button" disabled={busy} onClick={() => void login()}>{busy ? '正在開啟 LINE 登入…' : '使用 LINE 登入'}</button>
        <HelpDisclosure label="登入說明">點選登入後會啟用 LINE 登入所需的瀏覽器儲存；個人操作的保存仍需帳號授權。重新開啟 App 後可再點登入，以同一帳號取回已保存的畫線。</HelpDisclosure>
      </>}
    </div>
  </div>;
}
