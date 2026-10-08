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
import { storageName } from '../app/HostingMode';
interface AtlasDB extends DBSchema {
  symbols: { key: string; value: SymbolState };
  app: { key: string; value: AppSettings };
}
export const defaultApp: AppSettings = appSchema.parse({
  watchlist: ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMD', 'META', 'GOOGL', 'AMZN'],
  activeSymbol: 'AAPL',
  provider: 'demo',
});
export class IndexedDBStore {
  private db: Promise<IDBPDatabase<AtlasDB>>;
  constructor(name = storageName('atlas-terminal')) {
    this.db = openDB<AtlasDB>(name, 4, {
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
  async load() {
    const db = await this.db;
    const [app, symbols] = await Promise.all([db.get('app', 'settings'), db.getAll('symbols')]);
    return {
      app: app ? appSchema.parse(app) : structuredClone(defaultApp),
      symbols: symbols.map((s) => symbolStateSchema.parse(s)),
    };
  }
  async saveSymbol(s: SymbolState) {
    await (await this.db).put('symbols', symbolStateSchema.parse(s));
  }
  async saveApp(s: AppSettings) {
    await (await this.db).put('app', appSchema.parse(s), 'settings');
  }
  async export(): Promise<SettingsExport> {
    const { app, symbols } = await this.load();
    return exportSchema.parse({ version: 4, exportedAt: new Date().toISOString(), app, symbols });
  }
  async import(data: SettingsExport) {
    const validated = exportSchema.parse(data),
      db = await this.db;
    const tx = db.transaction(['symbols', 'app'], 'readwrite');
    // A request failure can exit before the final await; observe the abort rejection.
    void tx.done.catch(() => undefined);
    await tx.objectStore('symbols').clear();
    for (const s of validated.symbols) await tx.objectStore('symbols').put(s);
    await tx.objectStore('app').put(validated.app, 'settings');
    await tx.done;
  }
  async close() {
    (await this.db).close();
  }
}
