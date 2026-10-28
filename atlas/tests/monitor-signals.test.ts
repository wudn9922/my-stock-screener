import { beforeEach, describe, expect, it, vi } from 'vitest';
const state = vi.hoisted(() => ({ context: 'A', listeners: new Set<() => void>(), initialize: async () => ({}),
  identity: { userId: 'A', idToken: () => 'secret-in-body-only' } }));
vi.mock('../src/auth/access', () => ({
  accessState: { subscribe: (listener: () => void) => { state.listeners.add(listener); return () => state.listeners.delete(listener); } },
  initializeAccess: () => state.initialize(), requireCloudAccess: async () => state.identity, currentIdentity: () => state.identity,
}));
vi.mock('../src/auth/storage', () => ({ getStorageContextKey: () => state.context }));
vi.mock('../src/app/dataSources', () => ({ searchStocks: vi.fn(async () => []) }));
import { fetchMonitorSignals, groupMonitorSignals, latestSessionSignals, monitorSnapshotSchema, monitorSymbol } from '../src/report/monitorSignals';
import type { MonitorSignal } from '../../supabase/functions/monitor-signals/contract';
const signal = (extra: Partial<MonitorSignal> = {}): MonitorSignal => ({ groupId: 'us_g1', groupName: '美股權值', ticker: 'NVDA', name: 'NVIDIA',
  market: 'US', direction: 'breakout_up', maPeriod: 20, sentAt: '2026-10-27T15:00:00.000Z', price: 150, maValue: 148, volumeRatio: 1.2, ...extra });
const snapshot = (signals = [signal()]) => ({ version: 1, generatedAt: '2026-10-28T10:00:00.000Z', windowStart: '2026-09-28T10:00:00.000Z', truncated: false, signals });
beforeEach(() => { state.context = 'A'; state.initialize = async () => ({}); state.listeners.clear(); });
describe('monitor signal list', () => {
  it('validates dates, directions, amounts and response bounds', () => {
    expect(monitorSnapshotSchema.safeParse(snapshot()).success).toBe(true);
    for (const patch of [{ direction: 'failed' }, { sentAt: 'not a date' }, { maPeriod: 0 }, { price: -1 }])
      expect(monitorSnapshotSchema.safeParse(snapshot([signal(patch as Partial<MonitorSignal>)])).success).toBe(false);
  });
  it('groups by market/group, coalesces MAs and keeps newest duplicate only', () => {
    const groups = groupMonitorSignals([signal(), signal({ maPeriod: 50, direction: 'breakout_down' }), signal({ sentAt: '2026-10-26T15:00:00.000Z' }),
      signal({ groupId: 'custom_1' }), signal({ market: 'TW', ticker: '2330.TW' })], new Map([['US:NVDA', 'NVDA']]));
    expect(groups).toHaveLength(3); expect(groups[0]!.stocks).toHaveLength(1);
    expect(groups[0]).toMatchObject({ up: 1, down: 1 }); expect(groups[0]!.stocks[0]!.maPeriods).toEqual([20, 50]);
    expect(groups[0]!.stocks[0]!.symbol).toBe('NVDA');
  });
  it('keeps only each market\'s latest exchange-local session', () => {
    const { signals, sessions } = latestSessionSignals([
      // US 10/27 session: 15:00Z and 23:30Z are both 10/27 in New York (the latter is past midnight in Taipei).
      signal({ ticker: 'NVDA', sentAt: '2026-10-27T15:00:00.000Z' }), signal({ ticker: 'AAPL', sentAt: '2026-10-27T23:30:00.000Z' }),
      signal({ ticker: 'MSFT', sentAt: '2026-10-26T15:00:00.000Z' }),
      // TW 10/28 session: 01:30Z is 09:30 in Taipei; 10/27 is the previous session.
      signal({ market: 'TW', ticker: '2330.TW', sentAt: '2026-10-28T01:30:00.000Z' }), signal({ market: 'TW', ticker: '2317.TW', sentAt: '2026-10-27T05:00:00.000Z' }),
    ]);
    expect(sessions).toEqual({ US: '2026-10-27', TW: '2026-10-28' });
    expect(signals.map(row => row.ticker)).toEqual(['NVDA', 'AAPL', '2330.TW']);
    // A closed market keeps its last session instead of emptying.
    expect(latestSessionSignals([signal({ sentAt: '2026-10-23T15:00:00.000Z' })]).signals).toHaveLength(1);
    expect(latestSessionSignals([])).toEqual({ signals: [], sessions: {} });
  });
  it('resolves bare Taiwan tickers against the directory, never guesses listing', async () => {
    const search = vi.fn(async () => [{ symbol: '6488.TWO', market: 'TW' as const, name: '環球晶', exchange: 'TPEx' }]);
    expect(await monitorSymbol('6488', 'TW', search)).toBe('6488.TWO');
    expect(await monitorSymbol('9999', 'TW', search)).toBeNull();
    expect(await monitorSymbol('BRK.B', 'US', search)).toBe('BRK-B');
    expect(await monitorSymbol('2330.TW', 'TW', search)).toBe('2330.TW');
  });
  it('sends only a POST body, no query or storage, and validates the reply', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ ok: true, data: snapshot() })));
    const result = await fetchMonitorSignals(undefined, fetcher);
    expect(result.status).toBe('ok');
    expect(result.status === 'ok' && result.sessions).toEqual({ US: '2026-10-27' });
    const [url, init] = fetcher.mock.calls[0]!; expect(String(url)).not.toContain('?'); expect(String(url)).not.toContain('secret');
    expect(init).toMatchObject({ method: 'POST', cache: 'no-store' });
    expect(JSON.parse(String(init?.body))).toEqual({ action: 'list', idToken: 'secret-in-body-only' });
  });
  it('does not send the old identity if context changes while initialization waits', async () => {
    let release!: () => void;
    state.initialize = () => new Promise(resolve => { release = () => resolve({}); });
    const fetcher = vi.fn<typeof fetch>();
    const request = fetchMonitorSignals(undefined, fetcher);
    state.context = 'B'; state.listeners.forEach(listener => listener()); release();
    expect((await request).status).toBe('error'); expect(fetcher).not.toHaveBeenCalled();
  });
  it('does not display a late response after account switch', async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => {
      state.context = 'B'; state.listeners.forEach(listener => listener());
      return new Response(JSON.stringify({ ok: true, data: snapshot() }));
    });
    expect((await fetchMonitorSignals(undefined, fetcher)).status).toBe('error');
  });
  it.each([404, 500])('handles missing/unavailable service (%s) without exposing server errors', async status => {
    const result = await fetchMonitorSignals(undefined, vi.fn<typeof fetch>().mockResolvedValue(new Response('private service details', { status })));
    expect(result.status).toBe('error'); expect(JSON.stringify(result)).not.toContain('private service details');
  });
});
