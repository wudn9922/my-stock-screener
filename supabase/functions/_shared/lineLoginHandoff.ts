/** Bounded, encrypted, one-use transport across PWA / Safari / LINE storage partitions. */
interface RpcDatabase {
  rpc(
    name: string,
    parameters: Record<string, unknown>,
  ): PromiseLike<{ data: unknown; error: { code?: string } | null }>;
}
export class LineHandoffError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}
const encoder = new TextEncoder();
const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SECRET = /^[A-Za-z0-9_-]{43}$/;
const bytes = (value: string) =>
  Uint8Array.from(atob(value.replace(/-/g, '+').replace(/_/g, '/')), (c) =>
    c.charCodeAt(0),
  );
export async function handoffHash(value: string) {
  return [
    ...new Uint8Array(
      await crypto.subtle.digest('SHA-256', encoder.encode(value)),
    ),
  ]
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}
async function key(secret: string) {
  return crypto.subtle.importKey(
    'raw',
    await crypto.subtle.digest(
      'SHA-256',
      encoder.encode(`atlas-line-handoff-v1:${secret}`),
    ),
    'AES-GCM',
    false,
    ['encrypt', 'decrypt'],
  );
}
export async function encryptHandoff(
  value: unknown,
  id: string,
  secret: string,
) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = new Uint8Array(
    await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv, additionalData: encoder.encode(id) },
      await key(secret),
      encoder.encode(JSON.stringify(value)),
    ),
  );
  return btoa(String.fromCharCode(...iv, ...ciphertext));
}
export async function decryptHandoff(
  value: string,
  id: string,
  secret: string,
) {
  const encrypted = bytes(value);
  return JSON.parse(
    new TextDecoder().decode(
      await crypto.subtle.decrypt(
        {
          name: 'AES-GCM',
          iv: encrypted.slice(0, 12),
          additionalData: encoder.encode(id),
        },
        await key(secret),
        encrypted.slice(12),
      ),
    ),
  );
}
export async function lineLoginHandoff(
  database: RpcDatabase,
  body: Record<string, unknown>,
  serverSecret: string,
  caller: string,
  verify: (idToken: string) => Promise<string>,
): Promise<Record<string, unknown>> {
  const operation = String(body.action).replace('line.login.', '');
  if (
    !['begin', 'complete', 'poll', 'cancel'].includes(operation) ||
    typeof body.intentId !== 'string' ||
    !UUID.test(body.intentId)
  )
    throw new LineHandoffError(400, '登入要求格式不正確。');
  const secretName = operation === 'complete' ? 'writeSecret' : 'readSecret';
  if (typeof body[secretName] !== 'string' || !SECRET.test(body[secretName]))
    throw new LineHandoffError(400, '登入驗證碼格式不正確。');
  const parameters: Record<string, unknown> = {
    operation,
    intent_id: body.intentId,
    [operation === 'complete' ? 'write_hash' : 'read_hash']: await handoffHash(
      body[secretName],
    ),
  };
  if (operation === 'begin') {
    if (typeof body.writeSecret !== 'string' || !SECRET.test(body.writeSecret))
      throw new LineHandoffError(400, '登入驗證碼格式不正確。');
    parameters.write_hash = await handoffHash(body.writeSecret);
    parameters.caller_bucket = await handoffHash(`${serverSecret}:${caller}`);
  }
  if (operation === 'complete') {
    if (typeof body.idToken !== 'string' || body.idToken.length > 8192)
      throw new LineHandoffError(401, 'LINE 登入驗證失敗。');
    const userId = await verify(body.idToken);
    const payload = JSON.parse(
      new TextDecoder().decode(bytes(body.idToken.split('.')[1])),
    );
    if (
      payload.sub !== userId ||
      !Number.isFinite(payload.exp) ||
      payload.exp * 1000 <= Date.now() + 60000
    )
      throw new LineHandoffError(401, 'LINE 登入已過期，請重新登入。');
    parameters.encrypted_token = await encryptHandoff(
      {
        idToken: body.idToken,
        userId,
        displayName:
          typeof payload.name === 'string' ? payload.name.slice(0, 80) : '',
        expiresAt: payload.exp * 1000,
      },
      body.intentId,
      serverSecret,
    );
  }
  const { data, error } = await database.rpc(
    'atlas_line_login_handoff',
    parameters,
  );
  if (error || !data)
    throw new LineHandoffError(503, '登入交接服務尚未就緒，請稍後再試。');
  const result = data as {
    status?: string;
    expiresAt?: string;
    ciphertext?: string;
  };
  if (result.status === 'busy')
    throw new LineHandoffError(429, '登入要求過多，請兩分鐘後再試。');
  if (result.status === 'missing')
    throw new LineHandoffError(
      404,
      '這次登入尚未就緒或已失效，請從原來的 Atlas 重新登入。',
    );
  if (result.status === 'used')
    throw new LineHandoffError(
      410,
      '這次登入已使用過，請從原來的 Atlas 重新登入。',
    );
  if (
    operation === 'poll' &&
    result.status === 'consumed' &&
    result.ciphertext
  ) {
    const identity = await decryptHandoff(
      result.ciphertext,
      body.intentId,
      serverSecret,
    );
    if (identity.expiresAt <= Date.now() + 60000)
      throw new LineHandoffError(410, '這次登入已過期。');
    return { status: 'complete', identity };
  }
  return { status: result.status, expiresAt: result.expiresAt };
}
