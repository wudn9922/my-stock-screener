import { useEffect, useState } from 'react';
import { getStorageContextKey } from '../auth/storage';
import { formatDateTime } from '../ui/format';
import { holdingsContextApi } from './contextApi';
import { holdingsStore } from './store';

export const STATEMENT_BROKERS = [
  { key: 'cathay', label: '國泰證券', markets: ['US'] as const },
  { key: 'sinopac', label: '永豐證券（格式收集中）', markets: [] as const },
  { key: 'kgi', label: '凱基證券（格式收集中）', markets: [] as const },
];
export interface StatementStatus {
  links: { accountId: string; broker: string; market: 'TW' | 'US'; pending: number }[];
  unlinkedPending: number;
  messages: { broker: string | null; receivedAt: string | null; reportDate: string | null; status: string; trades: number; processedAt: string }[];
}
interface ApplyResult {
  status: string;
  applied: number;
  symbol?: string;
  tradeDate?: string;
}
const MESSAGES: Record<string, string> = {
  ok: '已套用',
  'no-account': '成交已保存，尚未連結帳戶',
  oversell: '賣出股數超過帳上持股，已暫停套用',
  conflict: '持股剛被修改，請按「立即套用」重試',
  forbidden: '雲端儲存未授權，未更新持股',
  password: 'PDF 密碼不正確，未讀取',
  unrecognized: '內容無法完整辨識，未更新持股',
  unsupported: '尚未支援此券商或版面',
  ignored: '不是對帳單，已略過',
  service: '服務暫時無法使用',
};
const label = (code: string) => MESSAGES[code] ?? MESSAGES.service!;
const brokerName = (key: string | null) => STATEMENT_BROKERS.find((b) => b.key === key)?.label.replace(/（.*$/, '') ?? '—';

export function applyText(result: ApplyResult): string {
  if (result.status === 'ok') return result.applied ? `已套用 ${result.applied} 筆成交。` : '沒有待套用的成交。';
  if (result.status === 'oversell')
    return `${result.tradeDate ?? ''} ${result.symbol ?? ''} 賣出股數超過帳上持股，已暫停套用。請用「修正持股」補上該檔在這筆成交前的持股，再按「立即套用」。`.trim();
  return label(result.status) + '。';
}

/** Owner-only status and controls for an account linked to e-mail statements. */
export function StatementPanel({ accountId, status, onChanged }: {
  accountId: string;
  status: StatementStatus;
  onChanged: (message: string) => void;
}) {
  const link = status.links.find((l) => l.accountId === accountId);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const context = getStorageContextKey();
  useEffect(() => setError(''), [accountId]);
  if (!link) return null;
  const messages = status.messages.filter((m) => m.broker === link.broker || m.broker === null).slice(0, 8);
  const run = (action: string, done: (value: never) => string) => {
    setBusy(true);
    setError('');
    void holdingsContextApi<never>(context, action, { accountId })
      .then(async (value) => {
        await holdingsStore().reload();
        if (context === getStorageContextKey()) onChanged(done(value));
      })
      .catch((reason: unknown) => {
        if (context === getStorageContextKey()) setError(reason instanceof Error ? reason.message : '對帳單同步暫時無法使用。');
      })
      .finally(() => setBusy(false));
  };
  return (
    <section className="console-card" aria-label="Email 對帳單同步">
      <h3>Email 對帳單同步 · {brokerName(link.broker)} {link.market === 'US' ? '美股' : '台股'}</h3>
      <p className="holdings-muted">
        每天由 Gmail 自動送出券商 PDF 對帳單，讀到的買賣會加減到這個帳戶；配股、分割等請用「修正持股」調整。
      </p>
      <p role="status">{link.pending ? `${link.pending} 筆成交待套用` : '沒有待套用的成交'}</p>
      {error && <p role="alert">{error}</p>}
      {messages.length > 0 ? (
        <ul className="holdings-statement-log" aria-label="最近處理的對帳單">
          {messages.map((m, index) => (
            <li key={index}>
              <span>{m.reportDate?.replaceAll('-', '/') ?? (m.receivedAt ? formatDateTime(m.receivedAt, false) : '—')}</span>
              <span>{label(m.status)}</span>
              {m.trades > 0 && <span>{m.trades} 筆</span>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="holdings-muted">尚未收到對帳單。</p>
      )}
      <div className="holdings-toolbar">
        <button type="button" className="primary-button" disabled={busy}
          onClick={() => run('holdings.statement.apply', (value: ApplyResult) => applyText(value))}>
          {busy ? '處理中…' : '立即套用'}
        </button>
        <button type="button" className="text-button" disabled={busy}
          onClick={() => {
            if (confirm('取消連結後不再自動更新此帳戶，持股會保留。確定取消？'))
              run('holdings.statement.unlink', () => '已取消對帳單連結，持股保留。');
          }}>
          取消連結
        </button>
      </div>
    </section>
  );
}
