import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DrawingSyncStatus, SyncStore } from '../src/cloud/DrawingSync';

type Emit = (status: DrawingSyncStatus) => void;
type FakeSync = { start: ReturnType<typeof vi.fn>; pull: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn> };
const h = vi.hoisted(() => ({
  userId: 'account-a' as string | null,
  mode: 'cloud', context: 'account-a:cloud',
  listeners: new Set<() => void>(), instances: [] as FakeSync[],
  initialize: async () => {},
  start: async (emit: Emit) => { emit('synced'); },
  pull: async (emit: Emit) => { emit('synced'); },
}));

vi.mock('../src/auth/access', () => ({
  initializeAccess: () => h.initialize(),
  currentIdentity: () => h.userId ? { userId: h.userId, idToken: () => `token-${h.userId}` } : null,
  accessState: {
    getSnapshot: () => ({ storageMode: h.mode }),
    subscribe: (listener: () => void) => { h.listeners.add(listener); return () => h.listeners.delete(listener); },
  },
}));
vi.mock('../src/auth/storage', () => ({
  getStorageContextKey: () => h.context,
  userStorage: { getItem: () => null, setItem: () => undefined },
}));
vi.mock('../src/cloud/drawingCloudApi', () => ({ DEFAULT_DRAWINGS_URL: '/mock-drawings', createDrawingCloudApi: () => ({}) }));
vi.mock('../src/cloud/DrawingSync', () => ({
  createLocalSyncMetaStore: () => ({}),
  DrawingSync: class {
    start: ReturnType<typeof vi.fn>;
    pull: ReturnType<typeof vi.fn>;
    stop = vi.fn();
    constructor(_store: unknown, _api: unknown, _meta: unknown, options: { onStatus: Emit }) {
      this.start = vi.fn(() => h.start(options.onStatus));
      this.pull = vi.fn(() => h.pull(options.onStatus));
      h.instances.push(this);
    }
  },
}));

let sync: typeof import('../src/cloud/startDrawingSync');
const store = {} as SyncStore;
beforeEach(async () => {
  vi.resetModules();
  h.userId = 'account-a'; h.mode = 'cloud'; h.context = 'account-a:cloud';
  h.listeners.clear(); h.instances.length = 0;
  h.initialize = async () => {};
  h.start = async emit => { emit('synced'); };
  h.pull = async emit => { emit('synced'); };
  vi.stubGlobal('document', Object.assign(new EventTarget(), { visibilityState: 'visible' }));
  sync = await import('../src/cloud/startDrawingSync');
});
afterEach(() => { sync.stopDrawingSync(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('prepare drawing synchronization before legacy merge', () => {
  it('never binds an old workspace to another account while awaiting access initialization', async () => {
    let release!: () => void;
    h.initialize = () => new Promise<void>(resolve => { release = resolve; });
    const initializing = sync.startDrawingSync(store);
    h.userId = 'account-b'; h.context = 'account-b:cloud';
    h.listeners.forEach(listener => listener());
    release();
    await initializing;
    expect(h.instances).toHaveLength(0);
    expect(sync.cloudSyncState.get()).toBe('off');
  });
  it('awaits the initial pull already started by the chart instead of racing past it', async () => {
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    h.start = async emit => { emit('syncing'); await pending; emit('synced'); };
    const initializing = sync.startDrawingSync(store);
    await vi.waitFor(() => expect(h.instances).toHaveLength(1));
    let prepared = false;
    const preparing = sync.prepareDrawingSyncForImport(store).then(() => { prepared = true; });
    await Promise.resolve(); await Promise.resolve();
    expect(prepared).toBe(false);
    expect(h.instances[0].start).toHaveBeenCalledTimes(1);
    release();
    await Promise.all([initializing, preparing]);
    expect(prepared).toBe(true);
    expect(h.instances[0].pull).not.toHaveBeenCalled();
  });

  it('retries a failed initial pull and proceeds only after the account cloud state is available', async () => {
    h.start = async emit => { emit('error'); };
    await sync.startDrawingSync(store);
    expect(sync.cloudSyncState.get()).toBe('error');
    await sync.prepareDrawingSyncForImport(store);
    expect(h.instances).toHaveLength(1);
    expect(h.instances[0].pull).toHaveBeenCalledTimes(1);
    expect(sync.cloudSyncState.get()).toBe('synced');
  });

  it.each(['error', 'expired'] as const)('rejects the import before any merge when retry still reports %s', async status => {
    h.start = async emit => { emit(status); };
    h.pull = async emit => { emit(status); };
    await expect(sync.prepareDrawingSyncForImport(store)).rejects.toThrow('尚未匯入舊版資料');
    expect(h.instances[0].pull).toHaveBeenCalledTimes(1);
  });

  it('cancels preparation when the account changes during an existing initial pull', async () => {
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    h.start = async emit => { emit('syncing'); await pending; emit('synced'); };
    const initializing = sync.startDrawingSync(store);
    await vi.waitFor(() => expect(h.instances).toHaveLength(1));
    const preparing = sync.prepareDrawingSyncForImport(store);
    // Attach rejection handling before releasing the pending asynchronous work.
    const rejected = expect(preparing).rejects.toThrow('匯入已取消');
    await Promise.resolve(); await Promise.resolve();
    h.userId = 'account-b'; h.context = 'account-b:cloud';
    h.listeners.forEach(listener => listener());
    release();
    await initializing;
    await rejected;
    expect(h.instances[0].stop).toHaveBeenCalled();
    expect(sync.cloudSyncState.get()).toBe('off');
  });

  it.each(['device', 'none'])('never creates cloud synchronization for %s mode', async mode => {
    h.mode = mode; h.context = `account-a:${mode}`;
    await sync.startDrawingSync(store);
    expect(h.instances).toHaveLength(0);
    // The coordinator calls prepare only in cloud mode; an accidental call is fail-closed.
    await expect(sync.prepareDrawingSyncForImport(store)).rejects.toThrow('匯入已取消');
    expect(h.instances).toHaveLength(0);
  });
});
