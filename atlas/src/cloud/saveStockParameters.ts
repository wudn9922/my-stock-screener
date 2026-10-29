import { getStorageContextKey } from '../auth/storage';
import { settingsApi, type ParameterStock } from './settingsApi';
import { FIXED_STOCK_GROUP, stockParameterRows } from './stockParameters';
import { stockParametersChanged } from './useStockParameters';

/** Reuse the original row, and remove legacy dormant duplicates only after a confirmed successful save. */
export async function saveStockParameters(ticker: string, mas: number[], current?: ParameterStock, groupId?: string) {
  const context = getStorageContextKey();
  const stocks = await settingsApi<ParameterStock[]>('stocks.list');
  const rows = stockParameterRows(stocks, ticker);
  const target = current ? rows.find(row => row.id === current.id) : rows[0];
  if (current && !target) throw new Error('原有參數已變更，請重新載入後再編輯。');
  if (current && target && (['ticker', 'group_id', 'ma1', 'ma2', 'ma3', 'ma4'] as const).some(key => target[key] !== current[key]))
    throw new Error('原有參數已變更，請重新載入後再編輯。');
  if (!current && target) throw new Error('此股票已有參數，請重新載入並確認原有參數後再編輯。');
  const ensureContext = () => {
    if (context !== getStorageContextKey()) throw new Error('帳號或儲存權限已變更，請重新操作。');
  };
  ensureContext();
  await settingsApi('stocks.save', { ...(target ? { id: target.id } : {}), ticker: target?.ticker ?? ticker,
    groupId: groupId ?? target?.group_id ?? FIXED_STOCK_GROUP, mas });
  try {
    if ((groupId ?? target?.group_id ?? FIXED_STOCK_GROUP) !== FIXED_STOCK_GROUP) {
      for (const duplicate of rows.filter(row => row.group_id === FIXED_STOCK_GROUP && row.id !== target?.id)) {
        ensureContext();
        await settingsApi('stocks.delete', { id: duplicate.id });
      }
    }
  } catch {
    throw new Error('原有分組參數已儲存；無分組重複資料尚未清除，請重新載入後重試。');
  } finally {
    stockParametersChanged();
  }
}
