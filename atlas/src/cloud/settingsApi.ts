import { accessState, initializeAccess, requireCloudAccess, currentIdentity } from '../auth/access';
import { getStorageContextKey, userStorage } from '../auth/storage';
import { LocalParameters } from './localParameters';

export const SETTINGS_API_URL = 'https://bxhqpfeberqbtxymghyt.supabase.co/functions/v1/user-settings';
export interface ParameterGroup {
  id: number;
  name: string;
  ma1: number;
  ma2: number;
  ma3: number;
  ma4: number;
}
export interface ParameterStock {
  id: number;
  ticker: string;
  group_id: string;
  ma1: number;
  ma2: number;
  ma3: number;
  ma4: number;
}
export interface IndexParameter {
  ticker: string;
  name: string;
  ma1: number;
  ma2: number;
  ma3: number;
  ma4: number;
}
/** A dormant per-ticker preference, never a report or monitoring group. */
export const FIXED_STOCK_GROUP = 'ungrouped';
export function parameterMAs(stock: Pick<ParameterStock, 'ma1' | 'ma2' | 'ma3' | 'ma4'>): number[] {
  return [stock.ma1, stock.ma2, stock.ma3, stock.ma4].filter((n) => n > 0);
}
export const FIXED_PARAMETER_GROUPS = {
  tw_g1: '台股權值',
  tw_g2: '台股熱門',
  us_g1: '美股權值',
  us_g2: '低本益比',
  us_g3: '超級績效',
  us_g4: '美股熱門',
};

export function parseMovingAverages(value: string): number[] {
  const parts = value
    .trim()
    .split(/[\s,，]+/)
    .filter(Boolean);
  const numbers = parts.map(Number);
  if (
    !numbers.length ||
    numbers.length > 4 ||
    numbers.some((n) => !Number.isInteger(n) || n < 1 || n > 1000) ||
    new Set(numbers).size !== numbers.length
  )
    throw new Error('均線請填 1～4 個不重複的整數（1～1000），以逗號分隔。');
  return numbers;
}

export async function settingsApi<T>(action: string, payload: Record<string, unknown> = {}, signal?: AbortSignal): Promise<T> {
  const access = await initializeAccess();
  const context = getStorageContextKey();
  if (signal?.aborted) throw signal.reason ?? new DOMException('Aborted', 'AbortError');
  if (/^(groups|stocks)\./.test(action) && access.storageMode !== 'cloud')
    return new LocalParameters(userStorage).call(action, payload) as T;
  const isAdminAction = action.startsWith('admin.');
  if (isAdminAction && !access.isAdmin) throw new Error('此操作僅限管理員。');
  if (isAdminAction && action.startsWith('admin.index.') && access.storageMode !== 'cloud')
    throw new Error('大盤雲端參數需由允許 Supabase 儲存的管理員修改。');
  const identity = isAdminAction ? currentIdentity() : await requireCloudAccess();
  if (identity?.userId !== access.userId || context !== getStorageContextKey()) throw new Error('帳號或儲存權限已變更，請重新操作。');
  const idToken = identity?.idToken();
  if (!idToken) throw new Error('LINE 登入已過期，目前操作不會上傳。請重新開啟 Atlas。');
  const controller = new AbortController();
  const abort = () => controller.abort(signal?.reason);
  signal?.addEventListener('abort', abort, { once: true });
  const unsubscribe = accessState.subscribe(() => {
    if (context !== getStorageContextKey()) controller.abort(new Error('帳號或儲存權限已變更。'));
  });
  const timeout = setTimeout(() => controller.abort(new Error('設定讀寫逾時。')), 15000);
  try {
    const response = await fetch(import.meta.env.VITE_SETTINGS_API_URL || SETTINGS_API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...payload, action, idToken }),
      signal: controller.signal,
    });
    const result = (await response.json()) as {
      ok?: boolean;
      data?: T;
      error?: string;
    };
    if (context !== getStorageContextKey()) throw new Error('帳號或儲存權限已變更，已忽略舊回應。');
    if (!response.ok || !result.ok)
      throw new Error(response.status === 401 ? 'LINE 登入已過期，請重新開啟 Atlas。' : result.error || '雲端設定讀寫失敗。');
    return result.data as T;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', abort);
    unsubscribe();
  }
}

export function parameterStorageMessage(subject = '參數') {
  const access = accessState.getSnapshot();
  return access.storageMode === 'cloud'
    ? `${subject}已儲存到你的 Supabase 帳號。`
    : access.storageMode === 'device'
      ? `${subject}已保存在此裝置的個人帳號空間，不會上傳。`
      : `${subject}已套用，僅本次瀏覽有效；重新載入後清除，不會保存或上傳。`;
}
