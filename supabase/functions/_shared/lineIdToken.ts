/**
 * LINE ID token verification shared by new callers (market-chart). It sends the same request as
 * user-settings / chart-drawings (POST id_token + client_id to LINE's verify endpoint); those
 * functions keep their own copies unchanged. Errors are DeviceSessionError so resolveLineUser
 * callers can tell a rejected token (401) from an unavailable verifier (503).
 */
import { DeviceSessionError } from './deviceSession.ts';
import { parseStorageUserId } from './storageAccess.ts';

export const LINE_VERIFY_URL = 'https://api.line.me/oauth2/v2.1/verify';
const LINE_VERIFY_TIMEOUT_MS = 8000;

export interface LineVerifyOptions {
  channelId: string | null | undefined;
  fetcher?: typeof fetch;
  timeoutMs?: number;
}

export async function verifyLineIdTokenWith(idToken: string, options: LineVerifyOptions): Promise<string> {
  if (!options.channelId) throw new DeviceSessionError(503, '登入驗證服務尚未設定，請稍後再試。');
  const fetcher = options.fetcher ?? fetch;
  let response: Response;
  try {
    response = await fetcher(LINE_VERIFY_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ id_token: idToken, client_id: options.channelId }).toString(),
      signal: AbortSignal.timeout(options.timeoutMs ?? LINE_VERIFY_TIMEOUT_MS),
      redirect: 'error',
    });
  } catch {
    throw new DeviceSessionError(503, '登入驗證服務暫時無法使用，請稍後再試。');
  }
  const identity = await response.json().catch(() => null) as { sub?: unknown } | null;
  if (response.status >= 500) throw new DeviceSessionError(503, '登入驗證服務暫時無法使用，請稍後再試。');
  if (!response.ok || !identity || typeof identity.sub !== 'string') throw new DeviceSessionError(401, '登入已失效，請重新登入。');
  try {
    return parseStorageUserId(identity.sub.trim());
  } catch {
    throw new DeviceSessionError(401, '登入已失效，請重新登入。');
  }
}
