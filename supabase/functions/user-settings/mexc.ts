import {
  holdingsId,
  holdingsObject,
  holdingDecimal,
  holdingsRevision,
  validateHoldingAccount,
  validateHoldingsDocument,
} from './holdings.ts';
import {
  MEXC_ERRORS,
  SKIPPED_ASSET_CONTROL,
  SKIPPED_ASSET_NAME_LENGTH,
  SKIPPED_ASSET_NAME_LIMIT,
  validateMexcState,
  type MexcBalance,
  type MexcQuote,
} from './mexcContract.ts';
import { readMexcFutures, futuresDiagnosticMetadata, type FuturesStep } from './mexcFutures.ts';

type ErrorCode = keyof typeof MEXC_ERRORS;
export class MexcError extends Error {
  constructor(
    readonly code: ErrorCode,
    readonly status = 400,
    readonly retryAfter = 0,
    readonly reason: string = code,
  ) {
    super(MEXC_ERRORS[code]);
  }
}
type DiagnosticStep = 'apiKeyInfo' | 'account' | 'ticker' | 'parse-balances' | 'parse-quotes' | 'assert-key' | FuturesStep;
const safeKeys = new Set([
  'code',
  'msg',
  'data',
  'note',
  'apikey',
  'accessKey',
  'status',
  'permissions',
  'ipWhiteList',
  'createTime',
  'remainingValidity',
  'accountType',
  'balances',
  'canTrade',
  'canWithdraw',
  'canDeposit',
  'updateTime',
  'makerCommission',
  'takerCommission',
  'buyerCommission',
  'sellerCommission',
  'symbol',
  'price',
  'timestamp',
]);
const safeMessages = new Set([
  'Api key info invalid',
  'invalid access key',
  'API-key format invalid',
  'Signature for this request is not valid',
  'Signature verification failed',
  'API KEY information does not exist',
  'No authority',
  'No permission to access the endpoint',
  'IP non white list',
  'Too Many Requests',
  'Internal error',
  'service not available, please try again',
  'success',
]);
/** Only fixed metadata crosses the logging boundary. Unknown names/messages can
 * contain echoed credentials or asset names, so they are redacted, never sliced. */
function diagnostic(step: DiagnosticStep, status: number | null, value: unknown, reason: string) {
  const row =
    value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
  if (step.startsWith('futures-')) {
    console.warn(JSON.stringify({ step, status, reason,
      ...futuresDiagnosticMetadata(value),
      ...(typeof row.code === 'number' && Number.isSafeInteger(row.code) ? { code: row.code } : {}),
      ...(typeof row.msg === 'string' && safeMessages.has(row.msg) ? { msg: row.msg } : {}),
    }));
    return;
  }
  const keys = [
    ...new Set(
      Object.keys(row)
        .slice(0, 80)
        .map((key) => (safeKeys.has(key) ? key : '[other]')),
    ),
  ].sort();
  const includeError = (status !== null && status >= 400 && status < 500) || 'code' in row;
  const code = typeof row.code === 'number' && Number.isSafeInteger(row.code) ? row.code : undefined;
  console.warn(
    JSON.stringify({
      step,
      status,
      topLevelKeys: keys,
      reason,
      ...(includeError && code !== undefined ? { code } : {}),
      ...(includeError && 'msg' in row
        ? {
            msg: typeof row.msg === 'string' && safeMessages.has(row.msg) ? row.msg : '[redacted]',
          }
        : {}),
    }),
  );
}
function sourceFailure(reason: string): never {
  throw new MexcError('source', 502, 0, reason);
}
function parseFailure(step: DiagnosticStep, value: unknown, status: number | null, error: unknown): never {
  const failure = error instanceof MexcError ? error : new MexcError('source', 502, 0, 'response-object');
  diagnostic(step, status, value, failure.reason);
  throw failure;
}
interface Database {
  rpc(
    name: string,
    params: Record<string, unknown>,
  ): PromiseLike<{ data: unknown; error: { code?: string } | null }>;
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
  if (typeof input !== 'string' || !/^\d{1,15}(?:\.\d{1,40})?$/.test(input)) sourceFailure('decimal-format');
  const [integer, fraction] = input.split('.');
  const value = (
    integer!.replace(/^0+(?=\d)/, '') + (fraction ? '.' + fraction.replace(/0+$/, '') : '')
  ).replace(/\.$/, '');
  try {
    return holdingDecimal(value, true);
  } catch {
    sourceFailure('decimal-range');
  }
}
function units(text: string): bigint {
  const [integer, fraction = ''] = text.split('.');
  return BigInt(integer!) * scale + BigInt(fraction.padEnd(18, '0'));
}
function fromUnits(value: bigint): string {
  return `${value / scale}.${String(value % scale).padStart(18, '0')}`.replace(/\.?0+$/, '');
}
export function parseMexcBalances(
  value: unknown,
  status: number | null = null,
  diagnosticValue: unknown = value,
): { balances: MexcBalance[]; skippedAssets: number; skippedAssetNames: string[] } {
  try {
    return balancesFrom(value);
  } catch (error) {
    return parseFailure('parse-balances', diagnosticValue, status, error);
  }
}
/** At most 20 raw names, control/format characters removed, 40 code points each. Response-only, never logged. */
export function skippedAssetName(raw: string): string {
  return Array.from(raw.replace(SKIPPED_ASSET_CONTROL, ''))
    .slice(0, SKIPPED_ASSET_NAME_LENGTH)
    .join('');
}
function balancesFrom(value: unknown): {
  balances: MexcBalance[];
  skippedAssets: number;
  skippedAssetNames: string[];
} {
  const row = holdingsObject(value);
  if (row.accountType !== 'SPOT' || !Array.isArray(row.balances) || row.balances.length > 10000)
    sourceFailure('account-shape');
  const result: MexcBalance[] = [],
    seen = new Set<string>();
  const reasons = { 'asset-invalid': 0, 'asset-duplicate': 0 };
  const classes = new Set<string>();
  const skippedNames = new Set<string>();
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
    const asset = typeof item.asset === 'string' ? item.asset.toUpperCase() : '';
    const invalid = !assetPattern.test(asset);
    if (invalid || seen.has(asset)) {
      reasons[invalid ? 'asset-invalid' : 'asset-duplicate']++;
      if (typeof item.asset === 'string' && skippedNames.size < SKIPPED_ASSET_NAME_LIMIT) {
        const name = skippedAssetName(item.asset);
        if (name.trim()) skippedNames.add(name);
      }
      if (typeof item.asset !== 'string') classes.add('non-string');
      else {
        if (/[a-z]/.test(item.asset)) classes.add('lowercase');
        if (/[^A-Z0-9]/.test(asset)) classes.add('symbol');
        if (asset.length > 20) classes.add('too-long');
        if (!asset.length) classes.add('empty');
      }
      if (!invalid) classes.add('duplicate');
      continue;
    }
    seen.add(asset);
    result.push({
      asset,
      symbol: `${asset}/USDT`,
      free,
      locked,
      quantity,
    });
  }
  if (result.length > 1000) throw new MexcError('limit');
  const skippedAssets = reasons['asset-invalid'] + reasons['asset-duplicate'];
  if (skippedAssets) {
    console.warn(
      JSON.stringify({
        step: 'parse-balances',
        reason: 'asset-skipped',
        count: skippedAssets,
        reasons,
        classes: [...classes].sort(),
      }),
    );
  }
  return {
    balances: result.sort((a, b) => a.asset.localeCompare(b.asset)),
    skippedAssets,
    // Names go only to this user's connect/sync response; the log line above stays count/class only.
    skippedAssetNames: [...skippedNames],
  };
}
export function assertReadOnlyKey(
  value: unknown,
  apiKey: string,
  status: number | null = null,
  diagnosticValue: unknown = value,
) {
  try {
    return readOnlyKey(value, apiKey);
  } catch (error) {
    return parseFailure('assert-key', diagnosticValue, status, error);
  }
}
function readOnlyKey(value: unknown, apiKey: string) {
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
    throw new MexcError('credentials', 400, 0, 'key-invalid-or-expired');
  if (!permissions.includes('SPOT_ACCOUNT_READ') || permissions.some((p) => !reads.has(p)))
    throw new MexcError('permission', 400, 0, 'read-only-permissions');
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
    {
      name: 'AES-GCM',
      iv,
      additionalData: aad(owner, accountId),
      tagLength: 128,
    },
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
      {
        name: 'AES-GCM',
        iv,
        additionalData: aad(owner, accountId),
        tagLength: 128,
      },
      key,
      unbase64(row.data),
    );
    return credentials(JSON.parse(new TextDecoder().decode(plain)));
  } catch {
    throw new MexcError('credentials');
  }
}
export async function mexcSignature(secret: string, query: string) {
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  return [...new Uint8Array(await crypto.subtle.sign('HMAC', key, encoder.encode(query)))]
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}
async function database(
  db: Database,
  owner: string,
  operation: string,
  payload: Record<string, unknown> = {},
) {
  let result;
  try {
    result = await db.rpc('atlas_mexc_action', {
      operation,
      owner_id: owner,
      payload,
    });
  } catch {
    throw new MexcError('service', 503);
  }
  if (result.error)
    throw new MexcError(
      ['PGRST202', 'PGRST205', '42883', '42P01', '3F000'].includes(result.error.code ?? '')
        ? 'setup'
        : 'service',
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
export async function readMexc(
  path: '/api/v3/apiKeyInfo' | '/api/v3/account' | '/api/v3/ticker/price',
  fetcher: typeof fetch,
  signal: AbortSignal,
  now: number,
  auth?: Credentials,
): Promise<{ value: unknown; status: number; diagnosticValue: unknown }> {
  const query = new URLSearchParams();
  if (auth) {
    if (path === '/api/v3/apiKeyInfo') query.set('accessKey', auth.apiKey);
    query.set('recvWindow', '5000');
    query.set('timestamp', String(now));
    query.set('signature', await mexcSignature(auth.apiSecret, query.toString()));
  }
  const step: DiagnosticStep = path.endsWith('apiKeyInfo')
    ? 'apiKeyInfo'
    : path.endsWith('account')
      ? 'account'
      : 'ticker';
  const fail = (
    reason: string,
    status: number | null,
    value?: unknown,
    code: ErrorCode = 'source',
    retryAfter = 0,
  ): never => {
    diagnostic(step, status, value, reason);
    throw new MexcError(code, code === 'limit' ? 429 : 502, retryAfter, reason);
  };
  let response: Response;
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
    return fail('network-or-timeout', null);
  }
  const rate = [418, 429].includes(response.status);
  const retryAfter = rate
    ? Math.min(3600, Math.max(30, Number(response.headers.get('Retry-After')) || 30))
    : 0;
  if (Number(response.headers.get('Content-Length')) > 2 * 1024 * 1024 || !response.body)
    return fail('response-size-or-empty', response.status, undefined, rate ? 'limit' : 'source', retryAfter);
  let parsed: unknown;
  try {
    const reader = response.body.getReader(),
      chunks: Uint8Array[] = [];
    let size = 0;
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
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.length;
    }
    parsed = JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    return fail(
      rate ? 'upstream-rate-limit' : 'invalid-json-or-stream',
      response.status,
      undefined,
      rate ? 'limit' : 'source',
      retryAfter,
    );
  }
  const row =
    parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : {};
  if (rate) return fail('upstream-rate-limit', response.status, parsed, 'limit', retryAfter);
  if (!response.ok || ('code' in row && row.code !== 0)) {
    const code = typeof row.code === 'number' ? row.code : undefined;
    const invalid =
      !!auth &&
      (response.status === 401 ||
        [401, 602, 10072, 700001, 700002, 730706, -2014, -2015, -1022].includes(code ?? 0));
    return fail(
      'code' in row ? 'upstream-code' : 'upstream-http',
      response.status,
      parsed,
      invalid ? 'credentials' : 'source',
    );
  }
  // The endpoint docs show bare objects. Also accept an explicit numeric-zero
  // success envelope; the same strict endpoint parsers still validate its data.
  return {
    value: 'code' in row && row.code === 0 && 'data' in row ? row.data : parsed,
    status: response.status,
    diagnosticValue: parsed,
  };
}

export function quotesFrom(
  value: unknown,
  balances: MexcBalance[],
  now: number,
  status: number | null = null,
  diagnosticValue: unknown = value,
): Record<string, MexcQuote> {
  try {
    return parseQuotes(value, balances, now);
  } catch (error) {
    return parseFailure('parse-quotes', diagnosticValue, status, error);
  }
}
function parseQuotes(value: unknown, balances: MexcBalance[], now: number): Record<string, MexcQuote> {
  if (!Array.isArray(value) || value.length > 20000) sourceFailure('ticker-shape');
  const quotes: Record<string, MexcQuote> = {},
    wanted = new Set(balances.map((b) => b.asset + 'USDT'));
  for (const raw of value) {
    const item = holdingsObject(raw);
    if (typeof item.symbol !== 'string' || !wanted.has(item.symbol)) continue;
    const symbol = item.symbol.slice(0, -4) + '/USDT';
    if (symbol in quotes) sourceFailure('duplicate-quote');
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
  const accountId =
    operation === 'connect' ? validateHoldingAccount(body.account).id : holdingsId(body.accountId);
  if (operation === 'disconnect') {
    const row = await database(db, owner, 'disconnect', { accountId });
    return {
      ...publicState(row),
      document: validateHoldingsDocument(row.document),
    };
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
    return {
      ...publicState(row),
      document: validateHoldingsDocument(row.document),
    };
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
    const info = await readMexc('/api/v3/apiKeyInfo', fetcher, controller.signal, now(), auth);
    assertReadOnlyKey(info.value, auth.apiKey, info.status, info.diagnosticValue);
    const accountResponse = await readMexc('/api/v3/account', fetcher, controller.signal, now(), auth);
    const { balances, skippedAssets, skippedAssetNames } = parseMexcBalances(
      accountResponse.value,
      accountResponse.status,
      accountResponse.diagnosticValue,
    );
    const ticker = await readMexc('/api/v3/ticker/price', fetcher, controller.signal, now());
    const quotes = quotesFrom(ticker.value, balances, now(), ticker.status, ticker.diagnosticValue);
    // Optional reads only start after every spot source has been validated.
    // Keep the final authorization/revision check after all upstream I/O.
    const permitted = String(holdingsObject(info.value).permissions).split(',').map(p => p.trim())
      .includes('CONTRACT_ACCOUNT_READ');
    const futures = await readMexcFutures(auth, permitted, fetcher, controller.signal, now, diagnostic);
    const envelope =
      operation === 'connect' ? await encryptMexcCredentials(auth, key, owner, accountId) : undefined;
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
    return {
      ...publicState(row),
      document: validateHoldingsDocument(row.document),
      skippedAssets,
      skippedAssetNames,
      futures,
    };
  } catch (error) {
    // No raw network errors, signed URLs, credentials, or balances enter logs.
    if (error instanceof MexcError && error.retryAfter) {
      try {
        await database(db, owner, 'backoff', {
          requestId,
          seconds: error.retryAfter,
        });
      } catch {
        /* Fail closed; do not retry upstream. */
      }
    }
    if (error instanceof MexcError) throw error;
    diagnostic('account', null, undefined, 'unexpected-internal-response');
    throw new MexcError('source', 502);
  } finally {
    clearTimeout(timeout);
  }
}
function publicState(row: Record<string, unknown>) {
  return validateMexcState({
    connections: row.connections,
    retryAt: row.retryAt,
    configured: true,
  });
}
