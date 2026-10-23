import { useSyncExternalStore } from 'react';
import { accessState } from './access';

export function StorageNotice() {
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  const label = access.storageMode === 'cloud'
    ? '此帳號已獲雲端儲存授權。'
    : access.storageMode === 'device'
      ? '僅保存在此裝置，不會同步個人資料到 Supabase。'
      : '僅本次瀏覽暫存，重新整理即清除，不會保存個人操作。';
  return <p className="source-note storage-policy-note" role="status" data-storage-mode={access.storageMode}>
    {access.userId ? '已登入 · ' : '訪客模式 · '}{label}{access.error && ` ${access.error}`}
  </p>;
}
