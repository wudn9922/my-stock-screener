/**
 * Remembered-device sign-in. A random device token (sent in the usual `idToken` field with the
 * `atlasdev.` prefix) stands in for a LINE ID token; only its SHA-256 hash is stored. It proves the
 * LINE account only: callers still check the storage grant on every request.
 */
interface RpcDatabase {
  rpc(
    name: string,
    parameters: Record<string, unknown>,
  ): PromiseLike<{ data: unknown; error: { code?: string } | null }>;
}
export class DeviceSessionError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}
export const DEVICE_TOKEN_PREFIX = 'atlasdev.';
const DEVICE_TOKEN = /^atlasdev\.[A-Za-z0-9_-]{43}$/;
const LINE_USER = /^U[0-9a-f]{32}$/;

export const isDeviceToken = (token: string) => token.startsWith(DEVICE_TOKEN_PREFIX);

async function tokenHash(token: string) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(token));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

async function deviceRpc(database: RpcDatabase, parameters: Record<string, unknown>) {
  const { data, error } = await database.rpc('atlas_device_session', parameters);
  if (error) {
    console.error('Device session RPC failed', error.code);
    throw new DeviceSessionError(503, '裝置登入服務暫時無法使用，請稍後重試。');
  }
  return (data ?? {}) as { status?: string; userId?: string; expiresAt?: string };
}

/** The LINE user behind a device token or a LINE ID token. */
export async function resolveLineUser(
  database: RpcDatabase,
  token: string,
  verifyLineIdToken: (idToken: string) => Promise<string>,
): Promise<string> {
  if (!isDeviceToken(token)) return verifyLineIdToken(token);
  if (!DEVICE_TOKEN.test(token)) throw new DeviceSessionError(401, '此裝置的登入已失效，請重新登入。');
  const result = await deviceRpc(database, { operation: 'use', session_hash: await tokenHash(token) });
  if (result.status !== 'active' || !LINE_USER.test(result.userId ?? '')) throw new DeviceSessionError(401, '此裝置的登入已失效，請重新登入。');
  return result.userId!;
}

/** Only a fresh LINE login may remember a device; a device token never mints another. */
export async function createDeviceSession(
  database: RpcDatabase,
  token: string,
  verifyLineIdToken: (idToken: string) => Promise<string>,
  userAgent: string | null,
) {
  if (isDeviceToken(token)) throw new DeviceSessionError(400, '請先使用 LINE 登入，再記住這台裝置。');
  const userId = await verifyLineIdToken(token);
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  const secret = btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  const deviceToken = `${DEVICE_TOKEN_PREFIX}${secret}`;
  const result = await deviceRpc(database, {
    operation: 'create', session_hash: await tokenHash(deviceToken), line_user_id: userId, agent: userAgent?.slice(0, 200) ?? null,
  });
  if (result.status !== 'created') throw new DeviceSessionError(503, '無法記住這台裝置，請稍後重試。');
  return { userId, deviceToken, expiresAt: result.expiresAt ?? null };
}

/** Sign-out forgets the device server-side; an unknown or malformed token is already signed out. */
export async function revokeDeviceSession(database: RpcDatabase, token: string) {
  if (!DEVICE_TOKEN.test(token)) return { revoked: false };
  await deviceRpc(database, { operation: 'revoke', session_hash: await tokenHash(token) });
  return { revoked: true };
}
