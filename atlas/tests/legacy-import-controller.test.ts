import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SettingsExport } from '../src/storage/schema';
import type { BrowserLegacySnapshot } from '../src/storage/legacyBrowserSettings';

const h = vi.hoisted(() => ({
  userId: null as string | null,
  mode: 'none' as 'none' | 'device' | 'cloud',
  valid: true,
  readChart: vi.fn<() => Promise<SettingsExport | null>>(),
  readBrowser: vi.fn<() => BrowserLegacySnapshot | null | Promise<BrowserLegacySnapshot | null>>(),
  mergeChart: vi.fn<(data: SettingsExport) => Promise<{ added: number; conflicts: number }>>(),
  mergeBrowser: vi.fn<(data: BrowserLegacySnapshot, assertCurrent: () => void) => Promise<{ added: number; conflicts: number; warnings: string[] }>>(),
  initializeChart: vi.fn<() => Promise<void>>(),
  prepareSync: vi.fn<() => Promise<void>>(),
  getChart: vi.fn(),
}));
vi.mock('../src/app/HostingMode', () => ({ SCREENER_HOSTING: true, storageName: (name: string) => `${name}-screener` }));
vi.mock('../src/cloud/lineIdentity', () => ({ refreshLineIdentity: async () => {
  const userId = h.userId;
  return userId ? { userId, idToken: () => h.valid && h.userId === userId ? `token-${userId}` : null } : null;
} }));
vi.mock('../src/storage/legacySettings', () => ({
  readLegacySettings: h.readChart,
  countLegacySettings: () => ({ drawings: 2, indicators: 1, lists: 1, settings: 2 }),
}));
vi.mock('../src/storage/legacyBrowserSettings', () => ({ readLegacyBrowserSettings: h.readBrowser, mergeLegacyBrowserSettings: h.mergeBrowser }));
vi.mock('../src/app/chartStore', () => ({ getChartStore: h.getChart, initializeChartStore: h.initializeChart }));
vi.mock('../src/cloud/startDrawingSync', () => ({ prepareDrawingSyncForImport: h.prepareSync }));

const A = `U${'1'.repeat(32)}`, B = `U${'2'.repeat(32)}`;
const legacy: SettingsExport = {
  version: 4, exportedAt: '2026-10-01T00:00:00Z',
  app: { watchlist: ['AAPL'], activeSymbol: 'AAPL', provider: 'market', recentSymbols: [], drawingDefaults: {}, alerts: [], indicatorPresets: [], workspace: { rightOpen: true, rightTab: 'indicators', debug: false } },
  symbols: [],
};
const browser: BrowserLegacySnapshot = {
  entries: [{ key: 'atlas-financials-view-v1', value: 'table' }],
  counts: { lists: 1, settings: 3, parameters: 2 }, warnings: [],
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(finish => { resolve = finish; });
  return { promise, resolve };
}
function grant(userId = h.userId, storageMode = h.mode) {
  return new Response(JSON.stringify({ ok: true, data: { userId, storageMode, isAdmin: false, revision: storageMode } }));
}
let disk: Map<string, string>;
let reads: ReturnType<typeof vi.fn<(key: string) => string | null>>;
let writes: ReturnType<typeof vi.fn<(key: string, value: string) => void>>;
let removals: ReturnType<typeof vi.fn<(key: string) => void>>;
let fetcher: ReturnType<typeof vi.fn<typeof fetch>>;

beforeEach(() => {
  vi.resetModules(); vi.clearAllMocks();
  h.userId = null; h.mode = 'none'; h.valid = true;
  disk = new Map();
  reads = vi.fn(key => disk.get(key) ?? null);
  writes = vi.fn((key, value) => { disk.set(key, value); });
  removals = vi.fn(key => { disk.delete(key); });
  vi.stubGlobal('localStorage', { getItem: reads, setItem: writes, removeItem: removals });
  fetcher = vi.fn<typeof fetch>(async () => grant());
  vi.stubGlobal('fetch', fetcher);
  h.readChart.mockResolvedValue(structuredClone(legacy));
  h.readBrowser.mockReturnValue(structuredClone(browser));
  h.mergeChart.mockResolvedValue({ added: 3, conflicts: 1 });
  h.mergeBrowser.mockImplementation(async (_data, check) => { check(); return { added: 2, conflicts: 1, warnings: [] }; });
  h.initializeChart.mockResolvedValue(); h.prepareSync.mockResolvedValue();
  h.getChart.mockReturnValue({ mergeLegacySettings: h.mergeChart });
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

async function verified(mode: 'device' | 'cloud' = 'device', userId = A) {
  h.userId = userId; h.mode = mode;
  const { accessState } = await import('../src/auth/access');
  await accessState.initialize();
  const api = await import('../src/storage/legacyImport');
  await api.ensureLegacyImport();
  return api;
}

describe('explicit legacy import authorization and decisions', () => {
  it.each([null, A])('never inspects legacy data, reads markers, or imports for guest/none (%s)', async userId => {
    h.userId = userId;
    const api = await import('../src/storage/legacyImport');
    await api.ensureLegacyImport(); await api.importLegacyData(); api.dismissLegacyImport(); await api.showLegacyImportAgain();
    expect(api.legacyImportState.getSnapshot().status).toBe('idle');
    expect(h.readChart).not.toHaveBeenCalled(); expect(h.readBrowser).not.toHaveBeenCalled();
    expect(h.mergeChart).not.toHaveBeenCalled(); expect(h.mergeBrowser).not.toHaveBeenCalled();
    expect(reads).not.toHaveBeenCalled(); expect(writes).not.toHaveBeenCalled(); expect(removals).not.toHaveBeenCalled();
  });

  it.each(['device', 'cloud'] as const)('only copies after an explicit click in %s and reports preserved conflicts', async mode => {
    const api = await verified(mode);
    expect(api.legacyImportState.getSnapshot()).toMatchObject({ status: 'available', counts: { drawings: 2, indicators: 1, lists: 2, settings: 7 } });
    await api.ensureLegacyImport();
    expect(h.readChart).toHaveBeenCalledOnce(); expect(h.mergeChart).not.toHaveBeenCalled(); expect(writes).not.toHaveBeenCalled();
    await api.importLegacyData();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(h.mergeChart).toHaveBeenCalledExactlyOnceWith(legacy);
    expect(h.mergeBrowser).toHaveBeenCalledExactlyOnceWith(browser, expect.any(Function));
    expect(h.prepareSync).toHaveBeenCalledTimes(mode === 'cloud' ? 1 : 0);
    expect(api.legacyImportState.getSnapshot()).toMatchObject({ status: 'imported', added: 5, conflicts: 2 });
    expect(JSON.parse(disk.get(api.legacyImportMarkerKey(A))!)).toMatchObject({ version: 1, status: 'imported' });
    await api.importLegacyData();
    expect(h.mergeChart).toHaveBeenCalledOnce(); expect(h.mergeBrowser).toHaveBeenCalledOnce();
  });

  it('records dismissal for this account/browser across modes and allows an explicit recheck', async () => {
    const api = await verified();
    api.dismissLegacyImport();
    expect(api.legacyImportState.getSnapshot().status).toBe('dismissed');
    expect(h.mergeChart).not.toHaveBeenCalled();
    h.mode = 'cloud';
    const { accessState } = await import('../src/auth/access');
    await accessState.refresh(); await api.ensureLegacyImport();
    expect(api.legacyImportState.getSnapshot().status).toBe('dismissed');
    expect(h.readChart).toHaveBeenCalledOnce();
    await api.showLegacyImportAgain();
    expect(removals).toHaveBeenCalledWith(api.legacyImportMarkerKey(A));
    expect(api.legacyImportState.getSnapshot().status).toBe('available');
    expect(h.readChart).toHaveBeenCalledTimes(2); expect(h.mergeChart).not.toHaveBeenCalled();
  });

  it('suppresses an imported decision after reopening the account in a different mode', async () => {
    const api = await verified();
    await api.importLegacyData();
    const marker = disk.get(api.legacyImportMarkerKey(A));
    h.mode = 'cloud';
    const { accessState } = await import('../src/auth/access');
    await accessState.refresh(); await api.ensureLegacyImport();
    expect(api.legacyImportState.getSnapshot().status).toBe('dismissed');
    expect(disk.get(api.legacyImportMarkerKey(A))).toBe(marker);
    expect(h.readChart).toHaveBeenCalledOnce(); expect(h.mergeChart).toHaveBeenCalledOnce();
  });

  it('leaves the shared source available for a second account and never automatically copies it', async () => {
    const api = await verified();
    await api.importLegacyData();
    const markerA = disk.get(api.legacyImportMarkerKey(A));
    h.userId = B;
    const { accessState } = await import('../src/auth/access');
    await accessState.refresh(); await api.ensureLegacyImport();
    expect(api.legacyImportState.getSnapshot().status).toBe('available');
    expect(h.readChart).toHaveBeenCalledTimes(2); expect(h.mergeChart).toHaveBeenCalledOnce();
    expect(disk.get(api.legacyImportMarkerKey(B))).toBeUndefined();
    await api.importLegacyData();
    expect(h.mergeChart).toHaveBeenNthCalledWith(2, legacy);
    expect(disk.get(api.legacyImportMarkerKey(A))).toBe(markerA);
    expect(JSON.parse(disk.get(api.legacyImportMarkerKey(B))!)).toMatchObject({ status: 'imported' });
    expect([...disk.keys()]).toEqual([api.legacyImportMarkerKey(A), api.legacyImportMarkerKey(B)]);
  });

  it('supports idempotent explicit reruns without inventing additions when merge reports existing items', async () => {
    const api = await verified();
    await api.importLegacyData();
    await api.showLegacyImportAgain();
    h.mergeChart.mockResolvedValueOnce({ added: 0, conflicts: 1 });
    h.mergeBrowser.mockResolvedValueOnce({ added: 0, conflicts: 1, warnings: [] });
    await api.importLegacyData();
    expect(api.legacyImportState.getSnapshot()).toMatchObject({ status: 'imported', added: 0, conflicts: 2 });
    expect(h.mergeChart).toHaveBeenNthCalledWith(2, legacy);
  });

  it('marks a genuinely empty source without saving a decision', async () => {
    h.readChart.mockResolvedValueOnce(null); h.readBrowser.mockReturnValueOnce(null);
    const api = await verified();
    expect(api.legacyImportState.getSnapshot()).toMatchObject({ status: 'empty', counts: { drawings: 0, indicators: 0, lists: 0, settings: 0 } });
    await api.importLegacyData(); api.dismissLegacyImport();
    expect(writes).not.toHaveBeenCalled(); expect(h.mergeChart).not.toHaveBeenCalled();
  });

  it('keeps source errors visible and retryable without recording a completed decision', async () => {
    h.readChart.mockRejectedValueOnce(new Error('legacy database unavailable'));
    const api = await verified();
    expect(api.legacyImportState.getSnapshot()).toMatchObject({ status: 'error', error: 'legacy database unavailable' });
    expect(writes).not.toHaveBeenCalled(); expect(h.mergeChart).not.toHaveBeenCalled();
    await api.showLegacyImportAgain();
    expect(api.legacyImportState.getSnapshot().status).toBe('available');
  });

  it('observes an asynchronous chart failure together with a synchronous browser storage failure', async () => {
    h.readChart.mockRejectedValueOnce(new Error('legacy chart unavailable'));
    h.readBrowser.mockImplementationOnce(() => { throw new Error('browser storage blocked'); });
    const api = await verified();
    expect(api.legacyImportState.getSnapshot()).toMatchObject({
      status: 'error', error: 'legacy chart unavailable；browser storage blocked',
    });
    expect(h.mergeChart).not.toHaveBeenCalled(); expect(h.mergeBrowser).not.toHaveBeenCalled(); expect(writes).not.toHaveBeenCalled();
  });

  it('stops before copying any chart data when browser source validation reports skipped records', async () => {
    h.readBrowser.mockReturnValueOnce({ ...browser, warnings: ['舊版設定格式無法辨識；原始資料未變動。'] });
    const api = await verified();
    expect(api.legacyImportState.getSnapshot()).toMatchObject({ status: 'error', error: '舊版設定格式無法辨識；原始資料未變動。' });
    await api.importLegacyData();
    expect(h.mergeChart).not.toHaveBeenCalled(); expect(h.mergeBrowser).not.toHaveBeenCalled(); expect(writes).not.toHaveBeenCalled();
  });
});

describe('legacy import cancellation across account and permission changes', () => {
  it.each(['account', 'mode', 'expiry'] as const)('does not publish or copy an old source after %s changes during discovery', async change => {
    const source = deferred<SettingsExport | null>();
    h.readChart.mockReturnValueOnce(source.promise);
    h.userId = A; h.mode = 'device';
    const { accessState } = await import('../src/auth/access');
    await accessState.initialize();
    const api = await import('../src/storage/legacyImport');
    const checking = api.ensureLegacyImport();
    await vi.waitFor(() => expect(h.readChart).toHaveBeenCalledOnce());
    if (change === 'expiry') h.valid = false;
    else {
      if (change === 'account') h.userId = B;
      else h.mode = 'none';
      await accessState.refresh();
    }
    source.resolve(legacy); await checking;
    await api.importLegacyData();
    expect(api.legacyImportState.getSnapshot().status).not.toBe('available');
    expect(h.mergeChart).not.toHaveBeenCalled(); expect(h.mergeBrowser).not.toHaveBeenCalled(); expect(writes).not.toHaveBeenCalled();
  });

  it('checks server permission again on the import click and stops when revoked', async () => {
    const api = await verified();
    h.mode = 'none';
    await api.importLegacyData();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(h.getChart).not.toHaveBeenCalled(); expect(h.mergeChart).not.toHaveBeenCalled(); expect(writes).not.toHaveBeenCalled();
  });

  it('ignores an older in-flight server refresh after switching to another account', async () => {
    const api = await verified();
    const response = deferred<Response>(); fetcher.mockImplementationOnce(() => response.promise);
    const importing = api.importLegacyData();
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    h.userId = B;
    const { accessState } = await import('../src/auth/access');
    await accessState.refresh();
    response.resolve(grant(A, 'device')); await importing;
    expect(accessState.getSnapshot().userId).toBe(B);
    expect(api.legacyImportState.getSnapshot().status).toBe('idle');
    expect(h.mergeChart).not.toHaveBeenCalled(); expect(writes).not.toHaveBeenCalled();
  });

  it('does not accept a server refresh if the verified LINE token expires in flight', async () => {
    const api = await verified();
    const response = deferred<Response>(); fetcher.mockImplementationOnce(() => response.promise);
    const importing = api.importLegacyData();
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    h.valid = false;
    response.resolve(grant(A, 'device')); await importing;
    expect(h.mergeChart).not.toHaveBeenCalled(); expect(h.mergeBrowser).not.toHaveBeenCalled(); expect(writes).not.toHaveBeenCalled();
  });

  it.each(['account', 'mode', 'expiry'] as const)('does not write a decision or start browser merges after %s changes during the chart merge', async change => {
    const api = await verified();
    const merge = deferred<{ added: number; conflicts: number }>();
    h.mergeChart.mockReturnValueOnce(merge.promise);
    const importing = api.importLegacyData();
    await vi.waitFor(() => expect(h.mergeChart).toHaveBeenCalledOnce());
    const { accessState } = await import('../src/auth/access');
    if (change === 'expiry') h.valid = false;
    else { if (change === 'account') h.userId = B; else h.mode = 'cloud'; await accessState.refresh(); }
    merge.resolve({ added: 1, conflicts: 0 }); await importing;
    expect(h.mergeBrowser).not.toHaveBeenCalled(); expect(writes).not.toHaveBeenCalled();
    expect(disk.get(api.legacyImportMarkerKey(B))).toBeUndefined();
  });

  it('supplies a live context guard to delayed browser merges and never marks the next account complete', async () => {
    const api = await verified();
    const waiting = deferred<void>();
    const destinations: string[] = [];
    h.mergeBrowser.mockImplementationOnce(async (_data, check) => {
      check(); await waiting.promise; check();
      destinations.push(h.userId!);
      return { added: 1, conflicts: 0, warnings: [] };
    });
    const importing = api.importLegacyData();
    await vi.waitFor(() => expect(h.mergeBrowser).toHaveBeenCalledOnce());
    h.userId = B;
    const { accessState } = await import('../src/auth/access');
    await accessState.refresh(); waiting.resolve(); await importing;
    expect(destinations).toEqual([]); expect(writes).not.toHaveBeenCalled();
    expect(api.legacyImportState.getSnapshot().status).toBe('idle');
  });
});
