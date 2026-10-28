/** Shared, data-only holdings contract. Errors deliberately never interpolate personal input. */
export const HOLDINGS_LIMITS = { accounts: 30, total: 2000, perAccount: 1000 } as const;
export type HoldingMarket = 'TW' | 'US' | 'CRYPTO';
export type HoldingCurrency = 'TWD' | 'USD' | 'USDT';
export type AccountKind = 'tw-broker' | 'us-broker' | 'crypto-exchange';
export type HoldingSource = 'import' | 'paste' | 'manual' | 'api';
export type Broker = 'cathay' | 'sinopac' | 'kgi' | 'pocket' | 'mexc' | 'firstrade' | 'other';
export interface ColumnMapping {
  fingerprint: string;
  symbol: number;
  quantity: number;
  average?: number;
  total?: number;
  unit: 'shares' | 'lots';
}
export interface HoldingAccount {
  id: string;
  name: string;
  kind: AccountKind;
  currency: HoldingCurrency;
  broker: Broker;
  mapping?: ColumnMapping;
}
export interface Holding {
  id: string;
  accountId: string;
  symbol: string;
  market: HoldingMarket;
  quantity: string;
  averageCost: string;
  costCurrency: HoldingCurrency;
  source: HoldingSource;
  updatedAt: string;
}
export interface HoldingAccountState {
  account: HoldingAccount;
  holdings: Holding[];
  revision: number;
}
export interface HoldingsDocument {
  version: 1;
  accounts: HoldingAccountState[];
}
export class HoldingsError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}
function fail(): never {
  throw new HoldingsError(400, '持股資料格式不正確，請檢查欄位與數量。');
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export function holdingsId(value: unknown): string {
  if (typeof value !== 'string' || !uuid.test(value)) fail();
  return value.toLowerCase();
}
export function holdingsObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail();
  return value as Record<string, unknown>;
}
function keys(row: Record<string, unknown>, allowed: string[]) {
  if (Object.keys(row).some((key) => !allowed.includes(key))) fail();
}
function choice<T extends string>(value: unknown, allowed: readonly T[]): T {
  if (!allowed.includes(value as T)) fail();
  return value as T;
}
export function holdingDecimal(value: unknown, zero = false): string {
  if (
    typeof value !== 'string' ||
    !/^(?:0|[1-9]\d{0,14})(?:\.\d{1,18})?$/.test(value) ||
    (!zero && !/[1-9]/.test(value))
  )
    fail();
  return value as string;
}
export function accountMarket(account: HoldingAccount): HoldingMarket {
  return account.kind === 'tw-broker' ? 'TW' : account.kind === 'us-broker' ? 'US' : 'CRYPTO';
}
export function validateHoldingAccount(value: unknown): HoldingAccount {
  const row = holdingsObject(value);
  keys(row, ['id', 'name', 'kind', 'currency', 'broker', 'mapping']);
  const id = holdingsId(row.id);
  if (typeof row.name !== 'string' || !row.name.trim() || row.name.length > 40 || /[\x00-\x1f\x7f]/.test(row.name))
    fail();
  const kind = choice(row.kind, ['tw-broker', 'us-broker', 'crypto-exchange'] as const);
  const currency = choice(row.currency, ['TWD', 'USD', 'USDT'] as const);
  if (currency !== ({ 'tw-broker': 'TWD', 'us-broker': 'USD', 'crypto-exchange': 'USDT' } as const)[kind]) fail();
  const broker = choice(row.broker, ['cathay', 'sinopac', 'kgi', 'pocket', 'mexc', 'firstrade', 'other'] as const);
  let mapping: ColumnMapping | undefined;
  if (row.mapping !== undefined) {
    const m = holdingsObject(row.mapping);
    keys(m, ['fingerprint', 'symbol', 'quantity', 'average', 'total', 'unit']);
    if (typeof m.fingerprint !== 'string' || !/^[a-f0-9]{64}$/.test(m.fingerprint)) fail();
    for (const key of ['symbol', 'quantity', 'average', 'total'])
      if (m[key] !== undefined && (!Number.isInteger(m[key]) || Number(m[key]) < 0 || Number(m[key]) > 127)) fail();
    if (m.symbol === undefined || m.quantity === undefined || (m.average === undefined && m.total === undefined))
      fail();
    const columns = [m.symbol, m.quantity, m.average, m.total].filter((v) => v !== undefined);
    if (new Set(columns).size !== columns.length) fail();
    mapping = {
      fingerprint: m.fingerprint,
      symbol: Number(m.symbol),
      quantity: Number(m.quantity),
      ...(m.average === undefined ? {} : { average: Number(m.average) }),
      ...(m.total === undefined ? {} : { total: Number(m.total) }),
      unit: choice(m.unit, ['shares', 'lots'] as const),
    };
  }
  return { id, name: row.name.trim(), kind, currency, broker, ...(mapping ? { mapping } : {}) };
}
export function validateHolding(value: unknown, account?: HoldingAccount): Holding {
  const row = holdingsObject(value);
  keys(row, ['id', 'accountId', 'symbol', 'market', 'quantity', 'averageCost', 'costCurrency', 'source', 'updatedAt']);
  const id = holdingsId(row.id),
    accountId = holdingsId(row.accountId);
  const market = choice(row.market, ['TW', 'US', 'CRYPTO'] as const);
  const currency = choice(row.costCurrency, ['TWD', 'USD', 'USDT'] as const);
  if (
    typeof row.symbol !== 'string' ||
    !(
      market === 'TW'
        ? /^\d{4,6}[A-Z]?\.(TW|TWO)$/
        : market === 'US'
          ? /^[A-Z][A-Z0-9.-]{0,19}$/
          : /^[A-Z0-9]{1,20}\/USDT$/
    ).test(row.symbol)
  )
    fail();
  if (currency !== ({ TW: 'TWD', US: 'USD', CRYPTO: 'USDT' } as const)[market]) fail();
  if (account && (account.id !== accountId || accountMarket(account) !== market || account.currency !== currency))
    fail();
  if (
    typeof row.updatedAt !== 'string' ||
    !/^\d{4}-\d{2}-\d{2}T/.test(row.updatedAt) ||
    !Number.isFinite(Date.parse(row.updatedAt))
  )
    fail();
  return {
    id,
    accountId,
    symbol: row.symbol,
    market,
    quantity: holdingDecimal(row.quantity),
    averageCost: holdingDecimal(row.averageCost, true),
    costCurrency: currency,
    source: choice(row.source, ['import', 'paste', 'manual', 'api'] as const),
    updatedAt: row.updatedAt,
  };
}
export function validateAccountState(value: unknown): HoldingAccountState {
  const row = holdingsObject(value),
    account = validateHoldingAccount(row.account);
  if (
    !Array.isArray(row.holdings) ||
    row.holdings.length > HOLDINGS_LIMITS.perAccount ||
    !Number.isSafeInteger(row.revision) ||
    Number(row.revision) < 0
  )
    fail();
  const holdings = (row.holdings as unknown[]).map((item) => validateHolding(item, account));
  if (
    new Set(holdings.map((item) => item.symbol)).size !== holdings.length ||
    new Set(holdings.map((item) => item.id)).size !== holdings.length
  )
    fail();
  return { account, holdings, revision: Number(row.revision) };
}
export function validateHoldingsDocument(value: unknown): HoldingsDocument {
  const row = holdingsObject(value);
  if (row.version !== 1 || !Array.isArray(row.accounts) || row.accounts.length > HOLDINGS_LIMITS.accounts) fail();
  const accounts = (row.accounts as unknown[]).map(validateAccountState);
  if (
    new Set(accounts.map((entry) => entry.account.id)).size !== accounts.length ||
    accounts.reduce((sum, a) => sum + a.holdings.length, 0) > HOLDINGS_LIMITS.total
  )
    fail();
  return { version: 1, accounts };
}
export function holdingsRevision(value: unknown): number | null {
  if (value === null) return null;
  if (!Number.isSafeInteger(value) || Number(value) < 0) fail();
  return Number(value);
}

interface HoldingsDatabase {
  rpc(name: string, params: Record<string, unknown>): PromiseLike<{ data: unknown; error: { code?: string } | null }>;
}
/** Call only after requireCloudStorage. Ownership always comes from the verified session. */
export async function handleHoldings(db: HoldingsDatabase, owner: string, body: Record<string, unknown>) {
  const operation = String(body.action).slice('holdings.'.length);
  let payload: Record<string, unknown> = {},
    expected: number | null = null;
  if (operation === 'replaceAccount') {
    const account = validateHoldingAccount(body.account);
    const entry = validateAccountState({ account, holdings: body.holdings, revision: 0 });
    payload = { account: entry.account, holdings: entry.holdings };
    expected = holdingsRevision(body.expectedRevision);
  } else if (operation === 'upsert') {
    const holding = validateHolding(body.holding);
    payload = { accountId: holding.accountId, holding };
    expected = holdingsRevision(body.expectedRevision);
    if (expected === null) fail();
  } else if (operation === 'delete' || operation === 'deleteAccount') {
    payload = {
      accountId: holdingsId(body.accountId),
      ...(operation === 'delete' ? { holdingId: holdingsId(body.holdingId) } : {}),
    };
    expected = holdingsRevision(body.expectedRevision);
    if (expected === null) fail();
  } else if (operation !== 'list') fail();
  let result: { data: unknown; error: { code?: string } | null };
  try {
    result = await db.rpc('atlas_holdings_action', {
      operation,
      owner_id: owner,
      payload,
      expected_revision: expected,
    });
  } catch {
    throw new HoldingsError(503, '無法確認持股操作結果，請重新載入持股。');
  }
  if (result.error) {
    if (['PGRST202', 'PGRST205', '42883', '42P01'].includes(result.error.code ?? ''))
      throw new HoldingsError(503, '雲端持股尚未啟用，請由維護者完成持股設定。');
    throw new HoldingsError(503, '持股服務暫時無法使用，請稍後重試。');
  }
  const data = holdingsObject(result.data);
  if (data.status === 'conflict') throw new HoldingsError(409, '持股已在其他裝置更新，請重新載入後再操作。');
  if (data.status === 'limit') throw new HoldingsError(400, '持股或帳戶數量已達上限。');
  if (data.status !== 'ok') throw new HoldingsError(400, '持股操作無法完成，請重新載入後再試。');
  return validateHoldingsDocument(data.document);
}
