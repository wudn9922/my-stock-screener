import { describe, expect, it } from 'vitest';
import { parseRoute, serializeRoute, type Route } from '../src/app/routes';

const id = '12345678-1234-4234-8234-123456789abc';
type Case = { input: string; route: Route; canonical: string; rewrite: boolean };
const cases: Case[] = [
  { input: '?page=console', route: { page: 'console' }, canonical: '?page=console', rewrite: false },
  { input: '?page=settings', route: { page: 'console' }, canonical: '?page=console', rewrite: true },
  { input: '?page=watchlist', route: { page: 'watchlist' }, canonical: '?page=watchlist', rewrite: false },
  { input: '?page=watchlists', route: { page: 'watchlist' }, canonical: '?page=watchlist', rewrite: true },
  { input: `?page=console&list=${id}`, route: { page: 'watchlist', list: id }, canonical: `?page=watchlist&list=${id}`, rewrite: true },
  { input: `?page=watchlists&list=${id}`, route: { page: 'watchlist', list: id }, canonical: `?page=watchlist&list=${id}`, rewrite: true },
  { input: `?page=watchlist&list=${id}`, route: { page: 'watchlist', list: id }, canonical: `?page=watchlist&list=${id}`, rewrite: false },
  { input: '?page=watchlist&list=invalid', route: { page: 'watchlist' }, canonical: '?page=watchlist', rewrite: true },
  ...(['tw', 'jp', 'kr', 'eu', 'us'] as const).flatMap(market => [
    { input: `?page=markets&market=${market}`, route: { page: 'markets' as const, market }, canonical: `?page=markets&market=${market}`, rewrite: false },
    { input: `?page=market&market=${market}`, route: { page: 'markets' as const, market }, canonical: `?page=markets&market=${market}`, rewrite: true },
  ]),
  { input: '?page=world', route: { page: 'world' }, canonical: '?page=world', rewrite: false },
  { input: '?page=world&view=rates', route: { page: 'world' }, canonical: '?page=world', rewrite: true },
  { input: '?page=rates', route: { page: 'world' }, canonical: '?page=world', rewrite: true },
  { input: '?page=bonds', route: { page: 'world' }, canonical: '?page=world', rewrite: true },
  ...(['indices', 'commodities', 'fx'] as const).map(view => ({
    input: `?page=world&view=${view}`, route: { page: 'world' as const, view }, canonical: `?page=world&view=${view}`, rewrite: false,
  })),
  { input: '?page=themes', route: { page: 'themes' }, canonical: '?page=themes', rewrite: false },
  { input: '?page=themes&theme=optical', route: { page: 'themes', theme: 'optical' }, canonical: '?page=themes&theme=optical', rewrite: false },
  { input: '?page=theme&theme=optical', route: { page: 'themes', theme: 'optical' }, canonical: '?page=themes&theme=optical', rewrite: true },
  { input: '?page=themes&market=us', route: { page: 'themes' }, canonical: '?page=themes', rewrite: true },
  { input: '?page=themes&market=tw', route: { page: 'themes', market: 'tw' }, canonical: '?page=themes&market=tw', rewrite: false },
  { input: '?page=themes&v=map', route: { page: 'themes', v: 'map' }, canonical: '?page=themes&v=map', rewrite: false },
  { input: '?page=themes&market=tw&v=map&theme=foundry', route: { page: 'themes', market: 'tw', v: 'map', theme: 'foundry' }, canonical: '?page=themes&market=tw&v=map&theme=foundry', rewrite: false },
  { input: '?page=themes&view=map', route: { page: 'themes', v: 'map' }, canonical: '?page=themes&v=map', rewrite: true },
  { input: '?page=screener', route: { page: 'screener' }, canonical: '?page=screener', rewrite: false },
  { input: '?page=screener&group=us_g1', route: { page: 'screener', group: 'us_g1' }, canonical: '?page=screener&group=us_g1', rewrite: false },
  { input: '?page=screener&group=line-trending-us&f=vol:100000', route: { page: 'screener', group: 'line-trending-us', f: 'vol:100000' }, canonical: '?page=screener&group=line-trending-us&f=vol%3A100000', rewrite: true },
  { input: '?page=chart', route: { page: 'chart' }, canonical: '?page=chart', rewrite: false },
  { input: '?page=chart&symbol=2330.TW&tf=1D', route: { page: 'chart', symbol: '2330.TW', tf: '1D' }, canonical: '?page=chart&symbol=2330.TW&tf=1D', rewrite: false },
  { input: '?page=chart&symbol=NVDA&tf=1W', route: { page: 'chart', symbol: 'NVDA', tf: '1W' }, canonical: '?page=chart&symbol=NVDA&tf=1W', rewrite: false },
  { input: '?symbol=2330.TW&tf=1D', route: { page: 'chart', symbol: '2330.TW', tf: '1D' }, canonical: '?page=chart&symbol=2330.TW&tf=1D', rewrite: true },
  { input: '?symbol=%5ETWII&timeframe=1W', route: { page: 'chart', symbol: '^TWII', tf: '1W' }, canonical: '?page=chart&symbol=^TWII&tf=1W', rewrite: true },
  { input: '?page=financials', route: { page: 'financials' }, canonical: '?page=financials', rewrite: false },
  ...(['q', 'a'] as const).map(p => ({ input: `?page=financials&symbol=AAPL&tf=1W&p=${p}`,
    route: { page: 'financials' as const, symbol: 'AAPL', tf: '1W' as const, p }, canonical: `?page=financials&symbol=AAPL&tf=1W&p=${p}`, rewrite: false })),
];

describe('phase 1 navigation URL inventory', () => {
  it.each(cases)('$input → $canonical', ({ input, route, canonical, rewrite }) => {
    expect(parseRoute(input)).toEqual({ route, rewrite });
    expect(serializeRoute(route)).toBe(canonical);
    expect(parseRoute(canonical)).toEqual({ route, rewrite: false });
  });
  for (const form of ['query', 'path', 'double'] as const) {
    it.each(cases)(`${form} LIFF: $input`, ({ input, route, canonical }) => {
      const inner = form === 'query' ? input : `/my-stock-screener/atlas/${input}`;
      const wrapped = `?liff.state=${encodeURIComponent(form === 'double' ? encodeURIComponent(inner) : inner)}`;
      expect(parseRoute(wrapped)).toEqual({ route, rewrite: true });
      expect(serializeRoute(parseRoute(wrapped).route)).toBe(canonical);
    });
  }
  it('keeps the original landing defaults and empty-query rewrite flags', () => {
    expect(parseRoute('', 'markets')).toEqual({ route: { page: 'markets', market: 'tw' }, rewrite: false });
    expect(parseRoute('?', 'chart')).toEqual({ route: { page: 'chart' }, rewrite: false });
  });
  it('unwraps legacy path-only list/settings aliases', () => {
    for (const [path, page] of [['watchlists', 'watchlist'], ['watchlist', 'watchlist'], ['settings', 'console'], ['console', 'console']] as const) {
      expect(parseRoute(`?liff.state=${encodeURIComponent(`/my-stock-screener/atlas/${path}`)}`)).toEqual({ route: { page }, rewrite: true });
    }
  });
});
