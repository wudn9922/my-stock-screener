import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { Bell, Share, Trash2 } from 'lucide-react';
import { accessState } from '../auth/access';
import { installPlatform } from '../pwa/install';
import { formatDateTime } from '../ui/format';
import {
  currentSubscription, disablePush, enablePush, fetchPushStatus, notificationPermission, pushSupport, removePushDevice,
  sendTestPush, webPushEnabled, type PushStatus,
} from './webPush';
import './pushPanel.css';

const message = (error: unknown) => error instanceof Error && error.message ? error.message : '手機通知暫時無法使用，請稍後再試。';

function DeniedHelp() {
  const platform = typeof navigator === 'undefined' ? 'desktop' : installPlatform(navigator);
  return <div className="push-help" role="note">
    <p><b>通知權限已被封鎖</b>，網站無法再次跳出詢問，需要到系統設定重新允許：</p>
    {platform === 'ios' ? <ol>
      <li>開啟 iPhone／iPad 的「設定」→「通知」。</li>
      <li>找到「Atlas」，開啟「允許通知」。</li>
      <li>回到 Atlas，再開啟「在這台裝置接收通知」。</li>
    </ol> : platform === 'android' ? <ol>
      <li>長按主畫面的 Atlas 圖示 →「應用程式資訊」→「通知」，或在 Chrome 網址列左側點設定圖示 →「權限」。</li>
      <li>將「通知」改為「允許」。</li>
      <li>回到 Atlas，再開啟「在這台裝置接收通知」。</li>
    </ol> : <ol>
      <li>點網址列左側的鎖頭或設定圖示 →「網站設定」。</li>
      <li>將「通知」改為「允許」，然後重新整理頁面。</li>
    </ol>}
  </div>;
}

function IosGuide() {
  return <div className="push-help">
    <p>iPhone／iPad 只能在<b>加入主畫面的 Atlas</b>收到通知（需 iOS 16.4 以上）：</p>
    <ol>
      <li>在 Safari 點「分享」<Share size={15} aria-hidden="true" />。</li>
      <li>選「加入主畫面」，再點「加入」。</li>
      <li>從主畫面的 Atlas 圖示開啟並登入。</li>
      <li>到「我的 → 手機通知」開啟「在這台裝置接收通知」。</li>
    </ol>
  </div>;
}

/** 手機通知: one toggle for this device, a test button and this account's device list. */
export function PushPanel() {
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  const [support] = useState(() => (typeof window === 'undefined' ? 'unsupported' : pushSupport()));
  const [permission, setPermission] = useState(() => notificationPermission());
  const [status, setStatus] = useState<PushStatus | null>(null);
  const [subscribed, setSubscribed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const ready = support === 'supported' && !!access.userId && access.storageMode === 'cloud';

  const refresh = useCallback(async () => {
    try {
      const [next, subscription] = await Promise.all([fetchPushStatus(), currentSubscription()]);
      setStatus(next);
      // On only when this browser holds a subscription that this account owns.
      setSubscribed(!!subscription && next.devices.some(device => device.current));
      setError('');
    } catch (failure) {
      setError(message(failure));
    }
  }, []);
  useEffect(() => { if (ready) void refresh(); }, [ready, access.epoch, refresh]);

  if (!webPushEnabled()) return null;
  const run = async (task: () => Promise<string | void>) => {
    setBusy(true); setNotice(''); setError('');
    try {
      const result = await task();
      if (result) setNotice(result);
      await refresh();
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(false);
      setPermission(notificationPermission());
    }
  };
  const toggle = () => run(async () => {
    if (subscribed) { await disablePush(); return '已關閉這台裝置的通知。'; }
    const result = await enablePush();
    if (result !== 'granted') return result === 'denied' ? '' : '尚未允許通知，可再按一次開啟。';
    return '已開啟。突破提醒會同時傳到這台裝置，點通知即可開啟圖表。';
  });

  let body;
  if (support === 'line') body = <p className="push-state">LINE 內建瀏覽器無法接收通知。請在右上角「⋯」選擇以 Safari／Chrome 開啟 Atlas 後再設定。</p>;
  else if (support === 'ios-install') body = <IosGuide />;
  else if (support === 'unsupported') body = <p className="push-state">這個瀏覽器不支援網頁通知。Android 請使用 Chrome；iPhone／iPad 請將 Atlas 加入主畫面（iOS 16.4 以上）。</p>;
  else if (!access.userId) body = <p className="push-state">請先使用 LINE 登入，才能讓這台裝置收到你的股票提醒。</p>;
  else if (access.storageMode !== 'cloud') body = <p className="push-state">手機通知需要帳號的雲端儲存授權；目前帳號未開啟，請聯絡管理員。</p>;
  else body = <>
    {permission === 'denied' && !subscribed ? <DeniedHelp /> : <label className="push-toggle">
      <span>
        <b>在這台裝置接收通知</b>
        <small>{subscribed ? '已開啟：突破與畫線提醒會推播到這台裝置。' : '關閉中：開啟後會詢問通知權限。'}</small>
      </span>
      <input type="checkbox" role="switch" checked={subscribed} disabled={busy} onChange={() => { void toggle(); }} />
    </label>}
    <button type="button" className="pwa-install-button push-test" disabled={busy || !status?.devices.length}
      onClick={() => { void run(async () => {
        const result = await sendTestPush();
        if (!result.sent && result.failed) return '通知服務未接受這次推播，請稍後再試。';
        return result.sent ? `已傳送到 ${result.sent} 台裝置，請查看通知並點一下確認會開啟圖表。` : '沒有可接收的裝置，請先開啟通知。';
      }); }}>傳送測試通知</button>
    {status && <section className="push-devices" aria-label="已開啟通知的裝置">
      <h4>已開啟通知的裝置（{status.devices.length}／{status.limit}）</h4>
      {status.devices.length ? <ul>
        {status.devices.map(device => <li key={device.id}>
          <span>
            <b>{device.label}{device.current && <em>這台裝置</em>}</b>
            <small>{device.service} · {device.lastSuccessAt ? `上次送達 ${formatDateTime(device.lastSuccessAt)}` : `加入 ${formatDateTime(device.createdAt, false)}`}{device.failureCount > 0 && ` · 失敗 ${device.failureCount} 次`}</small>
          </span>
          <button type="button" className="push-remove" disabled={busy} aria-label={`移除 ${device.label}`}
            onClick={() => { void run(async () => { await removePushDevice(device); return `已移除 ${device.label}。`; }); }}>
            <Trash2 size={16} aria-hidden="true" />移除
          </button>
        </li>)}
      </ul> : <p className="push-state">尚無裝置。</p>}
      {!status.configured && <p className="push-state">伺服器尚未設定推播金鑰，暫時無法送出通知。</p>}
    </section>}
  </>;

  return <section className="pwa-install push-panel" aria-label="手機通知">
    <h3><Bell size={17} aria-hidden="true" />手機通知 <span>免費</span></h3>
    <p>股票突破或畫線提醒時，手機會跳出通知；點通知直接開啟該股票的 Atlas 圖表。Discord 提醒照常保留。</p>
    {body}
    <p role="status" aria-live="polite" className="pwa-install-status">{notice}</p>
    {error && <p role="alert" className="push-error">{error}</p>}
  </section>;
}
