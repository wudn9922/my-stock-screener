import { accessState, initializeAccess, refreshAccess } from '../auth/access';
import { canPersist, getStorageContextKey } from '../auth/storage';
import { SCREENER_HOSTING } from '../app/HostingMode';
import { getChartStore, initializeChartStore } from '../app/chartStore';
import { prepareDrawingSyncForImport } from '../cloud/startDrawingSync';
import { countLegacySettings, readLegacySettings } from './legacySettings';
import { mergeLegacyBrowserSettings, readLegacyBrowserSettings, type BrowserLegacySnapshot } from './legacyBrowserSettings';
import type { SettingsExport } from './schema';

export interface LegacyImportSnapshot {
  status: 'idle' | 'checking' | 'available' | 'importing' | 'imported' | 'dismissed' | 'empty' | 'error';
  counts: { drawings: number; indicators: number; lists: number; settings: number };
  added: number;
  conflicts: number;
  warnings: string[];
  error: string | null;
}
interface Entry {
  context: string;
  snapshot: LegacyImportSnapshot;
  chart: SettingsExport | null;
  browser: BrowserLegacySnapshot | null;
  checking?: Promise<void>;
  importing?: Promise<void>;
}
const blank = (): LegacyImportSnapshot => ({ status: 'idle', counts: { drawings: 0, indicators: 0, lists: 0, settings: 0 }, added: 0, conflicts: 0, warnings: [], error: null });
const enabled = () => SCREENER_HOSTING && canPersist() && !!accessState.getSnapshot().userId;
/** Deliberately independent of mode: the decision belongs to this account on this browser. */
export const legacyImportMarkerKey = (userId: string) => `atlas-legacy-import-screener:v1:${userId}`;

class LegacyImportController {
  private entry: Entry = { context: getStorageContextKey(), snapshot: blank(), chart: null, browser: null };
  private listeners = new Set<() => void>();
  constructor() {
    accessState.subscribe(() => {
      if (this.entry.context === getStorageContextKey()) return;
      this.entry = { context: getStorageContextKey(), snapshot: blank(), chart: null, browser: null };
      this.listeners.forEach(listener => listener());
    });
  }
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  getSnapshot = () => this.entry.snapshot;
  private assertCurrent(entry: Entry) {
    if (entry !== this.entry || entry.context !== getStorageContextKey() || !enabled())
      throw new Error('帳號或儲存權限已變更，舊版資料匯入已取消。');
  }
  private publish(entry: Entry, patch: Partial<LegacyImportSnapshot>) {
    if (entry !== this.entry) return;
    entry.snapshot = { ...entry.snapshot, ...patch };
    this.listeners.forEach(listener => listener());
  }
  private marker(entry: Entry, status: 'imported' | 'dismissed') {
    this.assertCurrent(entry);
    const userId = accessState.getSnapshot().userId!;
    localStorage.setItem(legacyImportMarkerKey(userId), JSON.stringify({ version: 1, status, completedAt: new Date().toISOString() }));
  }
  ensure = async () => {
    const requestedContext = getStorageContextKey();
    await initializeAccess();
    if (!enabled() || requestedContext !== getStorageContextKey()) return;
    const entry = this.entry;
    if (entry.checking) return entry.checking;
    if (entry.snapshot.status !== 'idle') return;
    entry.checking = this.check(entry);
    return entry.checking;
  };
  private async check(entry: Entry) {
    this.publish(entry, { status: 'checking', error: null });
    try {
      this.assertCurrent(entry);
      const rawMarker = localStorage.getItem(legacyImportMarkerKey(accessState.getSnapshot().userId!));
      if (rawMarker) {
        let marker: { version?: unknown; status?: unknown } | null = null;
        try { marker = JSON.parse(rawMarker); } catch { /* Invalid decision records do not hide recoverable data. */ }
        if (marker?.version === 1 && (marker.status === 'imported' || marker.status === 'dismissed')) {
          // A saved decision hides the card; the result is shown only for this page's click.
          this.publish(entry, { status: 'dismissed' });
          return;
        }
      }
      // Observe both failures even if a browser storage API throws synchronously.
      const sources = await Promise.allSettled([
        Promise.resolve().then(() => { this.assertCurrent(entry); return readLegacySettings(); }),
        Promise.resolve().then(() => { this.assertCurrent(entry); return readLegacyBrowserSettings(); }),
      ]);
      this.assertCurrent(entry);
      const failures = sources.filter(result => result.status === 'rejected');
      if (failures.length) throw new Error(failures.map(result => String(result.reason instanceof Error ? result.reason.message : result.reason)).join('；'));
      const [chart, browser] = sources as [PromiseFulfilledResult<SettingsExport | null>, PromiseFulfilledResult<BrowserLegacySnapshot | null>];
      entry.chart = chart.value;
      entry.browser = browser.value;
      if (entry.browser?.warnings.length) throw new Error(entry.browser.warnings.join('；'));
      const chartCounts = entry.chart ? countLegacySettings(entry.chart) : { drawings: 0, indicators: 0, lists: 0, settings: 0 };
      const browserCounts = entry.browser?.counts;
      this.publish(entry, {
        status: entry.chart || entry.browser ? 'available' : 'empty',
        counts: {
          drawings: chartCounts.drawings, indicators: chartCounts.indicators,
          lists: chartCounts.lists + (browserCounts?.lists ?? 0),
          settings: chartCounts.settings + (browserCounts?.settings ?? 0) + (browserCounts?.parameters ?? 0),
        },
        warnings: entry.browser?.warnings ?? [],
      });
    } catch (error) {
      this.publish(entry, { status: 'error', error: error instanceof Error ? error.message : '舊版資料讀取失敗；原始資料未變更。' });
    }
  }
  import = async () => {
    if (!enabled()) return;
    const entry = this.entry;
    if (entry.importing) return entry.importing;
    if (entry.snapshot.status !== 'available') return;
    entry.importing = this.copy(entry);
    return entry.importing;
  };
  private async copy(entry: Entry) {
    this.publish(entry, { status: 'importing', error: null, added: 0, conflicts: 0 });
    try {
      // Recheck the server grant on the explicit click; a stale page cannot bypass revocation.
      await refreshAccess();
      this.assertCurrent(entry);
      const store = getChartStore();
      if (entry.chart) {
        await initializeChartStore(store);
        this.assertCurrent(entry);
        if (accessState.getSnapshot().storageMode === 'cloud') {
          await prepareDrawingSyncForImport(store);
          this.assertCurrent(entry);
        }
      }
      let added = 0, conflicts = 0;
      const warnings: string[] = [];
      if (entry.chart) {
        const result = await store.mergeLegacySettings(entry.chart);
        this.assertCurrent(entry);
        added += result.added; conflicts += result.conflicts;
      }
      if (entry.browser) {
        const result = await mergeLegacyBrowserSettings(entry.browser, () => this.assertCurrent(entry));
        this.assertCurrent(entry);
        added += result.added; conflicts += result.conflicts;
        warnings.push(...result.warnings);
      }
      this.marker(entry, 'imported');
      this.publish(entry, { status: 'imported', added, conflicts, warnings });
    } catch (error) {
      this.publish(entry, { status: 'error', error: error instanceof Error ? error.message : '匯入失敗；可重新檢查並重試，舊版資料未變更。' });
    } finally {
      entry.importing = undefined;
    }
  }
  dismiss = () => {
    if (!enabled()) return;
    const entry = this.entry;
    if (entry.snapshot.status !== 'available') return;
    try {
      this.marker(entry, 'dismissed');
      this.publish(entry, { status: 'dismissed' });
    } catch (error) {
      this.publish(entry, { status: 'error', error: error instanceof Error ? error.message : '無法記錄稍後匯入。' });
    }
  };
  showAgain = async () => {
    const requestedContext = getStorageContextKey();
    await initializeAccess();
    if (!enabled() || requestedContext !== getStorageContextKey()) return;
    const entry = this.entry;
    if (entry.importing) return;
    if (entry.snapshot.status === 'checking') return entry.checking;
    try {
      this.assertCurrent(entry);
      localStorage.removeItem(legacyImportMarkerKey(accessState.getSnapshot().userId!));
      entry.chart = null; entry.browser = null; entry.checking = undefined;
      this.publish(entry, blank());
      await this.ensure();
    } catch (error) {
      this.publish(entry, { status: 'error', error: error instanceof Error ? error.message : '無法重新檢查舊版資料。' });
    }
  };
}

export const legacyImportState = new LegacyImportController();
export const ensureLegacyImport = legacyImportState.ensure;
export const importLegacyData = legacyImportState.import;
export const dismissLegacyImport = legacyImportState.dismiss;
export const showLegacyImportAgain = legacyImportState.showAgain;
