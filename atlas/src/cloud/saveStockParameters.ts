import { getStorageContextKey } from '../auth/storage';
import { settingsApi, type ParameterStock } from './settingsApi';
import { FIXED_STOCK_GROUP, parameterMAs, stockParameterRows } from './stockParameters';
import { stockParametersChanged } from './useStockParameters';

const STALE = '原有參數已變更，請重新載入後再編輯。';
const fields = ['ticker', 'group_id', 'ma1', 'ma2', 'ma3', 'ma4'] as const;
const sameRow = (a: ParameterStock, b: ParameterStock) =>
  a.id === b.id && fields.every(key => a[key] === b[key]);
const sameMas = (a: number[], b: number[]) =>
  a.length === b.length && a.every((value, index) => value === b[index]);

/** The main save succeeded but old normalized-ticker copies still need removal. */
export class StockParameterCleanupError extends Error {
  constructor(
    readonly ticker: string,
    readonly savedId: number,
    readonly mas: number[],
    readonly groupId: string,
    readonly duplicates: ParameterStock[],
    readonly context: string,
  ) {
    super('均線已覆蓋，重複資料尚未清除。請按「重試清除重複資料」。');
    this.name = 'StockParameterCleanupError';
  }
}

/** Retry cleanup only if the just-saved row and every remaining legacy copy are unchanged. */
export async function retryStockParameterCleanup(failure: StockParameterCleanupError) {
  const ensureContext = () => {
    if (failure.context !== getStorageContextKey()) throw new Error('帳號或儲存權限已變更，請重新操作。');
  };
  ensureContext();
  const rows = stockParameterRows(await settingsApi<ParameterStock[]>('stocks.list'), failure.ticker);
  ensureContext();
  const saved = rows.find(row => row.id === failure.savedId);
  if (!saved || saved.group_id !== failure.groupId || !sameMas(parameterMAs(saved), failure.mas))
    throw new Error(STALE);
  for (const duplicate of failure.duplicates) {
    const stillThere = rows.find(row => row.id === duplicate.id);
    if (stillThere && !sameRow(stillThere, duplicate)) throw new Error(STALE);
  }
  try {
    for (const duplicate of failure.duplicates) {
      if (!rows.some(row => row.id === duplicate.id)) continue;
      ensureContext();
      await settingsApi('stocks.delete', { id: duplicate.id });
    }
  } catch {
    ensureContext();
    throw new Error('均線已覆蓋，重複資料尚未清除；清除失敗，請重新載入後重試。');
  } finally {
    stockParametersChanged();
  }
}

/**
 * The only parameter write path for the fixed editor, console and watchlists.
 * Reuse the grouped row's id if present, otherwise the ungrouped row's id,
 * and create one ungrouped row only for a new ticker. No shadow copy is written.
 * A full caller snapshot also protects legacy two-row edits against concurrent changes.
 */
export async function saveStockParameters(
  ticker: string, mas: number[], current?: ParameterStock, groupId?: string,
  expectedRows?: readonly ParameterStock[],
) {
  const context = getStorageContextKey();
  const ensureContext = () => {
    if (context !== getStorageContextKey()) throw new Error('帳號或儲存權限已變更，請重新操作。');
  };
  const rows = stockParameterRows(await settingsApi<ParameterStock[]>('stocks.list'), ticker);
  ensureContext();
  if (expectedRows) {
    const expected = stockParameterRows(expectedRows, ticker);
    if (expected.length !== rows.length || expected.some((row, i) => !sameRow(row, rows[i])))
      throw new Error(STALE);
  }
  if (current && !rows.some(row => sameRow(row, current))) throw new Error(STALE);
  if (!current && rows.length) throw new Error('此股票已有參數，請重新載入並確認原有參數後再編輯。');

  const grouped = rows.find(row => row.group_id !== FIXED_STOCK_GROUP);
  const target = grouped ?? rows[0];
  if (current && target && current.id !== target.id && !expectedRows) throw new Error(STALE);
  const destination = groupId ?? target?.group_id ?? FIXED_STOCK_GROUP;
  // An existing dormant copy can violate the DB's (user,ticker,group) unique key on a move.
  // Never delete that copy before the primary save: require explicit consolidation first.
  if (grouped && destination === FIXED_STOCK_GROUP && rows.some(row => row.group_id === FIXED_STOCK_GROUP))
    throw new Error('此股票仍有舊的無分組重複列，請先編輯並清除重複資料，再移出分組。');

  ensureContext();
  const saved = await settingsApi<{ id: number }[]>('stocks.save', {
    ...(target ? { id: target.id } : {}), ticker: target?.ticker ?? ticker,
    groupId: destination, mas,
  });
  stockParametersChanged();
  const duplicates = rows.filter(row => row.id !== target?.id && row.group_id === FIXED_STOCK_GROUP);
  try {
    for (const duplicate of duplicates) {
      ensureContext();
      await settingsApi('stocks.delete', { id: duplicate.id });
    }
  } catch {
    ensureContext();
    throw new StockParameterCleanupError(ticker, target?.id ?? saved[0].id, mas, destination, duplicates, context);
  } finally {
    if (duplicates.length) stockParametersChanged();
  }
}
