import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import { openDB } from 'idb';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppStore } from '../src/app/AppStore';
import { IndexedDBStore, defaultApp } from '../src/storage/IndexedDBStore';
import { emptySymbol, exportSchema, type SettingsExport } from '../src/storage/schema';
import { LEGACY_SETTINGS_DATABASE, countLegacySettings, mergeLegacySettings, readLegacySettings } from '../src/storage/legacySettings';

beforeEach(() => { vi.stubGlobal('indexedDB', new IDBFactory()); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function settings(): SettingsExport {
  const symbol = emptySymbol('AAPL');
  symbol.drawings = [{
    id: 'old-drawing', symbol: 'AAPL', type: 'horizontal',
    points: [{ time: 1_700_000_000, logical: 100, price: 100, timeframe: '1D' }],
    locked: true, visible: true, scope: { timeframes: ['1D'] },
    style: { color: '#112233', lineWidth: 2 },
  }];
  symbol.indicators = [{
    id: 'old-indicator', symbol: 'AAPL', type: 'SMA', period: 24, source: 'close',
    locked: true, visible: true, scope: { timeframe: '1D' }, color: '#123456', lineWidth: 2,
  }];
  return exportSchema.parse({
    version: 4, exportedAt: 'fixture', app: { ...defaultApp, activeSymbol: 'AAPL', watchlist: ['AAPL'] }, symbols: [symbol],
  });
}

async function seedLegacy(version: number, app: unknown, symbols: unknown[]) {
  const db = await openDB(LEGACY_SETTINGS_DATABASE, version, { upgrade(database) {
    database.createObjectStore('app'); database.createObjectStore('symbols', { keyPath: 'symbol' });
  } });
  await db.put('app', app, 'settings');
  for (const symbol of symbols) await db.put('symbols', symbol);
  db.close();
}

describe('read-only legacy chart inventory', () => {
  it('does not create a database when the legacy database is absent', async () => {
    const open = vi.spyOn(indexedDB, 'open');
    expect(await readLegacySettings()).toBeNull();
    expect(open).not.toHaveBeenCalled();
    expect(await indexedDB.databases()).toEqual([]);
  });

  it('aborts absent database creation on browsers without database enumeration', async () => {
    Object.defineProperty(indexedDB, 'databases', { value: undefined, configurable: true });
    expect(await readLegacySettings()).toBeNull();
    delete (indexedDB as unknown as { databases?: unknown }).databases;
    expect(await indexedDB.databases()).toEqual([]);
  });

  it.each([1, 2, 3, 4])('validates and migrates database v%i only in memory; original bytes and version stay unchanged', async version => {
    const source = settings();
    const symbols: unknown[] = source.symbols.map(symbol => version < 3 ? {
      ...symbol,
      drawings: symbol.drawings.map(drawing => ({ ...drawing, scope: { timeframes: 'all' } })),
      indicators: symbol.indicators.map(indicator => ({ ...indicator, scope: {} })),
    } : symbol);
    await seedLegacy(version, source.app, symbols);
    const first = await readLegacySettings();
    expect(first?.version).toBe(4);
    expect(first?.symbols[0].drawings[0].scope.timeframes).toEqual(['1D']);
    expect(first?.symbols[0].indicators[0].scope.timeframe).toBe('1D');
    expect(countLegacySettings(first!)).toEqual({ drawings: 1, indicators: 1, lists: 1, settings: 2, alerts: 0, presets: 0 });
    expect(await readLegacySettings()).toEqual(first);
    const raw = await openDB(LEGACY_SETTINGS_DATABASE);
    expect(raw.version).toBe(version);
    expect(await raw.get('app', 'settings')).toEqual(source.app);
    expect(await raw.getAll('symbols')).toEqual(symbols);
    raw.close();
  });

  it('rejects malformed legacy records without replacing or upgrading them', async () => {
    await seedLegacy(2, { activeSymbol: 'bad' }, [{ symbol: 'AAPL', drawings: ['bad'] }]);
    await expect(readLegacySettings()).rejects.toThrow();
    const raw = await openDB(LEGACY_SETTINGS_DATABASE);
    expect(raw.version).toBe(2);
    expect(await raw.get('app', 'settings')).toEqual({ activeSymbol: 'bad' });
    expect(await raw.getAll('symbols')).toEqual([{ symbol: 'AAPL', drawings: ['bad'] }]);
    raw.close();
  });
});

describe('additive account chart settings merge', () => {
  it('never replaces account records when loading or validating the existing account failed', async () => {
    const storage = new IndexedDBStore(`legacy-load-failure-${crypto.randomUUID()}`);
    const account = settings();
    account.app.activeSymbol = 'NVDA';
    account.symbols[0].drawings[0].points[0].price = 999;
    await storage.import(account);
    vi.spyOn(storage, 'load').mockRejectedValueOnce(new Error('Account data could not be validated'));
    const writes = vi.spyOn(storage, 'import');
    const store = new AppStore(storage);
    await store.initialize();
    expect(store.getSnapshot().ready).toBe(true);
    await expect(store.mergeLegacySettings(settings())).rejects.toThrow('尚未完整載入');
    expect(writes).not.toHaveBeenCalled();
    expect((await storage.export()).symbols).toEqual(account.symbols);
    expect((await storage.export()).app.activeSymbol).toBe('NVDA');
    await storage.close();
  });

  it('keeps account drawings, indicators, preferences and app values; adds only missing records and views', () => {
    const legacy = settings(), account = structuredClone(legacy);
    account.app.activeSymbol = 'NVDA';
    account.app.watchlist = ['NVDA'];
    account.symbols[0].drawings[0].points[0].price = 222;
    account.symbols[0].indicators[0].period = 58;
    account.symbols[0].preferences.magnet = true;
    legacy.symbols[0].drawings.push({ ...legacy.symbols[0].drawings[0], id: 'missing-drawing' });
    legacy.symbols[0].indicators.push({ ...legacy.symbols[0].indicators[0], id: 'missing-indicator' });
    legacy.symbols[0].preferences.views['1W'] = { from: 10, to: 20 };
    const { data, added, conflicts } = mergeLegacySettings(account, legacy);
    expect(data.app.activeSymbol).toBe('NVDA');
    expect(data.app.watchlist).toEqual(['NVDA', 'AAPL']);
    expect(data.symbols[0].drawings.map(drawing => drawing.id)).toEqual(['old-drawing', 'missing-drawing']);
    expect(data.symbols[0].drawings[0].points[0].price).toBe(222);
    expect(data.symbols[0].indicators.map(indicator => indicator.period)).toEqual([58, 24]);
    expect(data.symbols[0].preferences.magnet).toBe(true);
    expect(data.symbols[0].preferences.views['1W']).toEqual({ from: 10, to: 20 });
    expect(added).toBe(4);
    expect(conflicts).toBe(4);
    const rerun = mergeLegacySettings(data, legacy);
    expect(rerun.added).toBe(0);
    expect(rerun.data).toEqual(data);
    expect(account.symbols[0].drawings).toHaveLength(1);
    expect(legacy.symbols[0].drawings[0].points[0].price).toBe(100);
  });

  it('treats object key order as equal, and retains an account drawing on a true ID conflict', () => {
    const legacy = settings(), account = structuredClone(legacy);
    account.symbols[0].drawings[0].style = { lineWidth: 2, color: '#112233' };
    expect(mergeLegacySettings(account, legacy).conflicts).toBe(0);
    account.symbols[0].drawings[0].locked = false;
    const merged = mergeLegacySettings(account, legacy);
    expect(merged.conflicts).toBe(1);
    expect(merged.data.symbols[0].drawings[0].locked).toBe(false);
  });

  it('adopts old chart settings for a genuinely new account, then repeats idempotently', async () => {
    const storage = new IndexedDBStore(`legacy-target-${crypto.randomUUID()}`), store = new AppStore(storage);
    await store.initialize();
    const legacy = settings();
    legacy.app.workspace.rightTab = 'alerts';
    expect((await store.mergeLegacySettings(legacy)).added).toBeGreaterThan(0);
    expect(store.getSnapshot().app.workspace.rightTab).toBe('alerts');
    expect((await store.mergeLegacySettings(legacy)).added).toBe(0);
    await store.flush();
    const restored = new AppStore(storage);
    await restored.initialize();
    expect(restored.symbol('AAPL').drawings).toEqual(legacy.symbols[0].drawings);
    expect(restored.getSnapshot().app.workspace.rightTab).toBe('alerts');
    await storage.close();
  });

  it('preserves newer edits made while the import write is pending', async () => {
    const storage = new IndexedDBStore(`legacy-concurrent-${crypto.randomUUID()}`), store = new AppStore(storage);
    await store.initialize();
    let release!: () => void, started!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    const writing = new Promise<void>(resolve => { started = resolve; });
    const persist = storage.import.bind(storage);
    vi.spyOn(storage, 'import').mockImplementation(async data => { started(); await pending; return persist(data); });
    const importing = store.mergeLegacySettings(settings());
    await writing;
    store.updateSymbol('AAPL', symbol => ({ ...symbol, indicators: symbol.indicators.map(indicator => ({ ...indicator, period: 99 })) }));
    store.updateApp({ activeSymbol: 'NVDA' });
    release();
    await importing;
    await store.flush();
    const restored = new AppStore(storage);
    await restored.initialize();
    expect(restored.symbol('AAPL').indicators[0].period).toBe(99);
    expect(restored.symbol('AAPL').drawings).toHaveLength(1);
    expect(restored.getSnapshot().app.activeSymbol).toBe('NVDA');
    await storage.close();
  });

  it('preserves both imported items and an immediate edit before the queued write begins', async () => {
    const storage = new IndexedDBStore(`legacy-immediate-${crypto.randomUUID()}`), store = new AppStore(storage);
    await store.initialize();
    const importing = store.mergeLegacySettings(settings());
    store.updateSymbol('AAPL', symbol => ({ ...symbol, preferences: { ...symbol.preferences, magnet: true } }));
    store.updateApp({ activeSymbol: 'NVDA' });
    await importing;
    await store.flush();
    const restored = new AppStore(storage);
    await restored.initialize();
    expect(restored.symbol('AAPL').drawings).toHaveLength(1);
    expect(restored.symbol('AAPL').indicators).toHaveLength(1);
    expect(restored.symbol('AAPL').preferences.magnet).toBe(true);
    expect(restored.getSnapshot().app.watchlist).toEqual(['AAPL']);
    expect(restored.getSnapshot().app.activeSymbol).toBe('NVDA');
    await storage.close();
  });
});
