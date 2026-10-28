import {
  holdingsId,
  holdingsObject,
  holdingDecimal,
  holdingsRevision,
  validateHoldingAccount,
  validateHoldingsDocument,
} from './holdings.ts';
import { MEXC_ERRORS, validateMexcState, type MexcBalance, type MexcQuote } from './mexcContract.ts';

type ErrorCode = keyof typeof MEXC_ERRORS;
export class MexcError extends Error {
  constructor(
    readonly code: ErrorCode,
    readonly status = 400,
    readonly retryAfter = 0,
  ) {
    super(MEXC_ERRORS[code]);
  }
}
interface Database {
  rpc(name: string, params: Record<string, unknown>): PromiseLike<{ data: unknown; error: { code?: string } | null }>;
}
interface Credentials {
  apiKey: string;
  apiSecret: string;
}
interface Envelope {
  v: 1;
  iv: string;
  data: string;
}
const encoder = new TextEncoder();
const assetPattern = /^[A-Z0-9]{1,20}$/;
const origin = 'https://api.mexc.com';
const scale = 10n ** 18n;
export function mexcDecimal(input: unknown): string {
  if (typeof input !== 'string' || !/^\d{1,15}(?:\.\d{1,40})?$/.test(input)) throw new MexcError('source', 502);
  const [integer, fraction] = input.split('.');
  const value = (integer!.replace(/^0+(?=\d)/, '') + (fraction ? '.' + fraction.replace(/0+$/, '') : '')).replace(
    /\.$/,
    '',
  );
  try {
    return holdingDecimal(value, true);
  } catch {
    throw new MexcError('source', 502);
  }
}
function units(text: string): bigint {
  const [integer, fraction = ''] = text.split('.');
  return BigInt(integer!) * scale + BigInt(fraction.padEnd(18, '0'));
}
function fromUnits(value: bigint): string {
  return `${value / scale}.${String(value % scale).padStart(18, '0')}`.replace(/\.?0+$/, '');
}
export function parseMexcBalances(value: unknown): MexcBalance[] {
  const row = holdingsObject(value);
  if (row.accountType !== 'SPOT' || !Array.isArray(row.balances) || row.balances.length > 10000)
    throw new MexcError('source', 502);
  const result: MexcBalance[] = [],
    seen = new Set<string>();
  for (const raw of row.balances) {
    const item = holdingsObject(raw);
    const free = mexcDecimal(item.free),
      locked = mexcDecimal(item.locked);
    const quantity = fromUnits(units(free) + units(locked));
    try {
      holdingDecimal(quantity, true);
    } catch {
      throw new MexcError('limit', 400);
    }
    if (quantity === '0') continue;
    if (typeof item.asset !== 'string' || !assetPattern.test(item.asset) || seen.has(item.asset))
      throw new MexcError('source', 502);
    seen.add(item.asset);
    result.push({ asset: item.asset, symbol: `${item.asset}/USDT`, free, locked, quantity });
  }
  if (result.length > 1000) throw new MexcError('limit');
  return result.sort((a, b) => a.asset.localeCompare(b.asset));
}
export function assertReadOnlyKey(value: unknown, apiKey: string) {
  const row = holdingsObject(value);
  const reads = new Set([
    'SPOT_ACCOUNT_READ',
    'SPOT_DEAL_READ',
    'CONTRACT_ACCOUNT_READ',
    'CONTRACT_DEAL_READ',
    'SPOT_TRANSFER_READ',
    'SPOT_WITHDRAW_READ',
  ]);
  const permissions =
    typeof row.permissions === 'string'
      ? row.permissions
          .split(',')
          .map((p) => p.trim())
          .filter(Boolean)
      : [];
  if (
    (row.accessKey ?? row.apikey) !== apiKey ||
    row.status !== 'VALID' ||
    !Number.isInteger(row.remainingValidity) ||
    (Number(row.remainingValidity) <= 0 && row.remainingValidity !== -999)
  )
    throw new MexcError('credentials');
  if (!permissions.includes('SPOT_ACCOUNT_READ') || permissions.some((p) => !reads.has(p)))
    throw new MexcError('permission');
}
function credentials(value: Record<string, unknown>): Credentials {
  if (
    typeof value.apiKey !== 'string' ||
    !/^[A-Za-z0-9_-]{8,128}$/.test(value.apiKey) ||
    typeof value.apiSecret !== 'string' ||
    !/^[!-~]{8,256}$/.test(value.apiSecret)
  )
    throw new MexcError('invalid');
  return { apiKey: value.apiKey, apiSecret: value.apiSecret };
}
const base64 = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes));
function unbase64(value: string) {
  return Uint8Array.from(atob(value), (char) => char.charCodeAt(0));
}
async function encryptionKey(value: string | undefined) {
  try {
    if (!value || !/^[A-Za-z0-9+/]{43}=$/.test(value)) throw new Error();
    const bytes = unbase64(value);
    if (bytes.length !== 32) throw new Error();
    return await crypto.subtle.importKey('raw', bytes, 'AES-GCM', false, ['encrypt', 'decrypt']);
  } catch {
    throw new MexcError('setup', 503);
  }
}
const aad = (owner: string, accountId: string) => encoder.encode(`atlas:mexc:v1:${owner}:${accountId}`);
export async function encryptMexcCredentials(
  data: Credentials,
  key: CryptoKey,
  owner: string,
  accountId: string,
): Promise<Envelope> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encrypted = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv, additionalData: aad(owner, accountId), tagLength: 128 },
    key,
    encoder.encode(JSON.stringify(data)),
  );
  return { v: 1, iv: base64(iv), data: base64(new Uint8Array(encrypted)) };
}
export async function decryptMexcCredentials(
  raw: unknown,
  key: CryptoKey,
  owner: string,
  accountId: string,
): Promise<Credentials> {
  try {
    const row = holdingsObject(raw);
    if (row.v !== 1 || typeof row.iv !== 'string' || typeof row.data !== 'string' || row.data.length > 2048)
      throw new Error();
    const iv = unbase64(row.iv);
    if (iv.length !== 12) throw new Error();
    const plain = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv, additionalData: aad(owner, accountId), tagLength: 128 },
      key,
      unbase64(row.data),
    );
    return credentials(JSON.parse(new TextDecoder().decode(plain)));
  } catch {
    throw new MexcError('credentials');
  }
}
export async function mexcSignature(secret: string, query: string) {
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, [
    'sign',
  ]);
  return [...new Uint8Array(await crypto.subtle.sign('HMAC', key, encoder.encode(query)))]
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}
async function database(db: Database, owner: string, operation: string, payload: Record<string, unknown> = {}) {
  let result;
  try {
    result = await db.rpc('atlas_mexc_action', { operation, owner_id: owner, payload });
  } catch {
    throw new MexcError('service', 503);
  }
  if (result.error)
    throw new MexcError(
      ['PGRST202', 'PGRST205', '42883', '42P01', '3F000'].includes(result.error.code ?? '') ? 'setup' : 'service',
      503,
    );
  const row = holdingsObject(result.data);
  if (row.status === 'rate_limited')
    throw new MexcError('limit', 429, Math.min(3600, Math.max(1, Number(row.retryAfter) || 30)));
  if (row.status === 'conflict' || row.status === 'missing') throw new MexcError('conflict', 409);
  if (row.status === 'limit') throw new MexcError('limit');
  if (row.status !== 'ok') throw new MexcError('invalid');
  return row;
}
/** Fixed-origin, GET-only MEXC client. Neither upstream errors nor signed URLs escape this module. */
async function readMexc(
  path: '/api/v3/apiKeyInfo' | '/api/v3/account' | '/api/v3/ticker/price',
  fetcher: typeof fetch,
  signal: AbortSignal,
  now: number,
  auth?: Credentials,
): Promise<unknown> {
  const query = new URLSearchParams();
  if (auth) {
    if (path === '/api/v3/apiKeyInfo') query.set('accessKey', auth.apiKey);
    query.set('recvWindow', '5000');
    query.set('timestamp', String(now));
    query.set('signature', await mexcSignature(auth.apiSecret, query.toString()));
  }
  let response;
  try {
    response = await fetcher(`${origin}${path}${query.size ? '?' + query : ''}`, {
      method: 'GET',
      headers: auth ? { 'X-MEXC-APIKEY': auth.apiKey } : {},
      signal,
      redirect: 'error',
      cache: 'no-store',
      referrerPolicy: 'no-referrer',
    });
  } catch {
    throw new MexcError('source', 502);
  }
  if ([418, 429].includes(response.status))
    throw new MexcError('limit', 429, Math.min(3600, Math.max(30, Number(response.headers.get('Retry-After')) || 30)));
  if (!response.ok) throw new MexcError(auth && response.status < 500 ? 'credentials' : 'source', 502);
  // Bound both declared and streamed response size; do not consume arbitrary error bodies.
  if (Number(response.headers.get('Content-Length')) > 2 * 1024 * 1024 || !response.body)
    throw new MexcError('source', 502);
  const reader = response.body.getReader(),
    chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      size += part.value.byteLength;
      if (size > 2 * 1024 * 1024) {
        await reader.cancel();
        throw new Error();
      }
      chunks.push(part.value);
    }
    const data = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      data.set(chunk, offset);
      offset += chunk.length;
    }
    const parsed: unknown = JSON.parse(new TextDecoder().decode(data));
    if (parsed && typeof parsed === 'object' && 'code' in parsed) throw new Error();
    return parsed;
  } catch {
    throw new MexcError('source', 502);
  }
}
function quotesFrom(value: unknown, balances: MexcBalance[], now: number): Record<string, MexcQuote> {
  if (!Array.isArray(value) || value.length > 20000) throw new MexcError('source', 502);
  const quotes: Record<string, MexcQuote> = {},
    wanted = new Set(balances.map((b) => b.asset + 'USDT'));
  for (const raw of value) {
    const item = holdingsObject(raw);
    if (typeof item.symbol !== 'string' || !wanted.has(item.symbol)) continue;
    const symbol = item.symbol.slice(0, -4) + '/USDT';
    if (symbol in quotes) throw new MexcError('source', 502);
    const price = mexcDecimal(item.price);
    if (price !== '0')
      quotes[symbol] = {
        price,
        previous: null,
        asOf: new Date(now).toISOString(),
        source: 'MEXC 現貨（同步取回時間；未提供前日收盤）',
      };
  }
  if (balances.some((b) => b.asset === 'USDT'))
    quotes['USDT/USDT'] = {
      price: '1',
      previous: null,
      asOf: new Date(now).toISOString(),
      source: 'USDT 計價單位（非 USD/TWD 匯率）',
    };
  return quotes;
}
/** Global requireCloudStorage MUST precede this function; authorize rechecks after upstream I/O. */
export async function handleMexc(
  db: Database,
  owner: string,
  body: Record<string, unknown>,
  master: string | undefined,
  authorize: () => Promise<unknown>,
  fetcher: typeof fetch = fetch,
  now: () => number = Date.now,
) {
  const operation = String(body.action).slice('holdings.mexc.'.length);
  if (operation === 'status') {
    const row = await database(db, owner, 'status');
    return validateMexcState({
      connections: row.connections,
      retryAt: row.retryAt,
      configured: !!master && /^[A-Za-z0-9+/]{43}=$/.test(master),
    });
  }
  const accountId = operation === 'connect' ? validateHoldingAccount(body.account).id : holdingsId(body.accountId);
  if (operation === 'disconnect') {
    const row = await database(db, owner, 'disconnect', { accountId });
    return { ...publicState(row), document: validateHoldingsDocument(row.document) };
  }
  if (operation === 'apply') {
    const costs = holdingsObject(body.costs);
    if (Object.keys(costs).length > 1000 || Object.keys(costs).some((key) => !assetPattern.test(key)))
      throw new MexcError('invalid');
    for (const value of Object.values(costs)) holdingDecimal(value, true);
    const expectedRevision = holdingsRevision(body.expectedRevision);
    if (expectedRevision === null) throw new MexcError('invalid');
    const row = await database(db, owner, 'apply', {
      accountId,
      snapshotId: holdingsId(body.snapshotId),
      expectedRevision,
      costs,
    });
    return { ...publicState(row), document: validateHoldingsDocument(row.document) };
  }
  if (operation !== 'connect' && operation !== 'sync') throw new MexcError('invalid');
  const key = await encryptionKey(master);
  let auth: Credentials | undefined, account;
  if (operation === 'connect') {
    account = validateHoldingAccount(body.account);
    if (
      account.broker !== 'mexc' ||
      account.kind !== 'crypto-exchange' ||
      account.currency !== 'USDT' ||
      body.readOnlyConfirmed !== true
    )
      throw new MexcError('permission');
    auth = credentials(body);
  }
  const requestId = crypto.randomUUID();
  const claim = await database(db, owner, 'claim', {
    purpose: operation,
    accountId,
    requestId,
    ...(account ? { account, expectedRevision: holdingsRevision(body.expectedRevision) } : {}),
  });
  if (!auth) auth = await decryptMexcCredentials(claim.credential, key, owner, accountId);
  const controller = new AbortController(),
    timeout = setTimeout(() => controller.abort(), 9000);
  try {
    assertReadOnlyKey(await readMexc('/api/v3/apiKeyInfo', fetcher, controller.signal, now(), auth), auth.apiKey);
    const balances = parseMexcBalances(await readMexc('/api/v3/account', fetcher, controller.signal, now(), auth));
    const quotes = quotesFrom(
      await readMexc('/api/v3/ticker/price', fetcher, controller.signal, now()),
      balances,
      now(),
    );
    const envelope = operation === 'connect' ? await encryptMexcCredentials(auth, key, owner, accountId) : undefined;
    try {
      await authorize();
    } catch {
      throw new MexcError('authorization', 403);
    }
    const row = await database(db, owner, 'finish', {
      purpose: operation,
      accountId,
      requestId,
      expectedRevision: claim.expectedRevision,
      ...(account ? { account } : {}),
      ...(envelope ? { credential: envelope, keyLast4: auth.apiKey.slice(-4) } : {}),
      balances,
      quotes,
    });
    return { ...publicState(row), document: validateHoldingsDocument(row.document) };
  } catch (error) {
    // No raw network errors, signed URLs, credentials, or balances enter logs.
    if (error instanceof MexcError && error.retryAfter) {
      try {
        await database(db, owner, 'backoff', { requestId, seconds: error.retryAfter });
      } catch {
        /* Fail closed; do not retry upstream. */
      }
    }
    if (error instanceof MexcError) throw error;
    throw new MexcError('source', 502);
  } finally {
    clearTimeout(timeout);
  }
}
function publicState(row: Record<string, unknown>) {
  return validateMexcState({ connections: row.connections, retryAt: row.retryAt, configured: true });
}
