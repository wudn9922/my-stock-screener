import { useState, useSyncExternalStore } from 'react';
import { Copy, Download, Share } from 'lucide-react';
import { pwaInstall, serverInstallSnapshot } from './install';
import './installPanel.css';

const subscribe = (listener: () => void) => pwaInstall?.subscribe(listener) ?? (() => {});
const getSnapshot = () => pwaInstall?.getSnapshot() ?? serverInstallSnapshot;
const getServerSnapshot = () => serverInstallSnapshot;

export function InstallPanel() {
  const state = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const [copyStatus, setCopyStatus] = useState('');
  const currentUrl = typeof window === 'undefined' ? '' : window.location.href;

  async function copyLink() {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(currentUrl);
      setCopyStatus('已複製連結，請貼到外部瀏覽器開啟。');
    } catch {
      setCopyStatus('無法自動複製，請長按下方連結複製。');
    }
  }

  return <section className="pwa-install" aria-label="安裝 Atlas">
    <h3><Download size={17} aria-hidden="true" />加入主畫面 <span>免費</span></h3>
    {state.installed ? <p role="status" className="pwa-install-status">
      {state.status === 'installed' ? 'Atlas 已加入主畫面。請從主畫面圖示開啟。' : '已從主畫面開啟 Atlas，可直接使用目前視窗。'}
    </p> : <>
      <p>將 Atlas 加到主畫面，直接開啟網頁版。</p>
      {state.inLine ? <>
        <ol>
          <li>在 LINE 右上角點「⋯」，選「{state.platform === 'ios' ? '在 Safari 開啟' : state.platform === 'android' ? '在 Chrome 開啟' : '在外部瀏覽器開啟'}」（或「以預設瀏覽器開啟」）。</li>
          <li>在外部瀏覽器開啟 Atlas「顯示設定」，依「加入主畫面」說明操作。</li>
        </ol>
        <p>若找不到開啟選項，複製連結後貼到{state.platform === 'ios' ? ' Safari' : state.platform === 'android' ? ' Chrome' : '外部瀏覽器'}。</p>
        <button className="pwa-install-button" type="button" onClick={() => { void copyLink(); }}><Copy size={16} aria-hidden="true" />複製目前頁面連結</button>
        <input className="pwa-install-link" aria-label="目前頁面連結" value={currentUrl} readOnly onFocus={event => event.currentTarget.select()} />
        <p role="status" aria-live="polite">{copyStatus}</p>
      </> : state.platform === 'ios' ? <>
        <ol>
          <li>在 Safari 點「分享」<Share size={15} aria-hidden="true" />；若未顯示，先點「⋯」。</li>
          <li>向下找到並選擇「加入主畫面」。</li>
          <li>若有「以 Web App 打開」（Open as Web App）開關，請保持開啟，再點「加入」。</li>
        </ol>
        <p>完成後，從主畫面的 Atlas 圖示開啟。若看不到加入選項，請先用 Safari 開啟此頁。</p>
      </> : <>
        {state.canPrompt && <button className="pwa-install-button" type="button" onClick={() => { void pwaInstall?.promptInstall(); }}><Download size={16} aria-hidden="true" />安裝 Atlas</button>}
        <p role="status" aria-live="polite" className="pwa-install-status">
          {state.status === 'prompting' && '請在瀏覽器的安裝視窗選擇是否安裝。'}
          {state.status === 'dismissed' && '已取消安裝，仍可繼續使用網頁版。'}
          {state.status === 'accepted' && '已送出安裝要求，請完成瀏覽器的安裝流程。'}
          {state.status === 'error' && '瀏覽器未能開啟安裝視窗，請改用下方選單操作。'}
        </p>
        {state.platform === 'android' ? <p>也可開啟 Chrome 選單「⋮」，選「安裝應用程式」或「加入主畫面」，並依瀏覽器提示完成。</p>
          : <p>Chrome／Edge 可使用網址列的安裝圖示，或在瀏覽器選單選「安裝 Atlas」／「安裝這個網站作為應用程式」。若瀏覽器未提供安裝功能，可保留書籤。</p>}
      </>}
    </>}
    <p className="pwa-install-note">行情與帳號功能需要網路；加入主畫面不會變更帳號的儲存權限。</p>
  </section>;
}
