import { accessState } from '../auth/access';
import { getStorageContextKey } from '../auth/storage';
import { holdingsContextApi } from './contextApi';
import {
  MEXC_ERRORS,
  validateMexcState,
  validateSkippedAssetNames,
  type MexcState,
} from '../../../supabase/functions/user-settings/mexcContract';
import { holdingsStore } from './store';
import {
  validateMexcFutures,
  type MexcFutures,
} from '../../../supabase/functions/user-settings/mexcFuturesContract';
export type { MexcConnection, MexcState } from '../../../supabase/functions/user-settings/mexcContract';

const blank = {
  connections: [],
  retryAt: null,
  configured: false,
} as MexcState;
const inactive = {
  state: blank,
  loading: false,
  busy: false,
  error: '',
  accountErrors: {} as Record<string, string>,
  accountSkippedAssets: {} as Record<string, number>,
  accountSkippedAssetNames: {} as Record<string, string[]>,
  accountFutures: {} as Record<string, MexcFutures>,
  futuresLoading: {} as Record<string, boolean>,
  futuresErrors: {} as Record<string, boolean>,
  futuresUpdatedAt: {} as Record<string, string>,
};
export class MexcStore {
  private readonly context = getStorageContextKey();
  private readonly mode = accessState.getSnapshot().storageMode;
  private snapshot = {
    state: blank,
    loading: false,
    busy: false,
    error: '',
    accountErrors: {} as Record<string, string>,
    accountSkippedAssets: {} as Record<string, number>,
    accountSkippedAssetNames: {} as Record<string, string[]>,
    accountFutures: {} as Record<string, MexcFutures>,
    futuresLoading: {} as Record<string, boolean>,
    futuresErrors: {} as Record<string, boolean>,
    futuresUpdatedAt: {} as Record<string, string>,
  };
  private listeners = new Set<() => void>();
  private futuresAttempts = new Map<string, number>();
  private futuresRequests = new Map<string, Promise<void>>();
  private futuresGeneration = new Map<string, number>();
  private invalidateFutures(accountId: string) {
    this.futuresGeneration.set(accountId, (this.futuresGeneration.get(accountId) ?? 0) + 1);
  }
  private acceptFutures(accountId: string, value: unknown, timestamp: unknown, reset = false) {
    let futures: MexcFutures;
    try {
      futures = validateMexcFutures(value);
    } catch {
      futures = { status: 'unavailable' };
    }
    const validTime =
      typeof timestamp === 'string' &&
      /^\d{4}-\d{2}-\d{2}T/.test(timestamp) &&
      timestamp.length <= 35 &&
      Number.isFinite(Date.parse(timestamp));
    const failed = futures.status === 'unavailable' || !validTime;
    const accountFutures = { ...this.snapshot.accountFutures };
    const futuresUpdatedAt = { ...this.snapshot.futuresUpdatedAt };
    if (reset) {
      delete accountFutures[accountId];
      delete futuresUpdatedAt[accountId];
    }
    if (!failed) {
      accountFutures[accountId] = futures;
      futuresUpdatedAt[accountId] = timestamp as string;
    } else if (!accountFutures[accountId]) accountFutures[accountId] = { status: 'unavailable' };
    this.set({
      accountFutures,
      futuresUpdatedAt,
      futuresErrors: { ...this.snapshot.futuresErrors, [accountId]: failed },
    });
  }
  loadFutures(accountId: string): Promise<void> {
    if (this.context !== getStorageContextKey() || this.mode !== 'cloud') return Promise.resolve();
    const existing = this.futuresRequests.get(accountId);
    if (existing) return existing;
    if (
      this.snapshot.busy ||
      this.snapshot.loading ||
      !this.snapshot.state.connections.some((c) => c.accountId === accountId) ||
      Date.now() - (this.futuresAttempts.get(accountId) ?? -Infinity) < 60000
    )
      return Promise.resolve();
    this.futuresAttempts.set(accountId, Date.now());
    this.invalidateFutures(accountId);
    const generation = this.futuresGeneration.get(accountId);
    this.set({
      futuresLoading: { ...this.snapshot.futuresLoading, [accountId]: true },
    });
    const request = (async () => {
      try {
        const raw = await holdingsContextApi<Record<string, unknown>>(this.context, 'holdings.mexc.futures', {
          accountId,
        });
        this.active();
        if (generation !== this.futuresGeneration.get(accountId)) return;
        this.acceptFutures(accountId, raw.futures, raw.fetchedAt);
      } catch {
        if (generation === this.futuresGeneration.get(accountId))
          this.set({
            futuresErrors: {
              ...this.snapshot.futuresErrors,
              [accountId]: true,
            },
          });
      } finally {
        this.futuresRequests.delete(accountId);
        if (generation === this.futuresGeneration.get(accountId))
          this.set({
            futuresLoading: {
              ...this.snapshot.futuresLoading,
              [accountId]: false,
            },
          });
      }
    })();
    this.futuresRequests.set(accountId, request);
    return request;
  }
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
    const account = payload.account as { id?: unknown } | undefined;
    const accountId =
      typeof payload.accountId === 'string'
        ? payload.accountId
        : typeof account?.id === 'string'
          ? account.id
          : undefined;
    if (accountId && action !== 'apply') {
      this.invalidateFutures(accountId);
      this.set({
        futuresLoading: { ...this.snapshot.futuresLoading, [accountId]: false },
      });
    }
    this.set({
      busy: true,
      error: '',
      ...(action === 'connect' || action === 'sync'
        ? {
            state: {
              ...this.snapshot.state,
              retryAt: new Date(Date.now() + 30000).toISOString(),
            },
          }
        : {}),
    });
    try {
      const raw = await holdingsContextApi<Record<string, unknown>>(
        this.context,
        `holdings.mexc.${action}`,
        payload,
      );
      this.active();
      const state = validateMexcState(raw);
      const syncing = action === 'connect' || action === 'sync';
      const skippedAssets = syncing && raw.skippedAssets !== undefined ? raw.skippedAssets : 0;
      if (!Number.isSafeInteger(skippedAssets) || Number(skippedAssets) < 0 || Number(skippedAssets) > 10000)
        throw new Error(MEXC_ERRORS.service);
      const skippedAssetNames = syncing
        ? validateSkippedAssetNames(raw.skippedAssetNames, Number(skippedAssets))
        : [];
      holdingsStore().acceptCloudDocument(raw.document);
      const accountErrors = { ...this.snapshot.accountErrors };
      const accountSkippedAssets = { ...this.snapshot.accountSkippedAssets };
      const accountSkippedAssetNames = {
        ...this.snapshot.accountSkippedAssetNames,
      };
      const accountFutures = { ...this.snapshot.accountFutures };
      if (accountId) delete accountErrors[accountId];
      if (accountId && syncing) accountSkippedAssets[accountId] = Number(skippedAssets);
      if (accountId && syncing) accountSkippedAssetNames[accountId] = skippedAssetNames;
      if (accountId && action === 'disconnect') delete accountSkippedAssets[accountId];
      if (accountId && action === 'disconnect') delete accountSkippedAssetNames[accountId];
      if (accountId && action === 'disconnect') delete accountFutures[accountId];
      this.set({
        state,
        accountErrors,
        accountSkippedAssets,
        accountSkippedAssetNames,
        accountFutures,
      });
      if (accountId && syncing) {
        this.futuresAttempts.set(accountId, Date.now());
        this.acceptFutures(
          accountId,
          raw.futures,
          raw.futuresFetchedAt ?? state.connections.find((c) => c.accountId === accountId)?.syncedAt,
          action === 'connect',
        );
      }
      if (accountId && action === 'disconnect') {
        const futuresErrors = { ...this.snapshot.futuresErrors },
          futuresUpdatedAt = { ...this.snapshot.futuresUpdatedAt };
        delete futuresErrors[accountId];
        delete futuresUpdatedAt[accountId];
        this.futuresAttempts.delete(accountId);
        this.set({ futuresErrors, futuresUpdatedAt });
      }
      return state;
    } catch (error) {
      const message =
        error instanceof Error && (Object.values(MEXC_ERRORS) as string[]).includes(error.message)
          ? error.message
          : MEXC_ERRORS.service;
      const errorUpdate = accountId
        ? {
            error: '',
            accountErrors: {
              ...this.snapshot.accountErrors,
              [accountId]: message,
            },
          }
        : { error: message };
      this.set(errorUpdate);
      if (message === MEXC_ERRORS.limit && this.context === getStorageContextKey()) {
        try {
          const state = validateMexcState(await holdingsContextApi(this.context, 'holdings.mexc.status'));
          this.active();
          this.set({ state, ...errorUpdate });
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
