import { StorageAccessError, requireCloudStorage, type PolicyDatabase } from '../_shared/storageAccess.ts';
import { DeviceSessionError } from '../_shared/deviceSession.ts';
import { projectSignal, type MonitorSignal, type MonitorSnapshot } from './contract.ts';

export const PAGE_SIZE = 500;
export const MAX_SIGNALS = 5000;
export const WINDOW_DAYS = 30;
export const SIGNAL_COLUMNS = 'line_user_id,group_id,ticker,market,ma_period,last_alert_at,last_alert_direction,last_alert_price,last_alert_ma_value,last_alert_volume_ratio,metadata';
interface SignalQuery extends PromiseLike<{ data: unknown[] | null; error: unknown }> {
  select(columns: string): SignalQuery;
  eq(column: string, value: string): SignalQuery;
  gte(column: string, value: string): SignalQuery;
  order(column: string, options?: { ascending?: boolean }): SignalQuery;
  range(from: number, to: number): SignalQuery;
}
class SignalError extends Error {
  constructor(readonly status: number, message: string) { super(message); }
}
const ORIGINS = new Set(['https://wudn9922.github.io', 'http://localhost:5173', 'http://127.0.0.1:4177']);

/** All personal reads use the verified owner, never a caller-supplied user or group. */
export function createMonitorHandler(deps: {
  database: PolicyDatabase;
  verifyIdentity: (token: string) => Promise<string>;
  adminId: string;
  now?: () => number;
}) {
  return async (request: Request) => {
    const origin = request.headers.get('origin');
    const headers = {
      'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', Vary: 'Origin',
      ...(origin && ORIGINS.has(origin) ? { 'Access-Control-Allow-Origin': origin } : {}),
      'Access-Control-Allow-Headers': 'content-type', 'Access-Control-Allow-Methods': 'POST, OPTIONS',
    };
    const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers });
    if (origin && !ORIGINS.has(origin)) return reply({ ok: false, error: '不支援的網站來源。' }, 403);
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers });
    if (request.method !== 'POST') return reply({ ok: false, error: '僅支援唯讀查詢。' }, 405);
    try {
      if (Number(request.headers.get('content-length')) > 16_384) throw new SignalError(413, '查詢內容過大。');
      const reader = request.body?.getReader();
      const chunks: Uint8Array[] = []; let size = 0;
      if (reader) {
        try {
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            size += value.byteLength;
            if (size > 16_384) { await reader.cancel(); throw new SignalError(413, '查詢內容過大。'); }
            chunks.push(value);
          }
        } finally { reader.releaseLock(); }
      }
      const bytes = new Uint8Array(size); let cursor = 0;
      for (const chunk of chunks) { bytes.set(chunk, cursor); cursor += chunk.byteLength; }
      const raw = new TextDecoder().decode(bytes);
      let body;
      try { body = JSON.parse(raw); } catch { throw new SignalError(400, '查詢格式不正確。'); }
      if (!body || body.action !== 'list' || typeof body.idToken !== 'string' || !body.idToken || body.idToken.length > 8192)
        throw new SignalError(400, '缺少有效的唯讀查詢。');
      const owner = await deps.verifyIdentity(body.idToken);
      await requireCloudStorage(deps.database, owner, deps.adminId);
      const now = (deps.now ?? Date.now)();
      const windowStart = new Date(now - WINDOW_DAYS * 86_400_000).toISOString();
      const signals: MonitorSignal[] = [];
      let count = 0, truncated = false;
      for (let offset = 0; offset <= MAX_SIGNALS; offset += PAGE_SIZE) {
        const { data, error } = await (deps.database.from('breakout_alert_state') as SignalQuery)
          .select(SIGNAL_COLUMNS).eq('line_user_id', owner).gte('last_alert_at', windowStart)
          .order('last_alert_at', { ascending: false }).order('group_id').order('ticker').order('ma_period')
          .range(offset, offset + PAGE_SIZE - 1);
        if (error || !Array.isArray(data)) throw new SignalError(503, '監控訊號暫時無法讀取，請稍後重試。');
        for (const raw of data) {
          if (++count > MAX_SIGNALS) { truncated = true; break; }
          if (!raw || typeof raw !== 'object' || (raw as Record<string, unknown>).line_user_id !== owner)
            throw new SignalError(503, '監控訊號暫時無法讀取，請稍後重試。');
          const signal = projectSignal(raw);
          if (signal && Date.parse(signal.sentAt) >= Date.parse(windowStart) && Date.parse(signal.sentAt) <= now + 60_000) signals.push(signal);
        }
        if (truncated || data.length < PAGE_SIZE) break;
      }
      // A revoked grant during a multi-page read must not return old private rows.
      await requireCloudStorage(deps.database, owner, deps.adminId);
      const data: MonitorSnapshot = { version: 1, generatedAt: new Date(now).toISOString(), windowStart, signals, truncated };
      return reply({ ok: true, data });
    } catch (error) {
      if (error instanceof SignalError || error instanceof StorageAccessError || error instanceof DeviceSessionError)
        return reply({ ok: false, error: error.message }, error.status);
      return reply({ ok: false, error: '監控訊號暫時無法讀取，請稍後重試。' }, 503);
    }
  };
}
