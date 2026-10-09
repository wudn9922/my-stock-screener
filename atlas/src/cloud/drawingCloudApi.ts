import { CloudAuthError, type DrawingCloudApi, type RemoteDrawings, type SaveResult } from './DrawingSync';

/**
 * Client for the Supabase Edge Function `chart-drawings` (actions `atlas.list` / `atlas.save`).
 * The function verifies the LIFF ID token with LINE and only touches that user's rows.
 */
export const DEFAULT_DRAWINGS_URL = 'https://bxhqpfeberqbtxymghyt.supabase.co/functions/v1/chart-drawings';

interface ApiRecord {
  symbol?: unknown;
  drawings?: unknown;
  updatedAt?: unknown;
}

function toRemote(record: ApiRecord | null | undefined): RemoteDrawings | null {
  if (!record || typeof record.symbol !== 'string' || !Array.isArray(record.drawings)) return null;
  const updatedAt = Number(record.updatedAt);
  if (!Number.isFinite(updatedAt)) return null;
  return { symbol: record.symbol, drawings: record.drawings, updatedAt };
}

export function createDrawingCloudApi(
  idToken: () => string | null,
  url = DEFAULT_DRAWINGS_URL,
  fetcher: typeof fetch = (input, init) => fetch(input, init),
): DrawingCloudApi {
  async function call(action: string, payload: Record<string, unknown> = {}) {
    const token = idToken();
    if (!token) throw new CloudAuthError('LINE login expired');
    const response = await fetcher(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...payload, action, idToken: token }),
    });
    const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    if (response.status === 401) throw new CloudAuthError(String(body.error ?? 'Unauthorized'));
    if (!response.ok || body.ok !== true && !body.conflict) throw new Error(String(body.error ?? `HTTP ${response.status}`));
    return body;
  }

  return {
    async list() {
      const body = await call('atlas.list');
      const records = Array.isArray(body.records) ? (body.records as ApiRecord[]) : [];
      return records.map(toRemote).filter((record): record is RemoteDrawings => record !== null);
    },
    async save(symbol, drawings, updatedAt): Promise<SaveResult> {
      const body = await call('atlas.save', { symbol, drawings, updatedAt });
      if (body.ok === true) return { ok: true, updatedAt: Number(body.updatedAt) || updatedAt };
      const conflict = toRemote(body.conflict as ApiRecord);
      if (!conflict) throw new Error('Invalid conflict response');
      return { ok: false, conflict };
    },
  };
}
