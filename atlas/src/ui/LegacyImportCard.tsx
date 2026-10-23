import { useEffect, useSyncExternalStore } from 'react';
import { accessState } from '../auth/access';
import {
  dismissLegacyImport,
  ensureLegacyImport,
  importLegacyData,
  legacyImportState,
  showLegacyImportAgain,
} from '../storage/legacyImport';
import './legacyImport.css';

/** Legacy browser data belongs to no account until this account explicitly copies it. */
export function LegacyImportCard() {
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot, accessState.getSnapshot);
  const state = useSyncExternalStore(legacyImportState.subscribe, legacyImportState.getSnapshot, legacyImportState.getSnapshot);
  const allowed = access.ready && !!access.userId && (access.storageMode === 'device' || access.storageMode === 'cloud');
  useEffect(() => {
    if (allowed) void ensureLegacyImport();
  }, [allowed, access.epoch]);
  // Check the live permission independently of the controller's last result.
  if (!allowed || ['idle', 'checking', 'dismissed'].includes(state.status)) return null;
  if (state.status === 'empty') {
    return <p className="legacy-import-empty" role="status">這台裝置沒有可匯入的舊版資料。</p>;
  }
  const busy = state.status === 'importing';
  return (
    <aside className="legacy-import-card" aria-label="舊版資料匯入" data-testid="legacy-import-card" aria-busy={busy}>
      {state.status === 'imported' ? (
        <p role="status">已匯入 {state.added} 項；{state.conflicts} 項衝突保留帳號版本。</p>
      ) : state.status === 'error' ? (
        <>
          <p role="alert">舊版資料匯入未完成：{state.error || '無法讀取這台裝置的舊版資料。'}</p>
          <button className="ghost-button" type="button" onClick={() => void showLegacyImportAgain()}>重新檢查舊版資料</button>
        </>
      ) : (
        <>
          <p>
            這台裝置有舊版資料（畫線 {state.counts.drawings}、指標 {state.counts.indicators}、清單 {state.counts.lists}、設定 {state.counts.settings}），要匯入到你的帳號嗎？
          </p>
          <div className="legacy-import-actions">
            <button className="primary-button" type="button" data-testid="legacy-import-confirm" disabled={busy} onClick={() => void importLegacyData()}>
              {busy ? '匯入中…' : '匯入'}
            </button>
            <button className="ghost-button" type="button" data-testid="legacy-import-dismiss" disabled={busy} onClick={dismissLegacyImport}>
              暫時不要
            </button>
          </div>
        </>
      )}
      <small>只新增帳號缺少的項目；衝突保留帳號版本。舊版資料維持原樣，可從控制台重新顯示匯入提示。</small>
      {state.warnings?.map((warning) => <small key={warning}>{warning}</small>)}
    </aside>
  );
}
