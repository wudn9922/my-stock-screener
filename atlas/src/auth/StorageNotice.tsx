import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { acceptLineHandoff, isLineInAppBrowser, lineLoginError, loginLineIdentity, logoutLineIdentity } from '../cloud/lineIdentity';
import { beginSeparateLineLogin, cancelIncomingLineHandoff, completeIncomingLineHandoff, incomingLineApproval, incomingLineConfirmationCode, lineHandoffMessage, linePairingCode, needsSeparateLineLogin, type LineCodeChallenge, type LoginCandidate } from '../cloud/lineLoginHandoff';
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
  const [codeChallenge, setCodeChallenge] = useState<LineCodeChallenge | null>(null);
  const [code, setCode] = useState('');
  const [checkingCode, setCheckingCode] = useState(false);
  const approval = incomingLineApproval();
  const pending = useRef<AbortController | null>(null);
  const codeInput = useRef<HTMLInputElement | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  useEffect(() => {
    let frame: number | undefined;
    const revealCode = () => {
      if (!pending.current || document.visibilityState === 'hidden') return;
      if (frame !== undefined) cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const input = codeInput.current;
        if (!pending.current || document.visibilityState === 'hidden' || !input || input.disabled) return;
        input.scrollIntoView({ block: 'center', inline: 'nearest' });
        input.focus({ preventScroll: true });
      });
    };
    window.addEventListener('focus', revealCode);
    document.addEventListener('visibilitychange', revealCode);
    revealCode(); // A readiness poll can finish just after the PWA becomes visible.
    return () => {
      if (frame !== undefined) cancelAnimationFrame(frame);
      window.removeEventListener('focus', revealCode);
      document.removeEventListener('visibilitychange', revealCode);
    };
  }, [codeChallenge, checkingCode]);
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
        try { setCandidate(await beginSeparateLineLogin(controller.signal, setLoginUrl, challenge => {
          setCodeChallenge(challenge); setCheckingCode(false); setCode('');
        })); }
        finally { pending.current = null; setBusy(false); setLoginUrl(null); setCodeChallenge(null); setCode(''); setCheckingCode(false); }
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
    {incomingLineConfirmationCode() && <div className="line-login-candidate" role="group" aria-label="LINE 登入確認碼">
      <span>確認碼 <strong className="line-reverse-code">{incomingLineConfirmationCode()}</strong> · 只輸入在你剛剛發起登入的 Atlas，請勿轉傳。</span>
    </div>}
    {approval && <div className="line-login-candidate" role="group" aria-label="確認 LINE 登入交接">
      <span>只有你剛剛在自己的裝置按了登入，才按確認</span>
      <span>配對碼 {approval.code}</span>
      <button type="button" className="ghost-button" disabled={senderState.busy} onClick={() => void approveSender()}>確認登入原來的 Atlas</button>
      <button type="button" className="ghost-button" disabled={senderState.busy} onClick={() => { cancelIncomingLineHandoff(); setSenderState({ busy: false }); }}>取消交接</button>
    </div>}
    {candidate && <div className="line-login-candidate" role="group" aria-label="確認 LINE 登入帳號">
      <span>LINE 帳號：{candidate.displayName || candidate.userId.slice(-6)}</span>
      <button type="button" className="ghost-button" disabled={busy} onClick={() => void confirm()}>確認登入此帳號</button>
      <button type="button" className="ghost-button" disabled={busy} onClick={() => setCandidate(null)}>取消</button>
    </div>}
    {codeChallenge && <form className="line-login-candidate" aria-label="輸入 LINE 確認碼" onSubmit={event => {
      event.preventDefault(); if (checkingCode || !/^\d{6}$/.test(code)) return;
      setCheckingCode(true); codeChallenge.submit(code); setCode('');
    }}>
      <label>LINE 頁面的六位確認碼 <input ref={codeInput} aria-label="LINE 六位確認碼" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="off" value={code} disabled={checkingCode} onChange={event => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))} /></label>
      <button className="ghost-button" type="submit" disabled={checkingCode || !/^\d{6}$/.test(code)}>{checkingCode ? '正在驗證確認碼…' : '驗證確認碼'}</button>
      {codeChallenge.error && <span role="alert">{codeChallenge.error}</span>}
    </form>}
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
