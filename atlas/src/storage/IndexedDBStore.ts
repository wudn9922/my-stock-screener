import { openDB, type DBSchema, type IDBPDatabase } from 'idb';
import {
  appSchema,
  symbolStateSchema,
  exportSchema,
  upgradeLegacySettings,
  upgradeV3Settings,
  type SymbolState,
  type AppSettings,
  type SettingsExport,
} from './schema';
import { SCREENER_HOSTING } from '../app/HostingMode';
import { getStorageContextKey, persistentDatabaseName, canPersist } from '../auth/storage';
interface AtlasDB extends DBSchema {
  symbols: { key: string; value: SymbolState };
  app: { key: string; value: AppSettings };
}
// The screener site starts on real delayed data for Taiwan + US names; other builds keep Demo.
export const defaultApp: AppSettings = appSchema.parse(
  SCREENER_HOSTING
    ? {
        watchlist: ['2330.TW', '2317.TW', '2454.TW', 'NVDA', 'AAPL', 'MSFT', 'TSLA', 'GOOGL'],
        activeSymbol: '2330.TW',
        provider: 'market',
      }
    : {
        watchlist: ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMD', 'META', 'GOOGL', 'AMZN'],
        activeSymbol: 'AAPL',
        provider: 'demo',
      },
);
export class IndexedDBStore {
  private db: Promise<IDBPDatabase<AtlasDB>> | null = null;
  private memory = { app: structuredClone(defaultApp), symbols: [] as SymbolState[] };
  private readonly context = getStorageContextKey();
  private readonly guarded: boolean;
  constructor(name?: string) {
    this.guarded = name === undefined && SCREENER_HOSTING;
    const database = name ?? persistentDatabaseName('atlas-terminal');
    if (!database) return;
    this.db = openDB<AtlasDB>(database, 4, {
      async upgrade(db, oldVersion, _newVersion, tx) {
        if (oldVersion < 1) {
          db.createObjectStore('symbols', { keyPath: 'symbol' });
          db.createObjectStore('app');
        }
        if (oldVersion === 1 || oldVersion === 2) {
          // idb exposes a separate completion promise even during versionchange.
          // Observe its rejection while the open request still reports migration failure.
          void tx.done.catch(() => undefined);
          try {
            const app = await tx.objectStore('app').get('settings') ?? structuredClone(defaultApp);
            const symbols = await tx.objectStore('symbols').getAll();
            const migrated = upgradeLegacySettings({
              exportedAt: new Date().toISOString(), app, symbols,
            }, oldVersion === 1 ? 1 : 2);
            for (const state of migrated.symbols) await tx.objectStore('symbols').put(state);
            await tx.objectStore('app').put(migrated.app, 'settings');
          } catch {
            tx.abort();
          }
        }
        if (oldVersion === 3) {
          void tx.done.catch(() => undefined);
          try {
            const app = await tx.objectStore('app').get('settings') ?? structuredClone(defaultApp);
            const symbols = await tx.objectStore('symbols').getAll();
            const migrated = upgradeV3Settings({
              version: 3, exportedAt: new Date().toISOString(), app, symbols,
            });
            for (const state of migrated.symbols) await tx.objectStore('symbols').put(state);
            await tx.objectStore('app').put(migrated.app, 'settings');
          } catch {
            tx.abort();
          }
        }
      },
    });
  }
  private assertCurrent() {
    if (this.guarded && this.context !== getStorageContextKey()) throw new Error('帳號或儲存授權已變更，舊操作已取消。');
    if (this.guarded && this.db && !canPersist()) throw new Error('登入已過期，目前操作不會保存。');
  }
  async load() {
    this.assertCurrent();
    if (!this.db) return structuredClone(this.memory);
    const db = await this.db;
    const [app, symbols] = await Promise.all([db.get('app', 'settings'), db.getAll('symbols')]);
    this.assertCurrent();
    return {
      app: app ? appSchema.parse(app) : structuredClone(defaultApp),
      symbols: symbols.map((s) => symbolStateSchema.parse(s)),
    };
  }
  async saveSymbol(s: SymbolState) {
    this.assertCurrent();
    const parsed = symbolStateSchema.parse(s);
    if (!this.db) {
      this.memory.symbols = [...this.memory.symbols.filter((row) => row.symbol !== parsed.symbol), structuredClone(parsed)];
      return;
    }
    const db = await this.db;
    this.assertCurrent();
    await db.put('symbols', parsed);
  }
  async saveApp(s: AppSettings) {
    this.assertCurrent();
    const parsed = appSchema.parse(s);
    if (!this.db) { this.memory.app = structuredClone(parsed); return; }
    const db = await this.db;
    this.assertCurrent();
    await db.put('app', parsed, 'settings');
  }
  async export(): Promise<SettingsExport> {
    const { app, symbols } = await this.load();
    return exportSchema.parse({ version: 4, exportedAt: new Date().toISOString(), app, symbols });
  }
  async import(data: SettingsExport) {
    this.assertCurrent();
    const validated = exportSchema.parse(data);
    if (!this.db) { this.memory = structuredClone({ app: validated.app, symbols: validated.symbols }); return; }
    const db = await this.db;
    this.assertCurrent();
    const tx = db.transaction(['symbols', 'app'], 'readwrite');
    // A request failure can exit before the final await; observe the abort rejection.
    void tx.done.catch(() => undefined);
    await tx.objectStore('symbols').clear();
    for (const s of validated.symbols) await tx.objectStore('symbols').put(s);
    await tx.objectStore('app').put(validated.app, 'settings');
    await tx.done;
  }
  async close() {
    (await this.db)?.close();
  }
}
