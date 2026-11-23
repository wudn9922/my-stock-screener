import { useCallback, useEffect, useRef, useState } from 'react';
import { getStorageContextKey } from '../auth/storage';
import { settingsApi } from '../cloud/settingsApi';
import type { UnifiedList } from './unifiedLists';

/**
 * Per-list server notifications (均線／畫線 突破跌破), stored in `atlas_alert_settings` through user-settings
 * and read once per run by breakout_alert (Discord and Web Push). `all` = every list notifies, the default and
 * today's behaviour. In `custom` a symbol notifies for a type when ANY of its lists enables it (one alert each).
 * Keys are the unified list keys of groups and watchlists; chart favourites are device-only and never notify.
 */
export type AlertKind = 'ma' | 'drawing';
export interface AlertSettings {
  version: 1;
  mode: 'all' | 'custom';
  lists: Record<string, { ma: boolean; drawing: boolean }>;
}
export const DEFAULT_ALERT_SETTINGS: AlertSettings = { version: 1, mode: 'all', lists: {} };
export const ALERT_KIND_LABELS: Record<AlertKind, string> = { ma: '均線突破／跌破', drawing: '畫線突破／跌破' };

export const canNotify = (list: UnifiedList) => list.kind === 'group' || list.kind === 'watchlist' || list.kind === 'rules';

/** What a list sends: everything (groups) or its own monitor switch (watchlist MA) while every list notifies. */
export function listAlertFlags(settings: AlertSettings, list: UnifiedList): { ma: boolean; drawing: boolean } {
  if (!canNotify(list)) return { ma: false, drawing: false };
  if (settings.mode === 'all') return { ma: list.kind === 'group' || !!list.monitored, drawing: true };
  return settings.lists[list.key] ?? { ma: false, drawing: false };
}

/** Switching to custom starts from exactly what is sent today, so nothing changes until a list is edited. */
export function customFromCurrent(settings: AlertSettings, lists: readonly UnifiedList[]): AlertSettings {
  if (settings.mode === 'custom') return settings;
  return {
    version: 1,
    mode: 'custom',
    lists: Object.fromEntries(lists.filter(canNotify).map((list) => [list.key, listAlertFlags(settings, list)])),
  };
}

export function withListAlert(settings: AlertSettings, lists: readonly UnifiedList[], list: UnifiedList, kind: AlertKind, value: boolean): AlertSettings {
  const base = customFromCurrent(settings, lists);
  return { ...base, lists: { ...base.lists, [list.key]: { ...listAlertFlags(base, list), [kind]: value } } };
}

export function withAllLists(settings: AlertSettings, lists: readonly UnifiedList[], all: boolean): AlertSettings {
  return all ? { ...settings, mode: 'all' } : customFromCurrent({ ...settings, mode: 'all' }, lists);
}

/** The alert types this symbol notifies for, from any of its lists. */
export function symbolAlertKinds(settings: AlertSettings, lists: readonly UnifiedList[], members: ReadonlySet<string>): Set<AlertKind> {
  const kinds = new Set<AlertKind>();
  for (const list of lists) {
    if (!members.has(list.key)) continue;
    const flags = listAlertFlags(settings, list);
    if (flags.ma) kinds.add('ma');
    if (flags.drawing) kinds.add('drawing');
  }
  return kinds;
}

/** Cloud accounts only: load once, save each change optimistically and roll back on failure. */
export function useAlertSettings(enabled: boolean) {
  const context = getStorageContextKey();
  const [state, setState] = useState<{ context: string; settings: AlertSettings; error: string | null } | null>(null);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    void settingsApi<{ settings: AlertSettings }>('alerts.settings.get')
      .then((data) => { if (active) setState({ context, settings: data.settings ?? DEFAULT_ALERT_SETTINGS, error: null }); })
      .catch((error) => { if (active) setState({ context, settings: DEFAULT_ALERT_SETTINGS, error: error instanceof Error ? error.message : '通知設定無法載入' }); });
    return () => { active = false; };
  }, [enabled, context]);
  const current = state?.context === context ? state : null;
  const save = useCallback((next: AlertSettings) => {
    const previous = current?.settings ?? DEFAULT_ALERT_SETTINGS;
    setState({ context, settings: next, error: null });
    const task = queue.current.catch(() => undefined).then(() => settingsApi<{ settings: AlertSettings }>('alerts.settings.save', { settings: next }))
      .then(() => undefined, async (error) => {
        // Roll back to what the server holds (a later queued save may already have succeeded).
        const stored = await settingsApi<{ settings: AlertSettings }>('alerts.settings.get').then((data) => data.settings, () => previous);
        if (context === getStorageContextKey())
          setState({ context, settings: stored ?? previous, error: `通知設定未儲存：${error instanceof Error ? error.message : '雲端寫入失敗'}` });
      });
    queue.current = task;
    return task;
  }, [context, current?.settings]);
  return { settings: current?.settings ?? DEFAULT_ALERT_SETTINGS, loaded: !!current, error: current?.error ?? null, save };
}
