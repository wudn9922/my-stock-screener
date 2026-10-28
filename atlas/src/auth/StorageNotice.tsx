import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { acceptLineHandoff, isLineInAppBrowser, lineLoginError, loginLineIdentity, logoutLineIdentity, refreshLineIdentity, type LineIdentity } from '../cloud/lineIdentity';
import { DEVICE_SESSION_DAYS, deviceSessionVersion, forgetDevice, rememberAfterRedirect, rememberDevice, rememberedDevice, subscribeDeviceSession } from '../cloud/deviceSession';
import { beginSeparateLineLogin, cancelIncomingLineHandoff, completeIncomingLineHandoff, incomingLineApproval, incomingLineConfirmationCode, lineHandoffMessage, linePairingCode, needsSeparateLineLogin, type LineCodeChallenge, type LoginCandidate } from '../cloud/lineLoginHandoff';
import { accessState, currentIdentity, refreshAccess } from './access';
import { HelpDisclosure } from '../ui/HelpDisclosure';
import './storageNotice.css';

/** After an explicit login: keep the device remembered, or forget an earlier one, as the checkbox says. */
function applyRemember(identity: LineIdentity | null, remember: boolean) {
  if (!identity?.idToken()) return;
  if (remember) void rememberDevice(identity);
  else forgetDevice();
}

/**
 * `full` (我的 page): status, login/logout and the remember-device choice. `gate`: the installed app's
 * login page. `alerts` (every other page): only an in-progress login, handoff approval or error.
 */
export function StorageNotice({ variant = 'full' }: { variant?: 'full' | 'gate' | 'alerts' }) {
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  useSyncExternalStore(subscribeDeviceSession, deviceSessionVersion);
  const [remember, setRemember] = useState(true);
  const [rememberBusy, setRememberBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [candidate, setCandidate] = useState<LoginCandidate | null>(null);
  const [loginUrl, setLoginUrl] = useState<string | null>(null);
  const [launchMissed, setLaunchMissed] = useState(false);
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
        setLaunchMissed(false);
        try { setCandidate(await beginSeparateLineLogin(controller.signal, setLoginUrl, challenge => {
          setCodeChallenge(challenge); setCheckingCode(false); setCode('');
        }, () => setLaunchMissed(true))); }
        finally { pending.current = null; setBusy(false); setLoginUrl(null); setLaunchMissed(false); setCodeChallenge(null); setCode(''); setCheckingCode(false); }
        return;
      }
      rememberAfterRedirect(remember);
      const result = await loginLineIdentity();
      if (result === 'authenticated') {
        rememberAfterRedirect(false);
        applyRemember(await refreshLineIdentity(), remember);
        await refreshAccess(); setBusy(false);
      }
    } catch (cause) {
      rememberAfterRedirect(false);
      setError(cause instanceof Error ? cause.message : 'LINE 登入未完成，請稍後重試。');
      setBusy(false);
    }
  };
  const confirm = async () => {
    if (!candidate) return;
    setBusy(true); setError(null);
    try {
      acceptLineHandoff(candidate); setCandidate(null);
      applyRemember(await refreshLineIdentity(), remember);
      await refreshAccess();
    }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'LINE 登入未完成。'); }
    finally { setBusy(false); }
  };
  const logout = async () => {
    setBusy(true); setError(null);
    try { logoutLineIdentity(); }
    catch { setError('LINE 登出未能完成，請關閉此頁後再試。'); }
    finally { await refreshAccess(); setBusy(false); }
  };
  const rememberNow = async () => {
    const identity = currentIdentity();
    if (!identity) return;
    setRememberBusy(true); setError(null);
    if (!await rememberDevice(identity)) setError('無法記住這台裝置，請稍後重試。');
    setRememberBusy(false);
  };
  const approveSender = async () => {
    setSenderState({ busy: true });
    try { await completeIncomingLineHandoff(); }
    finally { setSenderState({ busy: false }); }
  };
  const message = error || access.error || lineLoginError();
  const remembered = !!access.userId && rememberedDevice()?.userId === access.userId;
  const flows = <>
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
    {busy && pending.current && <p role="status">請在 LINE 完成驗證後，返回這個 Atlas 視窗。{loginUrl && <>配對碼 {linePairingCode(new URL(loginUrl).searchParams.get('atlasLineHandoff')!)} {launchMissed && <strong id="line-launch-missed" className="line-launch-missed">LINE 沒有自動開啟？請點這裡</strong>} <a href={loginUrl} target="_blank" rel="noopener noreferrer" className={launchMissed ? 'primary-button line-launch-link' : undefined} aria-describedby={launchMissed ? 'line-launch-missed' : undefined}>開啟 LINE 登入視窗</a></>}<button type="button" className="ghost-button" onClick={() => pending.current?.abort()}>取消登入</button></p>}
  </>;
  if (variant === 'alerts') {
    // Other pages stay clear unless a login or handoff needs attention right now.
    const active = message || lineHandoffMessage() || incomingLineConfirmationCode() || approval || candidate || codeChallenge || (busy && pending.current);
    if (!active) return null;
    return <div className="source-note storage-policy-note">
      {message && <p role="status">{message}</p>}
      {flows}
    </div>;
  }
  const rememberChoice = <label className="remember-device">
    <input type="checkbox" checked={remember} disabled={busy} onChange={event => setRemember(event.target.checked)} />
    記住這台裝置（{DEVICE_SESSION_DAYS} 天內免登入）
  </label>;
  return <div className={`source-note storage-policy-note${variant === 'gate' ? ' storage-policy-gate' : ''}`}>
    {variant === 'gate'
      ? message && <p role="alert">{message}</p>
      : <p role="status" data-storage-mode={access.storageMode}>
        {access.userId ? '已登入 · ' : '訪客模式 · '}{label}{remembered && ' 此裝置已記住登入。'}{message && ` ${message}`}
      </p>}
    {flows}
    <div className="pwa-login-actions">{access.userId
      ? <>
        <button type="button" className="ghost-button" disabled={busy} onClick={() => void logout()}>{isLineInAppBrowser() ? '登出 Atlas' : '登出 LINE'}</button>
        {!remembered && currentIdentity()?.kind !== 'device' && !isLineInAppBrowser() &&
          <button type="button" className="ghost-button" disabled={busy || rememberBusy} onClick={() => void rememberNow()}>記住這台裝置</button>}
      </>
      : <>
        <button type="button" className={variant === 'gate' ? 'primary-button' : 'ghost-button'} disabled={busy} onClick={() => void login()}>{busy ? '正在開啟 LINE 登入…' : '使用 LINE 登入'}</button>
        {!isLineInAppBrowser() && rememberChoice}
        <HelpDisclosure label="登入說明">點選登入後會啟用 LINE 登入所需的瀏覽器儲存；個人操作的保存仍需帳號授權。勾選「記住這台裝置」時，這台裝置會保存一組登入憑證，{DEVICE_SESSION_DAYS} 天內未使用才需重新登入；按「登出」即刪除。</HelpDisclosure>
      </>}
    </div>
  </div>;
}
