import { beforeEach, describe, expect, it, vi } from 'vitest';
import { startLineIdentity } from '../src/cloud/lineIdentity';
import { settingsApi } from '../src/cloud/settingsApi';
import { refreshCloudWatchlist, pushCloudWatchlist } from '../src/watchlists/cloud';
import { createWatchlist, recordWatchlistData, visitWatchlist } from '../src/watchlists/model';

vi.mock('../src/cloud/lineIdentity', () => ({ startLineIdentity: vi.fn() }));
vi.mock('../src/cloud/settingsApi', () => ({ settingsApi: vi.fn() }));
const identity = { userId: 'U-test', idToken: () => 'test-token' };
const members = (...symbols: string[]) => symbols.map((symbol) => ({ symbol, name: symbol }));
const linked = () => ({
  ...visitWatchlist(createWatchlist({ name: '成長', market: 'US', group: 'universe-us', filters: '' }), members('AAPL'), '2026-10-08', 1)
    .list,
  cloud: { userId: identity.userId, version: '2026-10-08T09:00:00Z' },
});
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(startLineIdentity).mockResolvedValue(identity);
});

describe('linked cloud observation sync', () => {
  it('merges daily observations without losing the actual previous-opening comparison', async () => {
    const local = linked();
    const remote = recordWatchlistData(local, members('AAPL', 'NVDA'), '2026-10-09', '2026-10-08');
    vi.mocked(settingsApi).mockResolvedValue([{ id: local.id, definition: remote, updated_at: '2026-10-09T09:00:00Z' }]);
    const refreshed = await refreshCloudWatchlist(local);
    const opened = visitWatchlist(refreshed, members('AAPL', 'NVDA'), '2026-10-09', 2);
    expect(opened.previousDate).toBe('2026-10-08');
    expect(opened.entered.map((entry) => entry.symbol)).toEqual(['NVDA']);
    expect(opened.members[0].streak).toBe(2);
    expect(refreshed.cloud?.version).toBe('2026-10-09T09:00:00Z');
  });
  it('does not automatically access a previously linked account after the identity changes', async () => {
    const local = linked();
    vi.mocked(startLineIdentity).mockResolvedValue({ ...identity, userId: 'U-other' });
    expect(await refreshCloudWatchlist(local)).toBe(local);
    expect(settingsApi).not.toHaveBeenCalled();
  });
  it('retains unsynced local rules and uses the expected server version rather than overwriting a conflict', async () => {
    const local = linked();
    const edited = { ...local, filters: 'chg:3:', updatedAt: local.updatedAt + 1 };
    vi.mocked(settingsApi).mockResolvedValue([{ id: local.id, definition: local, updated_at: '2026-10-09T09:00:00Z' }]);
    expect(await refreshCloudWatchlist(edited)).toBe(edited);
    vi.mocked(settingsApi).mockRejectedValueOnce(new Error('409 version conflict'));
    await expect(pushCloudWatchlist(edited)).rejects.toThrow('409');
    expect(edited.cloud.version).toBe('2026-10-08T09:00:00Z');
    expect(settingsApi).toHaveBeenLastCalledWith('watchlists.save', { definition: edited, expectedUpdatedAt: edited.cloud.version });
  });
});
