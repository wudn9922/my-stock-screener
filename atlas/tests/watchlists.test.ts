import { describe, expect, it } from 'vitest';
import { createWatchlist, evaluateWatchlist, recordWatchlistData, visitWatchlist } from '../src/watchlists/model';
import { WatchlistStore, WATCHLIST_STORAGE_KEY } from '../src/watchlists/store';
import { validateWatchlist } from '../../supabase/functions/user-settings/watchlists';
import { parseMovingAverages } from '../src/cloud/settingsApi';

const members = (...symbols: string[]) => symbols.map((symbol) => ({ symbol, name: symbol }));
const make = () => createWatchlist({ name: '成長股', market: 'US', group: 'universe-us', filters: 'rs:80' });
const storage = () => {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
  };
};

describe('saved-list observations', () => {
  it('compares with the previous opening, survives reload, and never double-counts one data day', () => {
    const db = storage();
    const store = new WatchlistStore(db);
    const first = visitWatchlist(make(), members('AAPL', 'MSFT'), '2026-10-16', 1);
    expect(first.firstVisit).toBe(true);
    expect(first.entered).toEqual([]);
    store.save(first.list);
    const reloaded = new WatchlistStore(db).getSnapshot().document.lists[0];
    const next = visitWatchlist(reloaded, members('MSFT', 'NVDA'), '2026-10-19', 2);
    expect(next.entered.map((entry) => entry.symbol)).toEqual(['NVDA']);
    expect(next.exited.map((entry) => entry.symbol)).toEqual(['AAPL']);
    expect(next.members.map((entry) => entry.streak)).toEqual([2, 1]);
    const repeat = visitWatchlist(next.list, members('MSFT', 'NVDA'), '2026-10-19', 3);
    expect(repeat.entered).toEqual([]);
    expect(repeat.members.map((entry) => entry.streak)).toEqual([2, 1]);
  });
  it('daily collection does not advance the last-opening baseline and handles a source-confirmed holiday', () => {
    let list = visitWatchlist(make(), members('AAPL'), '2026-10-09', 1).list;
    list = recordWatchlistData(list, members('AAPL', 'MSFT'), '2026-10-13', '2026-10-09');
    list = recordWatchlistData(list, members('MSFT'), '2026-10-14', '2026-10-13');
    expect(list.lastOpenedAt).toBe(1);
    const opened = visitWatchlist(list, members('MSFT'), '2026-10-14', 4);
    expect(opened.previousDate).toBe('2026-10-09');
    expect(opened.entered.map((entry) => entry.symbol)).toEqual(['MSFT']);
    expect(opened.exited.map((entry) => entry.symbol)).toEqual(['AAPL']);
    expect(opened.members[0].streak).toBe(2);
  });
  it('does not invent continuity across missing sessions or overwrite newer observations with old data', () => {
    const first = visitWatchlist(make(), members('AAPL'), '2026-10-12').list;
    expect(visitWatchlist(first, members('AAPL'), '2026-10-14').members[0].streak).toBe(1);
    expect(() => visitWatchlist(first, [], '2026-10-09')).toThrow('更舊');
    expect(() => visitWatchlist(first, [], '2026-02-31')).toThrow();
  });
  it('refuses to evaluate an unavailable requested metric instead of producing a false empty/complete result', () => {
    expect(() => evaluateWatchlist(make(), [], null)).toThrow();
  });
});

describe('durable list storage and server validation', () => {
  it('keeps separate lists and rejects corrupt imports without replacing a valid document', () => {
    const db = storage(),
      store = new WatchlistStore(db);
    const a = make(),
      b = { ...make(), name: '另一組' };
    store.save(a);
    store.save(b);
    const before = store.exportJson();
    expect(store.getSnapshot().document.lists).toHaveLength(2);
    expect(() => store.importJson('{"version":1,"lists":[{}]}')).toThrow();
    expect(store.exportJson()).toBe(before);
    store.remove(a.id);
    expect(store.getSnapshot().document.lists[0].id).toBe(b.id);
  });
  it('preserves raw corrupt data and leaves memory unchanged when quota writes fail', () => {
    const db = storage();
    db.setItem(WATCHLIST_STORAGE_KEY, 'broken');
    const broken = new WatchlistStore(db);
    expect(broken.getSnapshot().error).toBeTruthy();
    expect(() => broken.save(make())).toThrow();
    expect(broken.exportRaw()).toBe('broken');
    const full = new WatchlistStore({
      getItem: () => null,
      setItem: () => {
        throw new Error('quota');
      },
    });
    expect(() => full.save(make())).toThrow('儲存失敗');
    expect(full.getSnapshot().document.lists).toEqual([]);
  });
  it('sanitizes ownership fields, validates all histories, and rejects oversized or malformed monitor settings', () => {
    const list = visitWatchlist(make(), members('AAPL'), '2026-10-19').list;
    const result = validateWatchlist({ ...list, line_user_id: 'other-user', cloud: { userId: 'other-user', version: 'anything' } });
    expect(result).not.toHaveProperty('line_user_id');
    expect(result).not.toHaveProperty('cloud');
    expect(result.lastViewed?.members).toHaveLength(1);
    expect(() => validateWatchlist({ ...list, maList: [20, 20] })).toThrow();
    expect(() => validateWatchlist({ ...list, monitorBreakouts: 'true' })).toThrow();
    expect(() => validateWatchlist({ ...list, history: [...list.history, ...list.history] })).toThrow();
    expect(parseMovingAverages('20，50, 200')).toEqual([20, 50, 200]);
    expect(() => parseMovingAverages('20,20')).toThrow();
  });
});
