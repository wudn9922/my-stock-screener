import { describe, expect, it } from 'vitest';
import { parseRoute, sameRoute, serializeRoute, unwrapLiffState } from '../src/app/routes';

const route = (search: string, fallback: Parameters<typeof parseRoute>[1] = 'markets') => parseRoute(search, fallback).route;

describe('site routes', () => {
  it('lands on the default page with no parameters, without rewriting the URL', () => {
    expect(parseRoute('', 'markets')).toEqual({ route: { page: 'markets', market: 'tw' }, rewrite: false });
    expect(parseRoute('?', 'chart')).toEqual({ route: { page: 'chart' }, rewrite: false });
  });

  it('parses every page and its parameters', () => {
    expect(route('?page=markets&market=us')).toEqual({ page: 'markets', market: 'us' });
    expect(route('?page=markets&market=JP')).toEqual({ page: 'markets', market: 'jp' });
    expect(route('?page=markets&market=cn')).toEqual({ page: 'markets', market: 'tw' });
    expect(route('?page=world')).toEqual({ page: 'world' });
    expect(route('?page=screener&group=tw_g1')).toEqual({ page: 'screener', group: 'tw_g1' });
    expect(route('?page=screener&group=<script>')).toEqual({ page: 'screener' });
    expect(route('?page=chart&symbol=2330.TW&tf=1D')).toEqual({ page: 'chart', symbol: '2330.TW', tf: '1D' });
    expect(route('?page=chart&symbol=nvda&tf=1w')).toEqual({ page: 'chart', symbol: 'NVDA', tf: '1W' });
    expect(route('?page=chart&symbol=^TWII&tf=bogus')).toEqual({ page: 'chart', symbol: '^TWII' });
    expect(route('?page=nope')).toEqual({ page: 'markets', market: 'tw' });
  });

  it('keeps legacy report links (?symbol=&tf=) working as chart links', () => {
    expect(route('?symbol=2330.TW&tf=1D')).toEqual({ page: 'chart', symbol: '2330.TW', tf: '1D' });
    expect(route('?symbol=%5ETWII&timeframe=1W')).toEqual({ page: 'chart', symbol: '^TWII', tf: '1W' });
    expect(route('?symbol=2330')).toEqual({ page: 'chart', symbol: '2330' });
    expect(parseRoute('?symbol=2330.TW&tf=1D').rewrite).toBe(true);
  });

  it('unwraps LINE LIFF liff.state (path + query, encoded once or twice)', () => {
    expect(route(`?liff.state=${encodeURIComponent('/atlas/?page=world')}`)).toEqual({ page: 'world' });
    expect(route(`?liff.state=${encodeURIComponent('?symbol=2330.TW&tf=1W')}`)).toEqual({ page: 'chart', symbol: '2330.TW', tf: '1W' });
    expect(route(`?liff.state=${encodeURIComponent(encodeURIComponent('/?page=screener&group=us_g1'))}`)).toEqual({ page: 'screener', group: 'us_g1' });
    expect(route(`?liff.state=${encodeURIComponent('/my-stock-screener/atlas/world')}`)).toEqual({ page: 'world' });
    expect(route(`?liff.state=${encodeURIComponent('/report/index.html#top')}`)).toEqual({ page: 'markets', market: 'tw' });
    expect(route(`?liff.state=${encodeURIComponent('?page=markets&market=kr')}&liff.referrer=x`)).toEqual({ page: 'markets', market: 'kr' });
    // liff.state wins over stray outer parameters and is always rewritten away.
    expect(parseRoute(`?page=world&liff.state=${encodeURIComponent('?page=chart&symbol=NVDA')}`)).toEqual({
      route: { page: 'chart', symbol: 'NVDA' },
      rewrite: true,
    });
    const unwrapped = unwrapLiffState(new URLSearchParams(`liff.state=${encodeURIComponent('/x?page=world#hash')}&utm=1`));
    expect(unwrapped.path).toBe('/x');
    expect(Object.fromEntries(unwrapped.params)).toEqual({ utm: '1', page: 'world' });
  });

  it('serializes canonically and round-trips', () => {
    const routes = [
      { page: 'markets', market: 'eu' },
      { page: 'world' },
      { page: 'screener', group: 'tw_all' },
      { page: 'screener' },
      { page: 'chart', symbol: '^TWII', tf: '1D' },
      { page: 'chart' },
    ] as const;
    for (const r of routes) {
      const search = serializeRoute(r);
      expect(parseRoute(search)).toEqual({ route: r, rewrite: false });
    }
    expect(serializeRoute({ page: 'chart', symbol: '^TWII', tf: '1D' })).toBe('?page=chart&symbol=^TWII&tf=1D');
    expect(sameRoute({ page: 'world' }, { page: 'world' })).toBe(true);
    expect(sameRoute({ page: 'chart', symbol: 'A' }, { page: 'chart', symbol: 'B' })).toBe(false);
  });
});
