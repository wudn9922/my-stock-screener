/**
 * Per-list notification settings (Atlas 我的 › 清單 › 通知). Pure validation shared with offline tests.
 * `mode: "all"` (also the absent default) keeps every current alert; `custom` lists per-list
 * 均線 (ma) / 畫線 (drawing) choices. Keep the key pattern in sync with breakout_alert/list_notifications.py.
 */
export const ALERT_SETTINGS_DEFAULT = { version: 1, mode: 'all', lists: {} } as const;
const LIST_KEY = /^(group:(tw_g[12]|us_g[1-4]|custom_\d{1,18})|watchlist:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/;
const MAX_LISTS = 200;

export type AlertSettings = { version: 1; mode: 'all' | 'custom'; lists: Record<string, { ma: boolean; drawing: boolean }> };

export function validateAlertSettings(value: unknown): AlertSettings {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('通知設定格式不正確');
  const raw = value as Record<string, unknown>;
  if (raw.version !== 1) throw new Error('通知設定版本不正確');
  if (raw.mode !== 'all' && raw.mode !== 'custom') throw new Error('通知模式不正確');
  const input = raw.lists ?? {};
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('清單通知設定格式不正確');
  const entries = Object.entries(input as Record<string, unknown>);
  if (entries.length > MAX_LISTS) throw new Error(`最多設定 ${MAX_LISTS} 份清單`);
  const lists: AlertSettings['lists'] = {};
  for (const [rawKey, flags] of entries) {
    const key = rawKey.startsWith('watchlist:') ? rawKey.toLowerCase() : rawKey;
    if (!LIST_KEY.test(key)) throw new Error('清單代號不正確');
    if (!flags || typeof flags !== 'object' || Array.isArray(flags)) throw new Error('清單通知設定格式不正確');
    const { ma = false, drawing = false } = flags as Record<string, unknown>;
    if (typeof ma !== 'boolean' || typeof drawing !== 'boolean') throw new Error('通知選項需為是／否');
    lists[key] = { ma, drawing };
  }
  return { version: 1, mode: raw.mode, lists };
}
