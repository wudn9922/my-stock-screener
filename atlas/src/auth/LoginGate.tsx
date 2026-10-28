import { PUBLIC_BASE, SCREENER_HOSTING } from '../app/HostingMode';
import { isLineInAppBrowser, lineGuestOnly } from '../cloud/lineIdentity';
import { rememberedDevice } from '../cloud/deviceSession';
import type { AccessSnapshot } from './access';
import { StorageNotice } from './StorageNotice';
import './loginGate.css';

/** An installed (home-screen) Atlas, where a login page replaces the in-page login controls. */
export function isInstalledApp() {
  return (globalThis.navigator as Navigator & { standalone?: boolean } | undefined)?.standalone === true ||
    globalThis.matchMedia?.('(display-mode: standalone)').matches === true;
}

/**
 * `gate`: the installed app opens on the login page until the visitor logs in or chooses guest for this
 * launch. `wait`: a remembered device is still being checked, so the page must not flash. Browsers and
 * LINE keep today's in-page login on 我的.
 */
export function loginGateState(access: AccessSnapshot, guestChosen: boolean): 'gate' | 'wait' | null {
  if (!SCREENER_HOSTING || access.userId || guestChosen || lineGuestOnly() || isLineInAppBrowser() || !isInstalledApp()) return null;
  if (!access.settled && rememberedDevice()) return 'wait';
  return 'gate';
}

export function LoginGate({ onGuest }: { onGuest: () => void }) {
  return <main className="login-gate" aria-labelledby="login-gate-title">
    <div className="login-gate-card">
      <img src={`${PUBLIC_BASE}icon.svg`} alt="" width={64} height={64} />
      <h1 id="login-gate-title">Atlas</h1>
      <p className="login-gate-sub">登入 LINE 帳號，取回你的自選清單、畫線與設定。</p>
      <StorageNotice variant="gate" />
      <button type="button" className="ghost-button login-gate-guest" onClick={onGuest}>先以訪客瀏覽</button>
      <p className="login-gate-note">訪客模式的操作只暫存到關閉 App 為止；之後可在「我的」登入。</p>
    </div>
  </main>;
}
