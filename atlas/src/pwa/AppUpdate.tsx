import { useEffect, useState, useSyncExternalStore } from 'react';
import { accessState } from '../auth/access';
import { PUBLIC_BASE } from '../app/HostingMode';
import './appUpdate.css';

export type VersionState = 'checking' | 'current' | 'available' | 'unavailable';

/** Compare only validated metadata from this installed app's deployment scope. */
export async function checkAppVersion(current: string, base: URL, fetcher: typeof fetch = fetch): Promise<VersionState> {
  if (!/^[a-f0-9]{12}$/.test(current)) return 'unavailable';
  try {
    const target = new URL('app-version.json', base);
    const response = await fetcher(target.href, { cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(8000) });
    if (!response.ok || (response.url && response.url !== target.href)) return 'unavailable';
    const data: unknown = await response.json();
    if (!data || typeof data !== 'object') return 'unavailable';
    const value = data as Record<string, unknown>;
    if (value.app !== 'atlas' || value.scope !== base.pathname || typeof value.version !== 'string' || !/^[a-f0-9]{12}$/.test(value.version)) return 'unavailable';
    return value.version === current ? 'current' : 'available';
  } catch { return 'unavailable'; }
}

export function AppUpdate() {
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  const [status, setStatus] = useState<VersionState>('checking');
  const [requested, setRequested] = useState(0);
  useEffect(() => {
    let active = true;
    let request = 0;
    const current = document.querySelector<HTMLMetaElement>('meta[name="atlas-build"]')?.content ?? '';
    const check = () => {
      if (document.visibilityState === 'hidden') return;
      const id = ++request;
      void checkAppVersion(current, new URL(PUBLIC_BASE, location.origin)).then(next => { if (active && request === id) setStatus(next); });
    };
    check();
    window.addEventListener('online', check);
    document.addEventListener('visibilitychange', check);
    return () => { active = false; window.removeEventListener('online', check); document.removeEventListener('visibilitychange', check); };
  }, [requested]);
  return <section className="pwa-update" aria-label="版本更新">
    <h3>版本更新</h3>
    <p role="status">{status === 'checking' ? '正在檢查版本…' : status === 'current' ? '目前是最新版本' : status === 'available' ? '網站已有新版，可更新主畫面 App' : '暫時無法確認版本，連線恢復後可再檢查'}</p>
    {status === 'available' ? <>
      <p className="pwa-update-detail">{access.storageMode === 'none' ? '重新開啟會清除目前暫存操作。' : '請確認畫線與設定已儲存，再重新開啟。'}</p>
      <button type="button" className="primary-button" onClick={() => location.reload()}>更新並重新開啟</button>
    </> : <button type="button" className="ghost-button" disabled={status === 'checking'} onClick={() => { setStatus('checking'); setRequested(value => value + 1); }}>檢查更新</button>}
  </section>;
}
