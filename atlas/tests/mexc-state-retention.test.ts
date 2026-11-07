import { beforeEach, describe, expect, it, vi } from 'vitest';
const h = vi.hoisted(() => ({ api: vi.fn(), accept: vi.fn() }));
vi.mock('../src/auth/access', () => ({ accessState: { getSnapshot: () => ({ storageMode: 'cloud' }) } }));
vi.mock('../src/auth/storage', () => ({ getStorageContextKey: () => 'cloud:offline-test' }));
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

describe('RAM-only futures data', () => {
  it('isolates accounts, updates on sync, retains on apply/load, clears on disconnect and never passes futures into holdings', async () => {
    const document = { version: 1, accounts: [] };
    h.api.mockResolvedValue({ ...state, document, futures: { status: 'no-permission' } });
    const store = new MexcStore();
    await store.call('connect', { account: { id: accountId } });
    expect(store.getSnapshot().accountFutures[accountId]).toEqual({ status: 'no-permission' });
    const other = '33333333-3333-4333-8333-333333333333';
    h.api.mockResolvedValue({ ...state, document, futures: { status: 'unavailable' } });
    await store.call('sync', { accountId: other });
    expect(store.getSnapshot().accountFutures[accountId]).toEqual({ status: 'no-permission' });
    expect(store.getSnapshot().accountFutures[other]).toEqual({ status: 'unavailable' });
    await store.load(); await store.call('apply', { accountId });
    expect(store.getSnapshot().accountFutures[accountId]).toEqual({ status: 'no-permission' });
    expect(store.getSnapshot().state).not.toHaveProperty('futures');
    for (const [doc] of h.accept.mock.calls) expect(doc).toBe(document);
    for (const call of h.api.mock.calls) if (call[2]) expect(call[2]).not.toHaveProperty('futures');
    await store.call('disconnect', { accountId: other });
    expect(store.getSnapshot().accountFutures[other]).toBeUndefined();
    expect(new MexcStore().getSnapshot().accountFutures).toEqual({});
  });
  it('a malformed futures payload becomes unavailable without failing spot or displaying unchecked fields', async () => {
    const document = { version: 1, accounts: [] };
    h.api.mockResolvedValue({ ...state, document, futures: { status: 'ok', account: {}, positions: [], skippedPositions: 0 } });
    const store = new MexcStore();
    await store.call('sync', { accountId });
    expect(store.getSnapshot().accountFutures[accountId]).toEqual({ status: 'unavailable' });
    expect(h.accept).toHaveBeenCalledExactlyOnceWith(document);
    expect(store.getSnapshot().accountErrors).toEqual({});
  });
});
