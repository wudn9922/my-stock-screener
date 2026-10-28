/** Public response contract only. Never add credentials or raw upstream responses here. */
import { holdingDecimal, holdingsId, holdingsObject } from './holdings.ts';
export interface MexcBalance {
  asset: string;
  symbol: string;
  free: string;
  locked: string;
  quantity: string;
}
export interface MexcQuote {
  price: string;
  previous: null;
  asOf: string;
  source: string;
}
export interface MexcConnection {
  accountId: string;
  keyLast4: string;
  connectedAt: string;
  syncedAt: string;
  snapshotId: string;
  holdingRevision: number;
  needsReview: boolean;
  balances: MexcBalance[];
  quotes: Record<string, MexcQuote>;
}
export interface MexcState {
  connections: MexcConnection[];
  retryAt: string | null;
  configured: boolean;
}
export function validateMexcState(value: unknown): MexcState {
  const row = holdingsObject(value);
  const timestamp = (v: unknown): string => {
    if (typeof v !== 'string' || !/^\d{4}-\d{2}-\d{2}T/.test(v) || !Number.isFinite(Date.parse(v)))
      throw new Error('MEXC 回應格式不正確。');
    return v;
  };
  if (!Array.isArray(row.connections) || row.connections.length > 30 || typeof row.configured !== 'boolean')
    throw new Error('MEXC 回應格式不正確。');
  const connections = row.connections.map((raw) => {
    const item = holdingsObject(raw);
    if (
      typeof item.keyLast4 !== 'string' ||
      !/^[A-Za-z0-9_-]{4}$/.test(item.keyLast4) ||
      typeof item.needsReview !== 'boolean' ||
      !Number.isSafeInteger(item.holdingRevision) ||
      Number(item.holdingRevision) < 0 ||
      !Array.isArray(item.balances) ||
      item.balances.length > 1000
    )
      throw new Error('MEXC 回應格式不正確。');
    const balances = item.balances.map((rawBalance) => {
      const b = holdingsObject(rawBalance);
      if (typeof b.asset !== 'string' || !/^[A-Z0-9]{1,20}$/.test(b.asset) || b.symbol !== `${b.asset}/USDT`)
        throw new Error('MEXC 回應格式不正確。');
      return {
        asset: b.asset,
        symbol: b.symbol as string,
        free: holdingDecimal(b.free, true),
        locked: holdingDecimal(b.locked, true),
        quantity: holdingDecimal(b.quantity),
      };
    });
    if (new Set(balances.map((b) => b.asset)).size !== balances.length) throw new Error('MEXC 回應格式不正確。');
    const quotes: Record<string, MexcQuote> = {};
    for (const [symbol, rawQuote] of Object.entries(holdingsObject(item.quotes))) {
      if (!balances.some((b) => b.symbol === symbol)) throw new Error('MEXC 回應格式不正確。');
      const q = holdingsObject(rawQuote);
      if (q.previous !== null || typeof q.source !== 'string' || q.source.length > 120)
        throw new Error('MEXC 回應格式不正確。');
      quotes[symbol] = { price: holdingDecimal(q.price), previous: null, asOf: timestamp(q.asOf), source: q.source };
    }
    return {
      accountId: holdingsId(item.accountId),
      keyLast4: item.keyLast4,
      connectedAt: timestamp(item.connectedAt),
      syncedAt: timestamp(item.syncedAt),
      snapshotId: holdingsId(item.snapshotId),
      holdingRevision: Number(item.holdingRevision),
      needsReview: item.needsReview,
      balances,
      quotes,
    };
  });
  if (new Set(connections.map((c) => c.accountId)).size !== connections.length)
    throw new Error('MEXC 回應格式不正確。');
  return { connections, retryAt: row.retryAt === null ? null : timestamp(row.retryAt), configured: row.configured };
}
export const MEXC_ERRORS = {
  invalid: 'MEXC 資料格式不正確，請檢查輸入。',
  setup: 'MEXC 同步尚未啟用，請由維護者完成設定。',
  permission: '請使用只含讀取權限的 MEXC API 金鑰，不可包含交易、提幣或其他寫入權限。',
  authorization: '雲端儲存授權已變更，請重新開啟 Atlas 確認。',
  credentials: 'MEXC 金鑰無法驗證，請確認金鑰、期限與 IP 白名單。',
  source: 'MEXC 暫時無法讀取，已保留原本持股，請稍後重試。',
  limit: 'MEXC 同步次數或持股數量已達限制，請稍後再試。',
  conflict: '持股或 MEXC 連線已更新，請重新載入後再操作。',
  service: '無法確認 MEXC 操作結果，請重新載入持股確認。',
} as const;
