import { defaultApp } from './IndexedDBStore';
import { exportSchema, parseSettings, type SettingsExport } from './schema';

export const LEGACY_SETTINGS_DATABASE = 'atlas-terminal-screener';

/** Read the original database without upgrading it, creating stores, or writing records. */
export async function readLegacySettings(): Promise<SettingsExport | null> {
  if (!globalThis.indexedDB) return null;
  if (typeof indexedDB.databases === 'function') {
    const databases = await indexedDB.databases();
    if (!databases.some(database => database.name === LEGACY_SETTINGS_DATABASE)) return null;
  }
  const db = await new Promise<IDBDatabase | null>((resolve, reject) => {
    let absent = false;
    const request = indexedDB.open(LEGACY_SETTINGS_DATABASE);
    // On older browsers without databases(), opening an absent database requests creation.
    // Abort before any store exists: no new database or version change is committed.
    request.onupgradeneeded = () => { absent = true; request.transaction?.abort(); };
    request.onerror = () => absent ? resolve(null) : reject(request.error ?? new Error('無法讀取舊版資料。'));
    request.onblocked = () => reject(new Error('舊版資料正由另一個分頁使用，請關閉該分頁後重試。'));
    request.onsuccess = () => resolve(request.result);
  });
  if (!db) return null;
  try {
    if (db.version < 1 || db.version > 4 || !db.objectStoreNames.contains('app') || !db.objectStoreNames.contains('symbols')) {
      throw new Error('舊版資料格式無法辨識；原始資料未變更。');
    }
    const version = db.version;
    const records = await new Promise<{ app: unknown; symbols: unknown[] }>((resolve, reject) => {
      const tx = db.transaction(['app', 'symbols'], 'readonly');
      const app = tx.objectStore('app').get('settings');
      const symbols = tx.objectStore('symbols').getAll();
      tx.oncomplete = () => resolve({ app: app.result, symbols: symbols.result });
      tx.onerror = () => reject(tx.error ?? new Error('無法讀取舊版資料。'));
      tx.onabort = () => reject(tx.error ?? new Error('舊版資料讀取已取消。'));
    });
    if (!records.app && !records.symbols.length) return null;
    // parseSettings is also used by the normal settings import. Legacy ownership and
    // alert migrations happen in memory, never through an IDB versionchange transaction.
    return parseSettings(JSON.stringify({
      version, exportedAt: 'legacy-browser', app: records.app ?? defaultApp, symbols: records.symbols,
    }));
  } finally {
    db.close();
  }
}

export function countLegacySettings(data: SettingsExport) {
  return {
    drawings: data.symbols.reduce((count, symbol) => count + symbol.drawings.length, 0),
    indicators: data.symbols.reduce((count, symbol) => count + symbol.indicators.length, 0),
    lists: data.app.watchlist.length ? 1 : 0,
    settings: data.symbols.length + 1,
    alerts: data.app.alerts.length,
    presets: data.app.indicatorPresets.length,
  };
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, entry]) => `${JSON.stringify(key)}:${canonical(entry)}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

/** Account values win. Only genuinely absent fields or identified records are added. */
export function mergeLegacySettings(
  currentData: SettingsExport,
  legacyData: SettingsExport,
  options: { appExists?: boolean } = {},
): { data: SettingsExport; added: number; conflicts: number } {
  const current = exportSchema.parse(currentData);
  const legacy = exportSchema.parse(legacyData);
  let added = 0, conflicts = 0;
  function keep<T>(account: T, old: T): T {
    if (canonical(account) !== canonical(old)) conflicts++;
    return structuredClone(account);
  }
  function records<T>(account: T[], old: T[], key: (item: T) => string): T[] {
    const result = structuredClone(account), existing = new Map(account.map(item => [key(item), item]));
    for (const item of old) {
      const match = existing.get(key(item));
      if (match !== undefined) keep(match, item);
      else { result.push(structuredClone(item)); existing.set(key(item), item); added++; }
    }
    return result;
  }
  function fields<T extends object>(account: T, old: T, handled: string[] = []): T {
    const result = structuredClone(account);
    for (const [key, value] of Object.entries(old)) {
      if (handled.includes(key)) continue;
      if (!Object.hasOwn(account, key)) { Object.assign(result, { [key]: structuredClone(value) }); added++; }
      else keep(Reflect.get(account, key), value);
    }
    return result;
  }
  const symbols = structuredClone(current.symbols);
  for (const old of legacy.symbols) {
    const index = symbols.findIndex(symbol => symbol.symbol === old.symbol);
    if (index === -1) {
      symbols.push(structuredClone(old));
      added += old.drawings.length + old.indicators.length + 1;
      continue;
    }
    const account = symbols[index];
    const preferences = fields(account.preferences, old.preferences, ['views', 'volumeOverrides']);
    // Views and volume overrides have independently owned timeframe keys.
    preferences.views = fields(account.preferences.views, old.preferences.views);
    if (account.preferences.volumeOverrides && old.preferences.volumeOverrides) {
      preferences.volumeOverrides = fields(account.preferences.volumeOverrides, old.preferences.volumeOverrides);
    } else if (!account.preferences.volumeOverrides && old.preferences.volumeOverrides) {
      preferences.volumeOverrides = structuredClone(old.preferences.volumeOverrides);
      added++;
    }
    symbols[index] = {
      ...account,
      drawings: records(account.drawings, old.drawings, item => item.id),
      indicators: records(account.indicators, old.indicators, item => item.id),
      preferences,
    };
  }
  let app = structuredClone(current.app);
  const compatibleAlerts = legacy.app.alerts.filter(alert => {
    if (alert.kind !== 'drawing' || !alert.enabled) return true;
    const drawing = symbols.find(symbol => symbol.symbol === alert.symbol)?.drawings.find(item => item.id === alert.drawingId);
    const compatible = !!drawing && (drawing.type === 'horizontal' || drawing.type === 'ray') && drawing.scope.timeframes.includes(alert.timeframe);
    if (!compatible) conflicts++;
    return compatible;
  });
  if (options.appExists === false) {
    app = { ...structuredClone(legacy.app), alerts: compatibleAlerts };
    added += 1 + legacy.app.watchlist.length + compatibleAlerts.length + legacy.app.indicatorPresets.length;
  } else {
    app = fields(current.app, legacy.app, ['watchlist', 'alerts', 'indicatorPresets', 'drawingDefaults']);
    app.watchlist = records(current.app.watchlist, legacy.app.watchlist, symbol => symbol);
    app.alerts = records(current.app.alerts, compatibleAlerts, alert => alert.id);
    app.indicatorPresets = records(current.app.indicatorPresets, legacy.app.indicatorPresets, preset => preset.id);
    app.drawingDefaults = fields(current.app.drawingDefaults, legacy.app.drawingDefaults);
  }
  // Validate the complete result, including collection limits and alert references, before
  // anything is published. A conflicting drawing cannot silently orphan a legacy alert.
  const data = exportSchema.parse({ ...current, app, symbols });
  return { data, added, conflicts };
}
