import { describe, expect, it, vi } from 'vitest';
vi.mock('../src/cloud/settingsApi', () => ({ settingsApi: vi.fn() }));
vi.mock('../src/auth/storage', () => ({ getStorageContextKey: () => 'account-a' }));
import { customFromCurrent, DEFAULT_ALERT_SETTINGS, listAlertFlags, symbolAlertKinds, withAllLists, withListAlert } from '../src/lists/alertSettings';
import type { UnifiedList } from '../src/lists/unifiedLists';

const list = (key: string, kind: UnifiedList['kind'], monitored?: boolean): UnifiedList =>
  ({ key, kind, name: key, symbols: [], feedsAlerts: kind === 'group', editable: kind !== 'rules', monitored });
const group = list('group:tw_g1', 'group');
const other = list('group:custom_7', 'group');
const watched = list('watchlist:d93f0aa6-c873-4f97-8cf9-45a7b55b6439', 'watchlist', true);
const quiet = list('watchlist:11111111-1111-4111-8111-111111111111', 'watchlist', false);
const favorites = list('favorites', 'favorites');
const lists = [favorites, watched, quiet, group, other];

describe('per-list notification settings', () => {
  it('defaults to today: groups both, watchlists MA by their own switch, drawings everywhere, favourites never', () => {
    expect(DEFAULT_ALERT_SETTINGS.mode).toBe('all');
    expect(listAlertFlags(DEFAULT_ALERT_SETTINGS, group)).toEqual({ ma: true, drawing: true });
    expect(listAlertFlags(DEFAULT_ALERT_SETTINGS, watched)).toEqual({ ma: true, drawing: true });
    expect(listAlertFlags(DEFAULT_ALERT_SETTINGS, quiet)).toEqual({ ma: false, drawing: true });
    expect(listAlertFlags(DEFAULT_ALERT_SETTINGS, favorites)).toEqual({ ma: false, drawing: false });
  });

  it('turning 所有清單都通知 off keeps what is sent; per-list edits choose MA, drawing, both or none', () => {
    const custom = withAllLists(DEFAULT_ALERT_SETTINGS, lists, false);
    expect(custom.mode).toBe('custom');
    for (const entry of lists) expect(listAlertFlags(custom, entry)).toEqual(listAlertFlags(DEFAULT_ALERT_SETTINGS, entry));
    expect(Object.keys(custom.lists)).not.toContain('favorites');
    const drawingOnly = withListAlert(custom, lists, group, 'ma', false);
    expect(listAlertFlags(drawingOnly, group)).toEqual({ ma: false, drawing: true });
    const none = withListAlert(drawingOnly, lists, group, 'drawing', false);
    expect(listAlertFlags(none, group)).toEqual({ ma: false, drawing: false });
    // Editing from all-lists mode switches to custom first.
    expect(withListAlert(DEFAULT_ALERT_SETTINGS, lists, quiet, 'ma', true).lists[quiet.key]).toEqual({ ma: true, drawing: true });
    expect(withAllLists(none, lists, true).mode).toBe('all');
    expect(customFromCurrent(none, lists)).toBe(none);
  });

  it('a symbol notifies for a type when any of its lists enables it', () => {
    let settings = withAllLists(DEFAULT_ALERT_SETTINGS, lists, false);
    settings = withListAlert(settings, lists, group, 'drawing', false);
    settings = withListAlert(settings, lists, other, 'ma', false);
    expect([...symbolAlertKinds(settings, lists, new Set([group.key, other.key]))].sort()).toEqual(['drawing', 'ma']);
    expect([...symbolAlertKinds(settings, lists, new Set([group.key]))]).toEqual(['ma']);
    settings = withListAlert(settings, lists, group, 'ma', false);
    expect([...symbolAlertKinds(settings, lists, new Set([group.key, favorites.key]))]).toEqual([]);
  });
});
