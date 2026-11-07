import { afterEach, describe, expect, it } from 'vitest';
import {
  chartList, chartListFor, forgetChartLists, LIST_STATE_KEY, listPosition, listState, rememberChartList,
} from '../src/app/listContext';
import {
  forgetDestination, INITIAL_ROUTE_MEMORY, isKeptPage, navDefault, navDestination, navPageOf, rememberRoute,
  routeIsDefault, scrollIdentity, scrollSection,
} from '../src/app/routeMemory';

afterEach(() => forgetChartLists());

describe('chart list context', () => {
  it('keeps the displayed order, drops blanks and repeated symbols, and keeps per-item periods', () => {
    const list = chartList('  台股全市場 ', [
      { symbol: '2330.TW', name: '台積電', maList: [20, 60] },
      { symbol: '', name: 'blank' },
      { symbol: null, name: 'no symbol' },
      { symbol: '2317.TW', name: '2317.TW' },
      { symbol: '2330.TW', name: 'duplicate' },
      { symbol: 'NVDA' },
    ]);
    expect(list).toEqual({
      name: '台股全市場',
      items: [{ symbol: '2330.TW', name: '台積電', maList: [20, 60] }, { symbol: '2317.TW' }, { symbol: 'NVDA' }],
    });
    expect(chartList('', []).name).toBe('清單');
  });

  it('reports the position with neighbours, disabled ends and no position outside the list', () => {
    const list = chartList('L', [{ symbol: 'A' }, { symbol: 'B' }, { symbol: 'C' }]);
    expect(listPosition(list, 'A')).toEqual({ index: 0, total: 3, previous: null, next: { symbol: 'B' } });
    expect(listPosition(list, 'b')).toEqual({ index: 1, total: 3, previous: { symbol: 'A' }, next: { symbol: 'C' } });
    expect(listPosition(list, 'C')).toEqual({ index: 2, total: 3, previous: { symbol: 'B' }, next: null });
    expect(listPosition(list, 'Z')).toBeNull();
    expect(listPosition(null, 'A')).toBeNull();
    expect(listPosition(list, undefined)).toBeNull();
    expect(listPosition(chartList('one', [{ symbol: 'A' }]), 'A')).toEqual({ index: 0, total: 1, previous: null, next: null });
  });

  it('lives in memory only: the history state is an opaque key that a reload cannot resolve', () => {
    const list = chartList('自選', [{ symbol: 'A' }, { symbol: 'B' }]);
    const state = rememberChartList(list);
    expect(Object.keys(state)).toEqual([LIST_STATE_KEY]);
    expect(JSON.stringify(state)).not.toContain('自選');
    expect(chartListFor(state)).toBe(list);
    expect(listState({ ...state, other: 1 })).toEqual(state);
    // A reload keeps history.state but not this page's memory: no context, nothing guessed.
    forgetChartLists();
    expect(chartListFor(state)).toBeNull();
    expect(listState(state)).toBeNull();
    for (const value of [null, undefined, 'x', 1, {}, { [LIST_STATE_KEY]: 3 }]) expect(chartListFor(value)).toBeNull();
  });

  it('keeps a bounded number of recent lists', () => {
    const states = Array.from({ length: 40 }, (_, index) => rememberChartList(chartList(`L${index}`, [{ symbol: 'A' }])));
    expect(chartListFor(states[0])).toBeNull();
    expect(chartListFor(states.at(-1))?.name).toBe('L39');
  });
});

describe('route memory for kept pages', () => {
  it('remembers the last route of each main page and the last 市場 section', () => {
    let memory = rememberRoute(INITIAL_ROUTE_MEMORY, { page: 'screener', group: 'universe-tw', f: 'vol:1000000' });
    memory = rememberRoute(memory, { page: 'chart', symbol: '2330.TW' });
    memory = rememberRoute(memory, { page: 'watchlist', list: '0f0e0d0c-0b0a-4908-8706-050403020100' });
    memory = rememberRoute(memory, { page: 'world', view: 'fx' });
    memory = rememberRoute(memory, { page: 'themes', market: 'tw', v: 'map', theme: 'ai-chips' });
    expect(navDestination(memory, 'screener')).toEqual({ page: 'screener', group: 'universe-tw', f: 'vol:1000000' });
    expect(navDestination(memory, 'watchlist')).toEqual({ page: 'watchlist', list: '0f0e0d0c-0b0a-4908-8706-050403020100' });
    // The bottom 市場 reopens the last section; a theme detail is not reopened (as before).
    expect(navDestination(memory, 'markets')).toEqual({ page: 'themes', market: 'tw', v: 'map' });
    expect(memory.sections.world).toEqual({ page: 'world', view: 'fx' });
    expect(memory.pages.themes).toEqual({ page: 'themes', market: 'tw', v: 'map', theme: 'ai-chips' });
    expect(navDestination(memory, 'console')).toEqual({ page: 'console' });
    expect(navDestination(INITIAL_ROUTE_MEMORY, 'markets')).toEqual({ page: 'markets', market: 'tw' });
    expect(rememberRoute(memory, { page: 'world', view: 'fx' })).not.toBe(memory);
    const same = rememberRoute(memory, { page: 'themes', market: 'tw', v: 'map', theme: 'ai-chips' });
    expect(same).toBe(memory);
    expect(rememberRoute(memory, { page: 'financials', symbol: 'AAPL' })).toBe(memory);
  });

  it('judges default routes and forgets only the tapped destination', () => {
    expect(routeIsDefault({ page: 'screener' })).toBe(true);
    expect(routeIsDefault({ page: 'screener', group: 'tw_g1' })).toBe(false);
    expect(routeIsDefault({ page: 'screener', f: 'vol:1' })).toBe(false);
    expect(routeIsDefault({ page: 'watchlist' })).toBe(true);
    expect(routeIsDefault({ page: 'watchlist', list: '0f0e0d0c-0b0a-4908-8706-050403020100' })).toBe(false);
    expect(routeIsDefault({ page: 'markets', market: 'tw' })).toBe(true);
    expect(routeIsDefault({ page: 'markets', market: 'us' })).toBe(false);
    expect(routeIsDefault({ page: 'world' })).toBe(false);
    expect(routeIsDefault({ page: 'console' })).toBe(true);
    expect(routeIsDefault({ page: 'chart', symbol: 'A' })).toBe(false);
    let memory = rememberRoute(INITIAL_ROUTE_MEMORY, { page: 'screener', group: 'tw_g1', f: 'vol:1' });
    memory = rememberRoute(memory, { page: 'world', view: 'fx' });
    const screenerReset = forgetDestination(memory, 'screener');
    expect(navDestination(screenerReset, 'screener')).toEqual({ page: 'screener' });
    expect(navDestination(screenerReset, 'markets')).toEqual({ page: 'world', view: 'fx' });
    const marketsReset = forgetDestination(memory, 'markets');
    expect(navDestination(marketsReset, 'markets')).toEqual({ page: 'markets', market: 'tw' });
    expect(marketsReset.pages.world).toBeUndefined();
    expect(navDestination(marketsReset, 'screener')).toEqual({ page: 'screener', group: 'tw_g1', f: 'vol:1' });
    expect(navDefault('markets')).toEqual({ page: 'markets', market: 'tw' });
  });

  it('maps pages to destinations, kept pages and scroll identities', () => {
    expect(navPageOf('financials')).toBe('chart');
    expect(navPageOf('themes')).toBe('markets');
    expect(navPageOf('world')).toBe('markets');
    expect(navPageOf('screener')).toBe('screener');
    expect(isKeptPage('screener')).toBe(true);
    expect(isKeptPage('console')).toBe(true);
    expect(isKeptPage('chart')).toBe(false);
    expect(isKeptPage('financials')).toBe(false);
    // Filters do not change the identity (the page scroll is kept); a group or a watchlist does.
    expect(scrollIdentity({ page: 'screener', group: 'a', f: 'vol:1' })).toBe(scrollIdentity({ page: 'screener', group: 'a' }));
    expect(scrollIdentity({ page: 'screener', group: 'a' })).not.toBe(scrollIdentity({ page: 'screener', group: 'b' }));
    expect(scrollIdentity({ page: 'watchlist', list: '0f0e0d0c-0b0a-4908-8706-050403020100' })).not.toBe(scrollIdentity({ page: 'watchlist' }));
    // Switching watchlists inside the page does not jump to the top (as before).
    expect(scrollSection({ page: 'watchlist', list: '0f0e0d0c-0b0a-4908-8706-050403020100' })).toBe(scrollSection({ page: 'watchlist' }));
    expect(scrollSection({ page: 'markets', market: 'us' })).toBe('us');
    expect(scrollSection({ page: 'chart', symbol: 'NVDA' })).toBe('NVDA');
  });
});
