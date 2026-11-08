import { beforeEach, describe, expect, it, vi } from 'vitest';
const h = vi.hoisted(() => ({ api: vi.fn(), accept: vi.fn(), context: 'cloud:offline-test' }));
vi.mock('../src/auth/access', () => ({ accessState: { getSnapshot: () => ({ storageMode: 'cloud' }) } }));
vi.mock('../src/auth/storage', () => ({ getStorageContextKey: () => h.context }));
vi.mock('../src/holdings/contextApi', () => ({ holdingsContextApi: h.api }));
vi.mock('../src/holdings/store', () => ({ holdingsStore: () => ({ acceptCloudDocument: h.accept }) }));
import { MexcStore } from '../src/holdings/mexc';
import { MEXC_ERRORS } from '../../supabase/functions/user-settings/mexcContract';
const accountId = '11111111-1111-4111-8111-111111111111';
const state = {
  configured: true,
  retryAt: null,
  connections: [
    {
      accountId,
      keyLast4: '9876',
      connectedAt: '2026-08-01T00:00:00Z',
      syncedAt: '2026-11-05T12:00:00Z',
      snapshotId: '22222222-2222-4222-8222-222222222222',
      holdingRevision: 1,
      needsReview: false,
      balances: [{ asset: 'BTC', symbol: 'BTC/USDT', free: '1', locked: '0', quantity: '1' }],
      quotes: {},
    },
  ],
};
beforeEach(() => {
  h.context = 'cloud:offline-test';
  h.api.mockReset();
  h.accept.mockReset();
});
describe('account-specific MEXC invalid-key state', () => {
  it('keeps the last snapshot and sync timestamp after failure and after a metadata reload', async () => {
    h.api.mockResolvedValue(state);
    const store = new MexcStore();
    await store.load();
    const previous = store.getSnapshot().state;
    h.api.mockRejectedValue(new Error(MEXC_ERRORS.credentials));
    await expect(store.call('sync', { accountId })).rejects.toThrow(MEXC_ERRORS.credentials);
    expect(store.getSnapshot().state.connections).toEqual(previous.connections);
    expect(store.getSnapshot().accountErrors[accountId]).toBe(MEXC_ERRORS.credentials);
    expect(store.getSnapshot().error).toBe('');
    expect(h.accept).not.toHaveBeenCalled();
    h.api.mockResolvedValue(state);
    await store.load();
    expect(store.getSnapshot().accountErrors[accountId]).toBe(MEXC_ERRORS.credentials);
    h.api.mockResolvedValue({ ...state, document: { version: 1, accounts: [] } });
    await store.call('sync', { accountId });
    expect(store.getSnapshot().accountErrors[accountId]).toBeUndefined();
    expect(h.accept).toHaveBeenCalledTimes(1);
  });
  it('does not show an account error on a different account', async () => {
    h.api.mockRejectedValue(new Error(MEXC_ERRORS.credentials));
    const store = new MexcStore();
    await expect(store.call('sync', { accountId })).rejects.toThrow();
    expect(store.getSnapshot().accountErrors['33333333-3333-4333-8333-333333333333']).toBeUndefined();
    expect(store.getSnapshot().error).toBe('');
  });
});

describe('transient account-specific skipped asset notice', () => {
  it('handles connect, metadata reload, apply and sync without persisting the skipped count', async () => {
    const document = { version: 1, accounts: [] };
    h.api.mockResolvedValue({ ...state, document, skippedAssets: 2 });
    const store = new MexcStore();
    await store.call('connect', { account: { id: accountId } });
    expect(store.getSnapshot().accountSkippedAssets).toEqual({
      [accountId]: 2,
    });
    expect(store.getSnapshot().state).not.toHaveProperty('skippedAssets');
    expect(h.accept).toHaveBeenCalledExactlyOnceWith(document);
    expect(h.api.mock.calls[0]![2]).not.toHaveProperty('skippedAssets');
    h.api.mockResolvedValue({ ...state, document });
    await store.load();
    await store.call('apply', { accountId });
    expect(store.getSnapshot().accountSkippedAssets[accountId]).toBe(2);
    const otherId = '33333333-3333-4333-8333-333333333333';
    h.api.mockResolvedValue({ ...state, document, skippedAssets: 1 });
    await store.call('sync', { accountId: otherId });
    expect(store.getSnapshot().accountSkippedAssets).toEqual({
      [accountId]: 2,
      [otherId]: 1,
    });
    h.api.mockResolvedValue({ ...state, document, skippedAssets: 0 });
    await store.call('sync', { accountId });
    expect(store.getSnapshot().accountSkippedAssets).toEqual({
      [accountId]: 0,
      [otherId]: 1,
    });
    await store.call('disconnect', { accountId: otherId });
    expect(store.getSnapshot().accountSkippedAssets[otherId]).toBeUndefined();
    expect(new MexcStore().getSnapshot().accountSkippedAssets).toEqual({});
  });

  it('accepts older responses without the optional count', async () => {
    h.api.mockResolvedValue({
      ...state,
      document: { version: 1, accounts: [] },
    });
    const store = new MexcStore();
    await store.call('sync', { accountId });
    expect(store.getSnapshot().accountSkippedAssets[accountId]).toBe(0);
  });

  it.each([-1, 1.5, '2', null, 10001])(
    'rejects an invalid skipped count %j before accepting the cloud document',
    async (skippedAssets) => {
      h.api.mockResolvedValue({
        ...state,
        document: { version: 1, accounts: [] },
        skippedAssets,
      });
      const store = new MexcStore();
      await expect(store.call('sync', { accountId })).rejects.toThrow(MEXC_ERRORS.service);
      expect(h.accept).not.toHaveBeenCalled();
      expect(store.getSnapshot().accountSkippedAssets).toEqual({});
    },
  );
});

describe('transient skipped asset names', () => {
  it('keeps validated names per account in RAM, retains them on apply/load and clears them on disconnect', async () => {
    const document = { version: 1, accounts: [] };
    h.api.mockResolvedValue({ ...state, document, skippedAssets: 2, skippedAssetNames: ['XXX-Y', 'usde'] });
    const store = new MexcStore();
    await store.call('connect', { account: { id: accountId } });
    expect(store.getSnapshot().accountSkippedAssetNames).toEqual({ [accountId]: ['XXX-Y', 'usde'] });
    expect(store.getSnapshot().state).not.toHaveProperty('skippedAssetNames');
    h.api.mockResolvedValue({ ...state, document });
    await store.load();
    await store.call('apply', { accountId });
    expect(store.getSnapshot().accountSkippedAssetNames[accountId]).toEqual(['XXX-Y', 'usde']);
    for (const call of h.api.mock.calls) if (call[2]) expect(call[2]).not.toHaveProperty('skippedAssetNames');
    h.api.mockResolvedValue({ ...state, document, skippedAssets: 0 });
    await store.call('sync', { accountId });
    expect(store.getSnapshot().accountSkippedAssetNames[accountId]).toEqual([]);
    await store.call('disconnect', { accountId });
    expect(store.getSnapshot().accountSkippedAssetNames[accountId]).toBeUndefined();
    expect(new MexcStore().getSnapshot().accountSkippedAssetNames).toEqual({});
  });
  it('accepts an older response with a count but no names', async () => {
    h.api.mockResolvedValue({ ...state, document: { version: 1, accounts: [] }, skippedAssets: 3 });
    const store = new MexcStore();
    await store.call('sync', { accountId });
    expect(store.getSnapshot().accountSkippedAssets[accountId]).toBe(3);
    expect(store.getSnapshot().accountSkippedAssetNames[accountId]).toEqual([]);
  });
  it.each([
    ['not an array', 'XXX-Y', 1],
    ['more names than skipped', ['A-1', 'B-2'], 1],
    ['over 20 names', Array.from({ length: 21 }, (_, i) => `N-${i}`), 30],
    ['over 40 characters', ['x'.repeat(41)], 1],
    ['a control character', ['BAD\u0007'], 1],
    ['a bidi override', ['BAD\u202E'], 1],
    ['a blank name', ['  '], 1],
    ['a non-string', [7], 1],
  ])(
    'rejects names with %s before accepting the cloud document',
    async (_label, skippedAssetNames, skippedAssets) => {
      h.api.mockResolvedValue({
        ...state,
        document: { version: 1, accounts: [] },
        skippedAssets,
        skippedAssetNames,
      });
      const store = new MexcStore();
      await expect(store.call('sync', { accountId })).rejects.toThrow(MEXC_ERRORS.service);
      expect(h.accept).not.toHaveBeenCalled();
      expect(store.getSnapshot().accountSkippedAssetNames).toEqual({});
    },
  );
  it('accepts exactly 20 names of 40 characters', async () => {
    const names = Array.from({ length: 20 }, (_, i) => `${i}`.padEnd(40, '-'));
    h.api.mockResolvedValue({
      ...state,
      document: { version: 1, accounts: [] },
      skippedAssets: 25,
      skippedAssetNames: names,
    });
    const store = new MexcStore();
    await store.call('sync', { accountId });
    expect(store.getSnapshot().accountSkippedAssetNames[accountId]).toEqual(names);
  });
});

describe('RAM-only futures data', () => {
  it('isolates accounts, updates on sync, retains on apply/load, clears on disconnect and never passes futures into holdings', async () => {
    const document = { version: 1, accounts: [] };
    h.api.mockResolvedValue({
      ...state,
      document,
      futures: { status: 'no-permission' },
    });
    const store = new MexcStore();
    await store.call('connect', { account: { id: accountId } });
    expect(store.getSnapshot().accountFutures[accountId]).toEqual({
      status: 'no-permission',
    });
    const other = '33333333-3333-4333-8333-333333333333';
    h.api.mockResolvedValue({
      ...state,
      document,
      futures: { status: 'unavailable' },
    });
    await store.call('sync', { accountId: other });
    expect(store.getSnapshot().accountFutures[accountId]).toEqual({
      status: 'no-permission',
    });
    expect(store.getSnapshot().accountFutures[other]).toEqual({
      status: 'unavailable',
    });
    await store.load();
    await store.call('apply', { accountId });
    expect(store.getSnapshot().accountFutures[accountId]).toEqual({
      status: 'no-permission',
    });
    expect(store.getSnapshot().state).not.toHaveProperty('futures');
    for (const [doc] of h.accept.mock.calls) expect(doc).toBe(document);
    for (const call of h.api.mock.calls) if (call[2]) expect(call[2]).not.toHaveProperty('futures');
    await store.call('disconnect', { accountId: other });
    expect(store.getSnapshot().accountFutures[other]).toBeUndefined();
    expect(new MexcStore().getSnapshot().accountFutures).toEqual({});
  });
  it('a malformed futures payload becomes unavailable without failing spot or displaying unchecked fields', async () => {
    const document = { version: 1, accounts: [] };
    h.api.mockResolvedValue({
      ...state,
      document,
      futures: {
        status: 'ok',
        account: {},
        positions: [],
        skippedPositions: 0,
      },
    });
    const store = new MexcStore();
    await store.call('sync', { accountId });
    expect(store.getSnapshot().accountFutures[accountId]).toEqual({
      status: 'unavailable',
    });
    expect(h.accept).toHaveBeenCalledExactlyOnceWith(document);
    expect(store.getSnapshot().accountErrors).toEqual({});
  });
});

describe('independent RAM-only futures reads', () => {
  const futures = {
    status: 'ok',
    account: {
      equity: '100',
      available: '90',
      positionMargin: '10',
      unrealized: '-1',
    },
    positions: [],
    skippedPositions: 0,
  };
  const fetchedAt = '2026-11-08T00:00:00Z';
  it('loads connected accounts without accepting a spot document, deduplicates and bounds attempts to once a minute', async () => {
    vi.useFakeTimers();
    try {
      h.api.mockResolvedValueOnce(state);
      const store = new MexcStore();
      await store.load();
      let finish!: (value: unknown) => void;
      h.api.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      );
      const a = store.loadFutures(accountId),
        b = store.loadFutures(accountId);
      expect(a).toBe(b);
      expect(store.getSnapshot().futuresLoading[accountId]).toBe(true);
      finish({ futures, fetchedAt });
      await a;
      expect(h.api).toHaveBeenLastCalledWith('cloud:offline-test', 'holdings.mexc.futures', { accountId });
      expect(store.getSnapshot().accountFutures[accountId]).toEqual(futures);
      expect(store.getSnapshot().futuresUpdatedAt[accountId]).toBe(fetchedAt);
      expect(store.getSnapshot().state).toEqual(state);
      expect(h.accept).not.toHaveBeenCalled();
      await store.loadFutures(accountId);
      expect(h.api).toHaveBeenCalledTimes(2);
      vi.advanceTimersByTime(60000);
      h.api.mockResolvedValue({ futures, fetchedAt });
      await store.loadFutures(accountId);
      expect(h.api).toHaveBeenCalledTimes(3);
      expect(new MexcStore().getSnapshot().accountFutures).toEqual({});
    } finally {
      vi.useRealTimers();
    }
  });
  it.each(['network', 'unavailable', 'invalid-time', 'invalid-payload'])(
    'retains last successful values and time after %s',
    async (failure) => {
      vi.useFakeTimers();
      try {
        const store = new MexcStore();
        h.api.mockResolvedValueOnce(state);
        await store.load();
        h.api.mockResolvedValueOnce({ futures, fetchedAt });
        await store.loadFutures(accountId);
        vi.advanceTimersByTime(60000);
        if (failure === 'network') h.api.mockRejectedValueOnce(new Error(MEXC_ERRORS.limit));
        else
          h.api.mockResolvedValueOnce({
            futures:
              failure === 'unavailable'
                ? { status: 'unavailable' }
                : failure === 'invalid-payload'
                  ? {}
                  : futures,
            fetchedAt: failure === 'invalid-time' ? 'not a timestamp' : '2026-11-08T00:01:00Z',
          });
        await store.loadFutures(accountId);
        expect(store.getSnapshot().accountFutures[accountId]).toEqual(futures);
        expect(store.getSnapshot().futuresUpdatedAt[accountId]).toBe(fetchedAt);
        expect(store.getSnapshot().futuresErrors[accountId]).toBe(true);
        expect(store.getSnapshot().futuresLoading[accountId]).toBe(false);
        expect(store.getSnapshot().accountErrors).toEqual({});
        expect(store.getSnapshot().state.retryAt).toBeNull();
        expect(h.accept).not.toHaveBeenCalled();
      } finally {
        vi.useRealTimers();
      }
    },
  );
  it('does not let an older read replace a newer manual sync or resurrect disconnected data', async () => {
    const store = new MexcStore();
    h.api.mockResolvedValueOnce(state);
    await store.load();
    let finish!: (value: unknown) => void;
    h.api.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const pending = store.loadFutures(accountId);
    const newer = {
      ...futures,
      account: { ...futures.account, equity: '200' },
    };
    h.api.mockResolvedValueOnce({
      ...state,
      document: {},
      futures: newer,
      futuresFetchedAt: fetchedAt,
    });
    await store.call('sync', { accountId });
    finish({ futures, fetchedAt });
    await pending;
    expect(store.getSnapshot().accountFutures[accountId]).toEqual(newer);
    h.api.mockResolvedValueOnce({ ...state, document: {} });
    await store.call('disconnect', { accountId });
    expect(store.getSnapshot().accountFutures[accountId]).toBeUndefined();
    expect(store.getSnapshot().futuresUpdatedAt[accountId]).toBeUndefined();
  });
  it('ignores in-flight responses and new timer reads after an identity change', async () => {
    const store = new MexcStore();
    h.api.mockResolvedValueOnce(state);
    await store.load();
    let finish!: (value: unknown) => void;
    h.api.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const pending = store.loadFutures(accountId);
    h.context = 'cloud:another-identity';
    finish({ futures, fetchedAt });
    await pending;
    await store.loadFutures(accountId);
    expect(h.api).toHaveBeenCalledTimes(2);
    expect(store.getSnapshot().accountFutures).toEqual({});
    expect(store.getSnapshot().futuresUpdatedAt).toEqual({});
    expect(new MexcStore().getSnapshot().accountFutures).toEqual({});
    expect(h.accept).not.toHaveBeenCalled();
  });
  it('retains successful futures when spot sync returns unavailable, but clears them for a newly connected key', async () => {
    const store = new MexcStore();
    h.api.mockResolvedValueOnce({
      ...state,
      document: {},
      futures,
      futuresFetchedAt: fetchedAt,
    });
    await store.call('sync', { accountId });
    h.api.mockResolvedValue({
      ...state,
      document: {},
      futures: { status: 'unavailable' },
    });
    await store.call('sync', { accountId });
    expect(store.getSnapshot().accountFutures[accountId]).toEqual(futures);
    expect(store.getSnapshot().futuresUpdatedAt[accountId]).toBe(fetchedAt);
    await store.call('connect', { account: { id: accountId } });
    expect(store.getSnapshot().accountFutures[accountId]).toEqual({
      status: 'unavailable',
    });
    expect(store.getSnapshot().futuresUpdatedAt[accountId]).toBeUndefined();
  });
});

describe('spot quote refresh never blanks a held asset', () => {
  const q = { price: '10', previous: null, source: 'MEXC fixture', asOf: '2026-11-08T00:00:00Z' };
  const withQuote = { ...state, connections: [{ ...state.connections[0]!, quotes: { 'BTC/USDT': q } }] };
  it('keeps the old price while sync is pending, after an omitted quote and after a metadata reload, then recovers', async () => {
    const store = new MexcStore();
    h.api.mockResolvedValueOnce(withQuote);
    await store.load();
    let finish!: (raw: unknown) => void;
    h.api.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const pending = store.call('sync', { accountId });
    expect(store.getSnapshot().busy).toBe(true);
    expect(store.getSnapshot().state.connections[0]!.quotes['BTC/USDT']).toEqual(q);
    finish({ ...state, document: {} });
    await pending;
    expect(store.getSnapshot().state.connections[0]!.quotes['BTC/USDT']).toEqual(q);
    expect(store.getSnapshot().accountRetainedQuotes[accountId]).toEqual(['BTC/USDT']);
    h.api.mockResolvedValueOnce(state);
    await store.load();
    expect(store.getSnapshot().state.connections[0]!.quotes['BTC/USDT']!.asOf).toBe(q.asOf);
    h.api.mockResolvedValueOnce({
      ...withQuote,
      document: {},
      connections: [
        {
          ...withQuote.connections[0]!,
          quotes: { 'BTC/USDT': { ...q, price: '12', asOf: '2026-11-08T00:01:00Z' } },
        },
      ],
    });
    await store.call('sync', { accountId });
    expect(store.getSnapshot().state.connections[0]!.quotes['BTC/USDT']!.price).toBe('12');
    expect(store.getSnapshot().accountRetainedQuotes[accountId]).toEqual([]);
  });
  it('does not carry a quote into a replacement key or a disconnected account', async () => {
    const store = new MexcStore();
    h.api.mockResolvedValueOnce(withQuote);
    await store.load();
    h.api.mockResolvedValueOnce({ ...state, document: {} });
    await store.call('connect', { account: { id: accountId } });
    expect(store.getSnapshot().state.connections[0]!.quotes).toEqual({});
    h.api.mockResolvedValueOnce({ ...state, connections: [], document: {} });
    await store.call('disconnect', { accountId });
    expect(store.getSnapshot().accountRetainedQuotes).toEqual({});
  });
});
