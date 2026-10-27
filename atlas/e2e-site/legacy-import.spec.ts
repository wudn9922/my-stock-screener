import { expect, test, type Page } from '@playwright/test';
import { buildReport, mockSite } from './mocks';
import {
  accessEnvelope,
  accountDatabase,
  accountPreference,
  DEVICE_USER_ID,
  installLineAccount,
  SECOND_USER_ID,
  type TestStorageMode,
} from './account-fixture';

// Synthetic browser settings, never production market data.
const SYMBOL = '2330.TW';
const LEGACY_DB = 'atlas-terminal-screener';
const WATCHLIST_KEY = 'atlas-saved-watchlists-v1';
const PREFERENCES_KEY = 'atlas-site-preferences-screener';
const SHARED_LIST = '20000000-0000-4000-8000-000000000001';
const LEGACY_LIST = '20000000-0000-4000-8000-000000000002';
const drawing = (id: string, price: number) => ({
  id, symbol: SYMBOL, type: 'horizontal',
  points: [{ time: 1791421200, logical: 20, price, timeframe: '1D' }],
  locked: false, visible: true, style: { color: '#ff0000', lineWidth: 2 }, scope: { timeframes: ['1D'] },
});
const indicator = (id: string, period: number) => ({
  id, symbol: SYMBOL, type: 'SMA', period, source: 'close', visible: true, locked: false,
  lineWidth: 2, color: '#00ff00', scope: { timeframe: '1D' },
});
const app = {
  watchlist: [SYMBOL, 'NVDA'], activeSymbol: SYMBOL, provider: 'market', recentSymbols: [],
  drawingDefaults: {}, alerts: [], indicatorPresets: [],
  workspace: { rightOpen: true, rightTab: 'indicators', debug: false },
};
const legacyState = {
  symbol: SYMBOL,
  drawings: [drawing('shared-drawing', 1000), drawing('legacy-drawing', 1100)],
  indicators: [indicator('shared-indicator', 13), indicator('legacy-indicator', 55)],
  preferences: { timeframe: '1D', views: { '1D': { from: 10, to: 30 } }, magnet: true },
};
const accountState = {
  ...legacyState,
  drawings: [drawing('shared-drawing', 2000)],
  indicators: [indicator('shared-indicator', 34)],
  preferences: { timeframe: '1D', views: { '1D': { from: 50, to: 90 } }, magnet: false },
};
const list = (id: string, name: string) => ({
  id, name, market: 'TW', group: 'tw_all', kind: 'manual', filters: '', symbols: [SYMBOL],
  maList: [20, 50, 200], monitorBreakouts: false, updatedAt: 1791421200000,
  history: [], lastViewed: null, lastOpenedAt: null,
});
const legacyDocument = {
  version: 1,
  lists: [list(SHARED_LIST, '舊版同名 ID'), list(LEGACY_LIST, '舊版研究清單')],
};

async function seedBrowser(page: Page, mode: TestStorageMode, populatedAccount: boolean) {
  await page.route('**/__legacy_seed__', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>seed</title>' }));
  await page.goto('./__legacy_seed__');
  await page.evaluate(async input => {
    const put = (name: string, settings: unknown, state: unknown) => new Promise<void>((resolve, reject) => {
      const open = indexedDB.open(name, 4);
      open.onupgradeneeded = () => {
        open.result.createObjectStore('app');
        open.result.createObjectStore('symbols', { keyPath: 'symbol' });
      };
      open.onerror = () => reject(open.error);
      open.onsuccess = () => {
        const db = open.result;
        const tx = db.transaction(['app', 'symbols'], 'readwrite');
        tx.objectStore('app').put(settings, 'settings');
        tx.objectStore('symbols').put(state);
        tx.oncomplete = () => { db.close(); resolve(); };
        tx.onerror = () => { db.close(); reject(tx.error); };
      };
    });
    await put(input.legacyDb, input.app, input.legacyState);
    localStorage.setItem(input.watchlistKey, JSON.stringify(input.legacyDocument));
    localStorage.setItem(input.preferencesKey, JSON.stringify({ colors: 'us', recent: [{ symbol: 'NVDA', name: '舊搜尋' }] }));
    if (input.populatedAccount) {
      await put(input.accountDb, input.app, input.accountState);
      localStorage.setItem(input.accountLists, JSON.stringify({ version: 1, lists: [input.currentList] }));
      localStorage.setItem(input.accountPreferences, JSON.stringify({ colors: 'tw', recent: [{ symbol: 'AAPL', name: '新搜尋' }] }));
    }
  }, {
    legacyDb: LEGACY_DB, accountDb: accountDatabase(DEVICE_USER_ID, mode), app, legacyState, accountState,
    watchlistKey: WATCHLIST_KEY, preferencesKey: PREFERENCES_KEY, legacyDocument, populatedAccount,
    accountLists: accountPreference(WATCHLIST_KEY, DEVICE_USER_ID, mode),
    accountPreferences: accountPreference(PREFERENCES_KEY, DEVICE_USER_ID, mode),
    currentList: list(SHARED_LIST, '帳號保留的較新清單'),
  });
}

async function readBrowserData(page: Page, userId = DEVICE_USER_ID, mode: TestStorageMode = 'device') {
  return page.evaluate(async input => {
    const read = (name: string) => new Promise<{ version: number; app: unknown; symbols: { drawings: { id: string; points: { price: number }[] }[]; indicators: { id: string; period: number }[]; preferences: { magnet: boolean; views: unknown } }[] }>((resolve, reject) => {
      const open = indexedDB.open(name);
      open.onerror = () => reject(open.error);
      open.onupgradeneeded = () => { open.transaction!.abort(); reject(new Error(`Missing database ${name}`)); };
      open.onsuccess = () => {
        const db = open.result;
        const tx = db.transaction(['app', 'symbols'], 'readonly');
        const settings = tx.objectStore('app').get('settings');
        const symbols = tx.objectStore('symbols').getAll();
        tx.oncomplete = () => { const result = { version: db.version, app: settings.result, symbols: symbols.result }; db.close(); resolve(result); };
        tx.onerror = () => { db.close(); reject(tx.error); };
      };
    });
    return {
      legacy: await read(input.legacyDb),
      sourceLists: localStorage.getItem(input.watchlistKey),
      sourcePreferences: localStorage.getItem(input.preferencesKey),
      account: await read(input.accountDb),
      lists: JSON.parse(localStorage.getItem(input.accountLists) ?? '{"version":1,"lists":[]}') as { lists: { id: string; name: string; cloud?: { userId: string } }[] },
      preferences: JSON.parse(localStorage.getItem(input.accountPreferences) ?? '{}') as { colors?: string },
    };
  }, {
    legacyDb: LEGACY_DB, accountDb: accountDatabase(userId, mode), watchlistKey: WATCHLIST_KEY, preferencesKey: PREFERENCES_KEY,
    accountLists: accountPreference(WATCHLIST_KEY, userId, mode), accountPreferences: accountPreference(PREFERENCES_KEY, userId, mode),
  });
}

async function installStoragePolicy(page: Page, mode: TestStorageMode, denied = false, existingDrawings: unknown[] = accountState.drawings) {
  await installLineAccount(page, DEVICE_USER_ID, `token-${DEVICE_USER_ID}`);
  const calls: Record<string, unknown>[] = [];
  const cloudDrawings = new Map<string, unknown[]>([[DEVICE_USER_ID, existingDrawings]]);
  const cloudLists = new Map<string, Map<string, { id: string; definition: unknown; updated_at: string }>>();
  await page.route('**/functions/v1/user-settings', async route => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    const userId = String(body.idToken).replace(/^token-/, '');
    if (body.action === 'access.get') return denied
      ? route.fulfill({ status: 403, json: { ok: false, error: 'Account verification denied' } })
      : route.fulfill({ json: accessEnvelope(userId, mode) });
    calls.push(body);
    if (mode !== 'cloud') return route.fulfill({ status: 403, json: { ok: false, error: 'No cloud grant' } });
    const lists = cloudLists.get(userId) ?? new Map();
    cloudLists.set(userId, lists);
    if (body.action === 'watchlists.save') {
      const id = (body.definition as { id: string }).id;
      lists.set(id, { id, definition: body.definition, updated_at: '2026-10-23T00:00:00Z' });
    }
    const data = body.action === 'watchlists.save'
      ? lists.get((body.definition as { id: string }).id)
      : body.action === 'watchlists.list' ? [...lists.values()] : [];
    return route.fulfill({ json: { ok: true, data } });
  });
  await page.route('**/functions/v1/chart-drawings', async route => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    const userId = String(body.idToken).replace(/^token-/, '');
    calls.push(body);
    if (mode !== 'cloud') return route.fulfill({ status: 403, json: { ok: false, error: 'No cloud grant' } });
    if (body.action === 'atlas.save') cloudDrawings.set(userId, body.drawings as unknown[]);
    return route.fulfill({ json: body.action === 'atlas.list'
      ? { ok: true, records: cloudDrawings.has(userId) ? [{ symbol: SYMBOL, drawings: cloudDrawings.get(userId), updatedAt: 1791421200000 }] : [] }
      : { ok: true, updatedAt: body.updatedAt } });
  });
  return calls;
}

for (const kind of ['guest', 'none', 'denied'] as const) {
  test(`${kind} never reads legacy browser records or offers import`, async ({ page }) => {
    await mockSite(page, { report: buildReport(20) });
    await seedBrowser(page, 'none', false);
    const calls = kind === 'guest' ? [] : await installStoragePolicy(page, 'none', kind === 'denied');
    await page.addInitScript(({ rawDb, rawKeys }) => {
      const state = globalThis as unknown as { __legacyReads: string[] };
      state.__legacyReads = [];
      const get = Storage.prototype.getItem;
      Storage.prototype.getItem = function (key) {
        if (rawKeys.includes(key)) state.__legacyReads.push(`storage:${key}`);
        return get.call(this, key);
      };
      const open = IDBFactory.prototype.open;
      IDBFactory.prototype.open = function (...args) {
        if (args[0] === rawDb) state.__legacyReads.push(`idb:${args[0]}`);
        return open.apply(this, args);
      };
      const databases = IDBFactory.prototype.databases;
      if (databases) IDBFactory.prototype.databases = function () {
        state.__legacyReads.push('idb:databases');
        return databases.call(this);
      };
    }, {
      rawDb: LEGACY_DB,
      rawKeys: [WATCHLIST_KEY, PREFERENCES_KEY, 'atlas-screener-filters-v1', 'atlas-themes-view-v1',
        'atlas-themes-map-v1-screener', 'atlas-financials-view-v1', 'atlas-financials-yoy-v1', 'atlas-parameter-workspace-v1'],
    });
    await page.goto('./?page=console');
    await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', 'none');
    await expect(page.getByTestId('legacy-import-card')).toHaveCount(0);
    await expect(page.getByTestId('legacy-import-reopen')).toHaveCount(0);
    await page.getByRole('button', { name: '個股', exact: true }).click();
    await expect(page.getByTestId('ohlc-header')).toBeVisible();
    await expect(page.getByTestId('legacy-import-confirm')).toHaveCount(0);
    expect(await page.evaluate(() => (globalThis as unknown as { __legacyReads: string[] }).__legacyReads)).toEqual([]);
    expect(calls).toEqual([]);
  });
}

for (const mode of ['device', 'cloud'] as const) {
  test(`${mode} explicitly merges old settings, preserves account conflicts, and can rerun without duplicates`, async ({ page }, testInfo) => {
    await mockSite(page, { report: buildReport(20), fundamentals: true });
    await seedBrowser(page, mode, true);
    const before = await readBrowserData(page, DEVICE_USER_ID, mode);
    const calls = await installStoragePolicy(page, mode);
    await page.goto('./?page=chart&symbol=2330.TW');
    const card = page.getByTestId('legacy-import-card');
    await expect(card).toBeVisible();
    await expect(card).toContainText(/畫線\s*2/);
    await expect(card).toContainText(/指標\s*2/);
    await expect(card).toContainText(/清單\s*\d+/);
    await expect(page.getByTestId('ohlc-header')).toBeVisible();
    expect(calls.filter(call => call.action === 'atlas.save' && JSON.stringify(call).includes('legacy-drawing'))).toEqual([]);
    expect((await readBrowserData(page, DEVICE_USER_ID, mode)).account.symbols[0].drawings).toHaveLength(1);
    await page.screenshot({ path: testInfo.outputPath(`legacy-import-${mode}-prompt.png`) });
    await page.getByTestId('legacy-import-confirm').click();
    await expect(card).toContainText(/匯入完成|已匯入/);
    await expect(card).toContainText(/衝突[^。]*[1-9]|[1-9][^。]*衝突/);
    const after = await readBrowserData(page, DEVICE_USER_ID, mode);
    expect(after.legacy).toEqual(before.legacy);
    expect(after.sourceLists).toEqual(before.sourceLists);
    expect(after.sourcePreferences).toEqual(before.sourcePreferences);
    const state = after.account.symbols[0];
    expect(state.drawings.map(item => item.id).sort()).toEqual(['legacy-drawing', 'shared-drawing']);
    expect(state.drawings.find(item => item.id === 'shared-drawing')!.points[0].price).toBe(2000);
    expect(state.indicators.map(item => item.id).sort()).toEqual(['legacy-indicator', 'shared-indicator']);
    expect(state.indicators.find(item => item.id === 'shared-indicator')!.period).toBe(34);
    expect(state.preferences).toEqual(accountState.preferences);
    expect(after.lists.lists.find(item => item.id === SHARED_LIST)!.name).toBe('帳號保留的較新清單');
    expect(after.lists.lists.find(item => item.id === LEGACY_LIST)!.name).toBe('舊版研究清單');
    expect(after.preferences.colors).toBe('tw');
    if (mode === 'cloud') {
      await expect.poll(() => calls.filter(call => call.action === 'atlas.save' && JSON.stringify(call).includes('legacy-drawing')).length).toBeGreaterThan(0);
      await expect.poll(() => calls.filter(call => call.action === 'watchlists.save' && (call.definition as { id?: string }).id === LEGACY_LIST).length).toBeGreaterThan(0);
      expect(calls.filter(call => call.action === 'atlas.save').every(call => call.idToken === `token-${DEVICE_USER_ID}`)).toBe(true);
    } else expect(calls).toEqual([]);
    await page.reload();
    await expect(page.getByTestId('ohlc-header')).toBeVisible();
    await expect(page.getByTestId('legacy-import-card')).toHaveCount(0);
    await page.getByRole('button', { name: '我的', exact: true }).click();
    await page.getByTestId('legacy-import-reopen').click();
    await expect(page.getByTestId('legacy-import-card')).toBeVisible();
    await page.getByTestId('legacy-import-confirm').click();
    await expect(page.getByTestId('legacy-import-card')).toContainText(/匯入完成|已匯入/);
    const rerun = await readBrowserData(page, DEVICE_USER_ID, mode);
    expect(rerun.account.symbols).toEqual(after.account.symbols);
    expect(rerun.lists.lists.map(item => item.id).sort()).toEqual([SHARED_LIST, LEGACY_LIST].sort());
    expect(rerun.legacy).toEqual(before.legacy);
    expect(rerun.sourceLists).toEqual(before.sourceLists);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  });
}

test('cloud import preserves server-only drawings before merging shared legacy records', async ({ page }) => {
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  await seedBrowser(page, 'cloud', false);
  const calls = await installStoragePolicy(page, 'cloud', false, [drawing('shared-drawing', 2500), drawing('cloud-only', 2600)]);
  await page.goto('./?page=chart&symbol=2330.TW');
  await expect(page.getByTestId('legacy-import-confirm')).toBeVisible();
  await page.getByTestId('legacy-import-confirm').click();
  await expect(page.getByTestId('legacy-import-card')).toContainText('已匯入');
  const after = await readBrowserData(page, DEVICE_USER_ID, 'cloud');
  const drawings = after.account.symbols[0].drawings;
  expect(drawings.map(item => item.id).sort()).toEqual(['cloud-only', 'legacy-drawing', 'shared-drawing']);
  expect(drawings.find(item => item.id === 'shared-drawing')!.points[0].price).toBe(2500);
  expect(after.legacy.symbols[0].drawings.find(item => item.id === 'shared-drawing')!.points[0].price).toBe(1000);
  await expect.poll(() => calls.filter(call => call.action === 'atlas.save' && (call.drawings as { id: string }[]).some(item => item.id === 'legacy-drawing')).length).toBeGreaterThan(0);
});

test('dismissal is per browser account, can be reopened, and leaves legacy available to another account', async ({ page }) => {
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  await seedBrowser(page, 'device', false);
  await installStoragePolicy(page, 'device');
  await page.goto('./?page=console');
  await expect(page.getByTestId('legacy-import-card')).toBeVisible();
  await page.getByTestId('legacy-import-dismiss').click();
  await expect(page.getByTestId('legacy-import-card')).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId('legacy-import-reopen')).toBeVisible();
  await expect(page.getByTestId('legacy-import-card')).toHaveCount(0);
  await page.getByTestId('legacy-import-reopen').click();
  await page.getByTestId('legacy-import-confirm').click();
  await expect(page.getByTestId('legacy-import-card')).toContainText(/匯入完成|已匯入/);
  const first = await readBrowserData(page);
  await page.evaluate(id => {
    const state = globalThis as unknown as { __lineAccount: string; __lineToken: string };
    state.__lineAccount = id;
    state.__lineToken = `token-${id}`;
    window.dispatchEvent(new Event('focus'));
  }, SECOND_USER_ID);
  await expect(page.getByRole('complementary', { name: '目前儲存權限' })).toContainText(SECOND_USER_ID);
  await expect(page.getByTestId('legacy-import-card')).toBeVisible();
  await page.getByTestId('legacy-import-confirm').click();
  await expect(page.getByTestId('legacy-import-card')).toContainText(/匯入完成|已匯入/);
  const second = await readBrowserData(page, SECOND_USER_ID);
  expect(second.legacy).toEqual(first.legacy);
  expect(second.sourceLists).toEqual(first.sourceLists);
  expect(second.sourcePreferences).toEqual(first.sourcePreferences);
  expect(second.account.symbols[0].drawings.map(item => item.id).sort()).toEqual(['legacy-drawing', 'shared-drawing']);
  expect(second.account.symbols[0].drawings.find(item => item.id === 'shared-drawing')!.points[0].price).toBe(1000);
  expect(second.lists.lists.map(item => item.id).sort()).toEqual([SHARED_LIST, LEGACY_LIST].sort());
});
