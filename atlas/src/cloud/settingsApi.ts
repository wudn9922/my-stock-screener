import { startLineIdentity } from './lineIdentity';

export const SETTINGS_API_URL = 'https://bxhqpfeberqbtxymghyt.supabase.co/functions/v1/user-settings';
export interface ParameterGroup { id: number; name: string; ma1: number; ma2: number; ma3: number; ma4: number }
export interface ParameterStock { id: number; ticker: string; group_id: string; ma1: number; ma2: number; ma3: number; ma4: number }
export interface IndexParameter { ticker: string; name: string; ma1: number; ma2: number; ma3: number; ma4: number }
export const FIXED_PARAMETER_GROUPS = { tw_g1: '台股權值', tw_g2: '台股熱門', us_g1: '美股權值', us_g2: '低本益比', us_g3: '超級績效', us_g4: '美股熱門' };

export function parseMovingAverages(value: string): number[] {
  const parts = value.trim().split(/[\s,，]+/).filter(Boolean);
  const numbers = parts.map(Number);
  if (!numbers.length || numbers.length > 4 || numbers.some((n) => !Number.isInteger(n) || n < 1 || n > 1000) || new Set(numbers).size !== numbers.length)
    throw new Error('均線請填 1～4 個不重複的整數（1～1000），以逗號分隔。');
  return numbers;
}

export async function settingsApi<T>(action: string, payload: Record<string, unknown> = {}, signal?: AbortSignal): Promise<T> {
  const identity = await startLineIdentity();
  const idToken = identity?.idToken();
  if (!idToken) throw new Error('請從 LINE 開啟 Atlas 並登入，以存取原有雲端參數；本機清單仍可使用。');
  const response = await fetch(import.meta.env.VITE_SETTINGS_API_URL || SETTINGS_API_URL, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, action, idToken }), signal: signal ?? AbortSignal.timeout(15000),
  });
  const result = await response.json() as { ok?: boolean; data?: T; error?: string };
  if (!response.ok || !result.ok) throw new Error(response.status === 401 ? 'LINE 登入已過期，請重新開啟 Atlas。' : result.error || '雲端設定讀寫失敗。');
  return result.data as T;
}
