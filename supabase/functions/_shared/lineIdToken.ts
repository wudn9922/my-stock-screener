/**
 * Verify LINE ID tokens for market-chart, user-settings and chart-drawings. Callers can distinguish
 * a rejected token (401) from an unavailable verifier (503) through DeviceSessionError.
 */
import { DeviceSessionError } from './deviceSession.ts';
import { parseStorageUserId } from './storageAccess.ts';

export const LINE_VERIFY_URL = 'https://api.line.me/oauth2/v2.1/verify';
const LINE_VERIFY_TIMEOUT_MS = 8000;

export class LineIdTokenError extends DeviceSessionError {
  constructor(status: 401 | 503, message: string, readonly legacyMessage = message) {
    super(status, message);
  }
}

const unavailable = () => new LineIdTokenError(503, '登入驗證服務暫時無法使用，請稍後再試。');

export interface LineVerifyOptions {
  channelId: string | null | undefined;
  fetcher?: typeof fetch;
  timeoutMs?: number;
}

export async function verifyLineIdTokenWith(idToken: string, options: LineVerifyOptions): Promise<string> {
  if (!options.channelId) throw new LineIdTokenError(503, '登入驗證服務尚未設定，請稍後再試。');
  const fetcher = options.fetcher ?? fetch;
  const signal = AbortSignal.timeout(options.timeoutMs ?? LINE_VERIFY_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetcher(LINE_VERIFY_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ id_token: idToken, client_id: options.channelId }).toString(),
      signal,
      redirect: 'error',
    });
  } catch {
    throw unavailable();
  }
  if (response.status >= 500) throw unavailable();
  if (!response.ok) throw new LineIdTokenError(401, '登入已失效，請重新登入。', 'Invalid LINE ID Token');
  const identity = await response.json().catch((error: unknown) => {
    if (signal.aborted || !(error instanceof SyntaxError)) throw unavailable();
    return null;
  }) as { sub?: unknown } | null;
  try {
    return parseStorageUserId(typeof identity?.sub === 'string' ? identity.sub.trim() : '');
  } catch {
    throw new LineIdTokenError(401, '登入已失效，請重新登入。', 'Invalid LINE User ID');
  }
}
