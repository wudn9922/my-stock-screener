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
/** Hosted supabase.co ingress only. Never fall back to caller-controlled forwarding headers. */
export function lineNetworkPrefix(headers: Headers): string | null {
  const ip = headers.get('cf-connecting-ip')?.trim();
  if (!ip || /[\s,/%\[\]]/.test(ip)) return null;
  if (/^(?:\d{1,3}\.){3}\d{1,3}$/.test(ip)) {
    const parts = ip.split('.');
    if (parts.some((p) => String(Number(p)) !== p || Number(p) > 255))
      return null;
    return `${parts.slice(0, 3).join('.')}.0/24`;
  }
  if (!ip.includes(':')) return null;
  try {
    // WHATWG rejects malformed IPv6 and canonicalizes dotted IPv4 suffixes.
    const normalized = new URL(`http://[${ip}]/`).hostname.slice(1, -1);
    const [left, right] = normalized.split('::');
    const a = left ? left.split(':') : [],
      b = right ? right.split(':') : [];
    const words =
      right === undefined
        ? a
        : [...a, ...Array(8 - a.length - b.length).fill('0'), ...b];
    if (words.length !== 8) return null;
    const numbers = words.map((v) => parseInt(v, 16));
    // Cloudflare cross-zone Workers share this sentinel; it is not a caller's network.
    if (
      numbers.join(',') ===
      [0x2a06, 0x98c0, 0x3600, 0, 0, 0, 0, 0x103].join(',')
    )
      return null;
    if (numbers.slice(0, 5).every((v) => v === 0) && numbers[5] === 0xffff) {
      return `${numbers[6] >>> 8}.${numbers[6] & 255}.${numbers[7] >>> 8}.0/24`;
    }
    return (
      numbers
        .slice(0, 4)
        .map((v) => v.toString(16).padStart(4, '0'))
        .join(':') + '::/64'
    );
  } catch {
    return null;
  }
}
function confirmationCode() {
  const range = 1000000,
    limit = Math.floor(0x100000000 / range) * range;
  let value: number;
  do {
    value = crypto.getRandomValues(new Uint32Array(1))[0];
  } while (value >= limit);
  return String(value % range).padStart(6, '0');
}
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
  headers: Headers,
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
  let code: string | undefined;
  if (operation === 'begin' || operation === 'complete') {
    const prefix = lineNetworkPrefix(headers);
    if (!prefix)
      throw new LineHandoffError(
        403,
        '無法確認登入網路，請在同一個網路下完成登入。',
      );
    parameters.network_hash = await handoffHash(
      `${serverSecret}:atlas-line-network-v1:${prefix}`,
    );
  }
  if (operation === 'begin') {
    if (typeof body.writeSecret !== 'string' || !SECRET.test(body.writeSecret))
      throw new LineHandoffError(400, '登入驗證碼格式不正確。');
    parameters.write_hash = await handoffHash(body.writeSecret);
    parameters.caller_bucket = parameters.network_hash;
  }
  if (operation === 'complete') {
    if (body.confirmed !== true)
      throw new LineHandoffError(400, '請先核對配對碼並確認登入交接。');
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
    code = confirmationCode();
    parameters.confirmation_hash = await handoffHash(
      `${serverSecret}:atlas-line-code-v1:${body.intentId}:${code}`,
    );
  }
  if (operation === 'poll' && body.code !== undefined && body.code !== '') {
    if (typeof body.code !== 'string' || !/^\d{6}$/.test(body.code))
      throw new LineHandoffError(400, '請輸入 LINE 頁面顯示的六位確認碼。');
    parameters.confirmation_hash = await handoffHash(
      `${serverSecret}:atlas-line-code-v1:${body.intentId}:${body.code}`,
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
    attemptsRemaining?: number;
  };
  if (result.status === 'network_mismatch')
    throw new LineHandoffError(403, '請在同一個網路下完成登入');
  if (result.status === 'restart_required')
    throw new LineHandoffError(
      410,
      '登入交接已更新，請從原來的 Atlas 重新登入。',
    );
  if (result.status === 'locked')
    throw new LineHandoffError(410, '確認碼錯誤已達五次，請重新登入。');
  if (operation === 'complete' && result.status === 'complete')
    return { status: 'complete', code };
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
    if (!parameters.confirmation_hash)
      throw new LineHandoffError(
        503,
        '登入交接服務需更新確認碼保護，請重新登入。',
      );
    const identity = await decryptHandoff(
      result.ciphertext,
      body.intentId,
      serverSecret,
    );
    if (identity.expiresAt <= Date.now() + 60000)
      throw new LineHandoffError(410, '這次登入已過期。');
    return { status: 'complete', identity };
  }
  return {
    status: result.status,
    expiresAt: result.expiresAt,
    attemptsRemaining: result.attemptsRemaining,
  };
}
