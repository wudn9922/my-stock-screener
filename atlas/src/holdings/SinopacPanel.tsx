import { useEffect, useState } from 'react';
import { getStorageContextKey } from '../auth/storage';
import { holdingsContextApi } from './contextApi';
import { holdingsStore } from './store';
import { formatDateTime } from '../ui/format';
interface Status {
  accountId: string;
  lastSyncAt: string | null;
  lastSuccessAt: string | null;
  code: string | null;
  skippedShorts: number;
}
const messages: Record<string, string> = {
  ok: '正常',
  closed: '休市未同步',
  login: '永豐登入失敗',
  permission: '永豐帳務權限不足',
  quota: '永豐流量超限，請稍後再試',
  timeout: '永豐同步逾時，請稍後再試',
  calendar: '交易日曆尚未就緒',
  invalid: '永豐資料無法完整辨識，未更新持股',
  conflict: '持股已更新，請重新同步',
  service: '永豐同步暫時無法使用',
};
export function SinopacPanel({
  accountId,
  onCreated,
  onManaged,
}: {
  accountId?: string;
  onCreated?: () => void;
  onManaged?: (id: string | null) => void;
}) {
  const [status, setStatus] = useState<Status | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const context = getStorageContextKey();
  useEffect(() => {
    let active = true;
    setStatus(null);
    setError('');
    void holdingsContextApi<Status>(context, 'holdings.sinopac.status', accountId ? { accountId } : {})
      .then((s) => {
        if (active) {
          setStatus(s);
          onManaged?.(s.accountId);
        }
      })
      .catch(() => {
        if (active) {
          setError('永豐同步尚未設定或此帳號無權限，請聯絡維護者。');
          onManaged?.(null);
        }
      });
    return () => {
      active = false;
    };
  }, [context, accountId]);
  return (
    <section className="console-card" aria-label="永豐台股自動同步">
      <h3>永豐證券台股持股唯讀同步</h3>
      <p>
        請先到永豐建立只勾「行情/資料、帳務、正式環境」、不勾「交易」的金鑰，並由維護者設定好服務。請勿在網頁輸入金鑰。
      </p>
      {error && <p role="alert">{error}</p>}
      {status && (
        <>
          <p role="status">{status.code ? (messages[status.code] ?? messages.service) : '尚未同步'}</p>
          <p>
            上次同步時間：
            {status.lastSyncAt ? formatDateTime(status.lastSyncAt) : '—'}
            ；上次成功：
            {status.lastSuccessAt ? formatDateTime(status.lastSuccessAt) : '—'}
          </p>
          {status.skippedShorts > 0 && <p>有 {status.skippedShorts} 筆融券部位未匯入</p>}
        </>
      )}
      <button
        type="button"
        className="primary-button"
        disabled={busy || !status}
        onClick={() => {
          setBusy(true);
          setError('');
          void holdingsContextApi<Status>(context, 'holdings.sinopac.sync', {
            accountId: status!.accountId,
          })
            .then(async (next) => {
              await holdingsStore().reload();
              if (context === getStorageContextKey()) {
                setStatus(next);
                onCreated?.();
              }
            })
            .catch((error: unknown) => {
              if (context === getStorageContextKey()) {
                const message = error instanceof Error ? error.message : '';
                setError(
                  [...Object.values(messages), '60 秒內僅能同步一次'].includes(message)
                    ? message
                    : '永豐同步未完成，請重新載入狀態後再試。',
                );
              }
            })
            .finally(() => setBusy(false));
        }}
      >
        {busy ? '同步中…' : '同步'}
      </button>
    </section>
  );
}
