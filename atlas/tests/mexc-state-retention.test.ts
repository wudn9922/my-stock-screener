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
