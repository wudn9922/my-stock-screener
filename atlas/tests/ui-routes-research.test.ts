import { describe, expect, it } from 'vitest';
import { parseRoute, sameRoute, serializeRoute } from '../src/app/routes';

const route = (search: string) => parseRoute(search, 'markets').route;
const liff = (state: string) => `?liff.state=${encodeURIComponent(state)}`;

describe('world rates view route', () => {
  it('keeps ?page=world unchanged (no view field at all)', () => {
    const parsed = parseRoute('?page=world');
    expect(parsed).toEqual({ route: { page: 'world' }, rewrite: false });
    expect('view' in parsed.route).toBe(false);
  });

  it('parses the rates view and drops unknown views', () => {
    expect(route('?page=world&view=rates')).toEqual({ page: 'world', view: 'rates' });
    expect(route('?page=world&view=RATES')).toEqual({ page: 'world', view: 'rates' });
    expect(route('?page=world&view=bogus')).toEqual({ page: 'world' });
    expect(parseRoute('?page=world&view=rates').rewrite).toBe(false);
    expect(parseRoute('?page=world&view=bogus').rewrite).toBe(true);
    // The view only belongs to the world page.
    expect(route('?page=themes&view=rates')).toEqual({ page: 'themes' });
  });

  it('maps the rates and bonds aliases to world + rates and rewrites to the canonical URL', () => {
    for (const alias of ['rates', 'bonds', 'RATES']) {
      const parsed = parseRoute(`?page=${alias}`);
      expect(parsed).toEqual({ route: { page: 'world', view: 'rates' }, rewrite: true });
      expect(serializeRoute(parsed.route)).toBe('?page=world&view=rates');
    }
    expect(route('?page=bonds&view=nope')).toEqual({ page: 'world', view: 'rates' });
  });

  it('serializes and round-trips', () => {
    expect(serializeRoute({ page: 'world' })).toBe('?page=world');
    expect(serializeRoute({ page: 'world', view: 'rates' })).toBe('?page=world&view=rates');
    expect(parseRoute('?page=world&view=rates')).toEqual({ route: { page: 'world', view: 'rates' }, rewrite: false });
    expect(sameRoute({ page: 'world' }, { page: 'world', view: 'rates' })).toBe(false);
  });

  it('unwraps LIFF state for the rates alias and path forms', () => {
    expect(parseRoute(liff('/atlas/?page=rates'))).toEqual({ route: { page: 'world', view: 'rates' }, rewrite: true });
    expect(parseRoute(liff('?page=world&view=rates'))).toEqual({ route: { page: 'world', view: 'rates' }, rewrite: true });
    expect(route(liff('/my-stock-screener/atlas/bonds'))).toEqual({ page: 'world', view: 'rates' });
    expect(route(liff('/atlas/?page=world'))).toEqual({ page: 'world' });
    expect(route(`${liff(encodeURIComponent('/?page=rates'))}&liff.referrer=x`)).toEqual({ page: 'world', view: 'rates' });
  });
});

describe('financials route', () => {
  it('parses symbol and the quarterly flag', () => {
    expect(route('?page=financials&symbol=AAPL')).toEqual({ page: 'financials', symbol: 'AAPL' });
    expect(route('?page=financials&symbol=aapl&p=q')).toEqual({ page: 'financials', symbol: 'AAPL', p: 'q' });
    expect(route('?page=financials&symbol=2330.TW&p=Q')).toEqual({ page: 'financials', symbol: '2330.TW', p: 'q' });
    expect(route('?page=financials&symbol=AAPL&p=a')).toEqual({ page: 'financials', symbol: 'AAPL' });
    expect(route('?page=financials&symbol=AAPL&p=bogus')).toEqual({ page: 'financials', symbol: 'AAPL' });
    expect(route('?page=financials')).toEqual({ page: 'financials' });
    expect(route('?page=financial&symbol=AAPL')).toEqual({ page: 'financials', symbol: 'AAPL' });
  });

  it('is canonical and rewrites junk parameters away', () => {
    expect(parseRoute('?page=financials&symbol=AAPL&p=q')).toEqual({
      route: { page: 'financials', symbol: 'AAPL', p: 'q' },
      rewrite: false,
    });
    expect(parseRoute('?page=financials&symbol=aapl&p=bogus')).toEqual({
      route: { page: 'financials', symbol: 'AAPL' },
      rewrite: true,
    });
    expect(parseRoute('?page=financials&p=q&symbol=AAPL').rewrite).toBe(true);
    expect(serializeRoute({ page: 'financials', symbol: '^TWII', p: 'q' })).toBe('?page=financials&symbol=^TWII&p=q');
    expect(serializeRoute({ page: 'financials' })).toBe('?page=financials');
  });

  it('does not change how a bare symbol or the chart page resolve', () => {
    expect(route('?symbol=AAPL')).toEqual({ page: 'chart', symbol: 'AAPL' });
    expect(route('?page=chart&symbol=AAPL&p=q')).toEqual({ page: 'chart', symbol: 'AAPL' });
  });

  it('unwraps LIFF state for financials', () => {
    expect(parseRoute(liff('/atlas/?page=financials&symbol=AAPL&p=q'))).toEqual({
      route: { page: 'financials', symbol: 'AAPL', p: 'q' },
      rewrite: true,
    });
    expect(route(liff('/atlas/financials?symbol=nvda'))).toEqual({ page: 'financials', symbol: 'NVDA' });
    expect(route(liff(encodeURIComponent('?page=financials&symbol=2330.TW')))).toEqual({ page: 'financials', symbol: '2330.TW' });
  });

  it('compares routes by canonical URL', () => {
    expect(sameRoute({ page: 'financials', symbol: 'AAPL' }, { page: 'financials', symbol: 'AAPL', p: 'q' })).toBe(false);
    expect(sameRoute({ page: 'financials', symbol: 'AAPL', p: 'q' }, { page: 'financials', symbol: 'AAPL', p: 'q' })).toBe(true);
  });
});
