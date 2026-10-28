import { accessState } from '../auth/access';
import { getStorageContextKey } from '../auth/storage';
import { holdingsContextApi } from './contextApi';
import { MEXC_ERRORS, validateMexcState, type MexcState } from '../../../supabase/functions/user-settings/mexcContract';
import { holdingsStore } from './store';
export type { MexcConnection, MexcState } from '../../../supabase/functions/user-settings/mexcContract';

const blank = { connections: [], retryAt: null, configured: false } as MexcState;
const inactive = { state: blank, loading: false, busy: false, error: '' };
export class MexcStore {
  private readonly context = getStorageContextKey();
  private readonly mode = accessState.getSnapshot().storageMode;
  private snapshot = { state: blank, loading: false, busy: false, error: '' };
  private listeners = new Set<() => void>();
  getSnapshot = () => (this.context === getStorageContextKey() ? this.snapshot : inactive);
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private active() {
    if (this.context !== getStorageContextKey() || this.mode !== 'cloud')
      throw new Error('MEXC 同步需要目前帳號的雲端儲存授權。');
  }
  private set(update: Partial<typeof this.snapshot>) {
    if (this.context !== getStorageContextKey()) return;
    this.snapshot = { ...this.snapshot, ...update };
    this.listeners.forEach((listener) => listener());
  }
  async load() {
    if (this.mode !== 'cloud') return;
    this.active();
    if (this.snapshot.loading || this.snapshot.busy) return;
    this.set({ loading: true });
    try {
      const state = validateMexcState(await holdingsContextApi(this.context, 'holdings.mexc.status'));
      this.active();
      this.set({ state, error: '' });
    } catch {
      this.set({ error: '無法載入 MEXC 連線狀態，請確認同步服務已啟用。' });
    } finally {
      this.set({ loading: false });
    }
  }
  async call(action: 'connect' | 'sync' | 'apply' | 'disconnect', payload: Record<string, unknown>) {
    this.active();
    if (this.snapshot.busy || this.snapshot.loading) throw new Error('請等候目前操作完成。');
    this.set({
      busy: true,
      error: '',
      ...(action === 'connect' || action === 'sync'
        ? { state: { ...this.snapshot.state, retryAt: new Date(Date.now() + 30000).toISOString() } }
        : {}),
    });
    try {
      const raw = await holdingsContextApi<Record<string, unknown>>(this.context, `holdings.mexc.${action}`, payload);
      this.active();
      const state = validateMexcState(raw);
      holdingsStore().acceptCloudDocument(raw.document);
      this.set({ state });
      return state;
    } catch (error) {
      const message =
        error instanceof Error && (Object.values(MEXC_ERRORS) as string[]).includes(error.message)
          ? error.message
          : MEXC_ERRORS.service;
      this.set({ error: message });
      if (message === MEXC_ERRORS.limit && this.context === getStorageContextKey()) {
        try {
          const state = validateMexcState(await holdingsContextApi(this.context, 'holdings.mexc.status'));
          this.active();
          this.set({ state, error: message });
        } catch {
          /* Keep the local cooldown and fixed error when metadata is unavailable. */
        }
      }
      throw new Error(message);
    } finally {
      this.set({ busy: false });
    }
  }
}
let current: { context: string; store: MexcStore } | undefined;
export function mexcStore() {
  const context = getStorageContextKey();
  if (current?.context !== context) current = { context, store: new MexcStore() };
  return current.store;
}
