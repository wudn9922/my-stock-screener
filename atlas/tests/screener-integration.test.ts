import { afterEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { normalizeSymbol } from '../src/market-data/MarketDataProvider';
import { getMarketProfile, isIndexSymbol, CANONICAL_SYMBOL_REGEX } from '../src/market-data/MarketProfile';
import { isSecEligibleSymbol } from '../src/fundamentals/SecEligibility';
import { FinancialNormalizer } from '../src/fundamentals/FinancialNormalizer';
import {
  SecEdgarProvider,
  StaticFundamentalsUnavailableError,
  STATIC_FUNDAMENTALS_UNAVAILABLE,
} from '../src/fundamentals/SecEdgarProvider';
import { parseLaunchParams, parseLaunchTimeframe } from '../src/app/LaunchParams';
import { isScreenerBase, storageName } from '../src/app/HostingMode';
import { parseAtlasTimeframes } from '../scripts/atlas-timeframes.ts';
import {
  collectSymbols,
  fetchScreenerSymbols,
  normalizeScreenerTicker,
  supabaseHeaders,
  supabaseRestUrl,
} from '../scripts/screener-symbols.mjs';

const rawNvda = JSON.parse(readFileSync('tests/fixtures/sec/NVDA.json', 'utf8'));
const normalizer = new FinancialNormalizer();
const nvdaAnnual = normalizer.normalize(rawNvda, 'NVDA', 'annual');

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ticker rule (A)', () => {
  it.each([
    ['2330.TW', '2330.TW'],
    ['6488.two', '6488.TWO'],
    ['00632R.TW', '00632R.TW'],
    ['^TWII', '^TWII'],
    ['^twoii', '^TWOII'],
    ['^GSPC', '^GSPC'],
    ['^SOX', '^SOX'],
    [' brk-b ', 'BRK-B'],
    ['NVDA', 'NVDA'],
  ])('accepts %s', (input, expected) => {
    expect(normalizeSymbol(input)).toBe(expected);
    expect(CANONICAL_SYMBOL_REGEX.test(expected)).toBe(true);
  });

  it.each([
    '',
    '   ',
    '^',
    '^^TWII',
    '^-X',
    '2330',
    '2330.HK',
    '23.TW',
    'NVDA;RM -RF',
    '<SCRIPT>',
    '../ETC/PASSWD',
    'A B',
    '%5ETWII',
    'NVDA/../../X',
    'ABCDEFGHIJKLMNOP',
    'NVDA?X=1',
    'NVDA#',
    'NVDA\u0000',
  ])('rejects %j with a market-neutral message', (input) => {
    expect(() => normalizeSymbol(input)).toThrow('請輸入有效的股票或指數代號');
  });

  it('assigns market profiles to Taiwan indices and keeps other indices on the US profile', () => {
    expect(getMarketProfile('^TWII')).toMatchObject({ market: 'TW', exchange: 'TWSE', currency: 'TWD' });
    expect(getMarketProfile('^TWOII')).toMatchObject({ market: 'TW', exchange: 'TPEx' });
    expect(getMarketProfile('^GSPC')).toMatchObject({ market: 'US', currency: 'USD' });
    expect(getMarketProfile('2330.TW').exchange).toBe('TWSE');
    expect(isIndexSymbol('^SOX')).toBe(true);
    expect(isIndexSymbol('NVDA')).toBe(false);
  });

  it('only US non-index symbols are SEC eligible', () => {
    expect(isSecEligibleSymbol('NVDA')).toBe(true);
    expect(isSecEligibleSymbol('BRK-B')).toBe(true);
    expect(isSecEligibleSymbol('2330.TW')).toBe(false);
    expect(isSecEligibleSymbol('6488.TWO')).toBe(false);
    expect(isSecEligibleSymbol('^GSPC')).toBe(false);
    expect(isSecEligibleSymbol('^TWII')).toBe(false);
  });
});

describe('static SecEdgarProvider (C)', () => {
  const base = '/my-stock-screener/atlas/fundamentals/';
  const json = (value: unknown, status = 200) =>
    new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });

  it('reads <SYMBOL>-<period>.json, validates, caches and reports manifest freshness', async () => {
    const checkedAt = 1_790_000_000;
    const fetcher = vi.fn(async (url: string | URL | Request) => {
      const path = String(url);
      if (path.endsWith('manifest.json'))
        return json({ version: 1, symbols: { NVDA: { status: 'fresh', checkedAt, fetchedAt: checkedAt - 10 } } });
      if (path === `${base}NVDA-annual.json`) return json(nvdaAnnual);
      return new Response('missing', { status: 404 });
    });
    const provider = new SecEdgarProvider({ staticFiles: true, staticBase: base, fetcher, now: () => (checkedAt + 3600) * 1000 });
    const records = await provider.getFinancials('nvda', 'annual');
    expect(records).toEqual(nvdaAnnual);
    expect(provider.staticSnapshot).toBe(true);
    expect(provider.getSnapshotMetadata('NVDA')).toMatchObject({
      symbol: 'NVDA',
      generatedAt: checkedAt,
      fetchedAt: checkedAt - 10,
      stale: false,
      offline: false,
      source: 'https://data.sec.gov/api/xbrl/companyfacts/CIK0001045810.json',
    });
    records[0].revenue = 1;
    expect((await provider.getFinancials('NVDA', 'annual'))[0].revenue).toBe(nvdaAnnual[0].revenue);
    const fileRequests = fetcher.mock.calls.filter(([url]) => String(url).includes('NVDA-annual'));
    expect(fileRequests).toHaveLength(1);
    expect(fetcher.mock.calls.some(([url]) => String(url).includes('/api/'))).toBe(false);
  });

  it('turns a 404 into the clear not-pre-downloaded message', async () => {
    const fetcher = vi.fn(async (_url: string | URL | Request) => new Response('missing', { status: 404 }));
    const provider = new SecEdgarProvider({ staticFiles: true, staticBase: base, fetcher });
    const error = await provider.getFinancials('SPY', 'quarterly').catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(StaticFundamentalsUnavailableError);
    expect((error as Error).message).toBe(STATIC_FUNDAMENTALS_UNAVAILABLE);
    expect(String(fetcher.mock.calls[0][0])).toBe(`${base}SPY-quarterly.json`);
  });

  it('treats an SPA index.html fallback for a missing file as not available', async () => {
    const fetcher = vi.fn(async () => new Response('<!doctype html>', { status: 200, headers: { 'Content-Type': 'text/html' } }));
    const provider = new SecEdgarProvider({ staticFiles: true, staticBase: base, fetcher });
    await expect(provider.getFinancials('SPY', 'annual')).rejects.toBeInstanceOf(StaticFundamentalsUnavailableError);
  });

  it('never requests files for Taiwan listings or indices', async () => {
    const fetcher = vi.fn(async () => json([]));
    const provider = new SecEdgarProvider({ staticFiles: true, staticBase: base, fetcher });
    for (const symbol of ['2330.TW', '^GSPC', '^TWII'])
      await expect(provider.getFinancials(symbol, 'annual')).rejects.toBeInstanceOf(StaticFundamentalsUnavailableError);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('rejects schema-invalid and mismatched files instead of showing them', async () => {
    const invalid = structuredClone(nvdaAnnual);
    (invalid[0] as { revenue: unknown }).revenue = 'lots';
    let body: unknown = invalid;
    const fetcher = vi.fn(async (url: string | URL | Request) =>
      String(url).endsWith('manifest.json') ? new Response('', { status: 404 }) : json(body),
    );
    const provider = new SecEdgarProvider({ staticFiles: true, staticBase: base, fetcher });
    await expect(provider.getFinancials('NVDA', 'annual')).rejects.toThrow();
    body = nvdaAnnual;
    await expect(provider.getFinancials('AAPL', 'annual')).rejects.toThrow('mismatched');
    await expect(provider.getFinancials('NVDA', 'quarterly')).rejects.toThrow('mismatched');
    const errorProvider = new SecEdgarProvider({
      staticFiles: true,
      staticBase: base,
      fetcher: vi.fn(async () => new Response('', { status: 500 })),
    });
    await expect(errorProvider.getFinancials('NVDA', 'annual')).rejects.toThrow('暫時無法讀取');
  });

  it('keeps the backend path for non-static builds', async () => {
    const fetcher = vi.fn(async (_url: string | URL | Request) => json(nvdaAnnual));
    const provider = new SecEdgarProvider({ staticFiles: false, fetcher });
    await provider.getFinancials('NVDA', 'annual');
    expect(String(fetcher.mock.calls[0][0])).toBe('/api/fundamentals?symbol=NVDA&period=annual');
    expect(provider.getSnapshotMetadata('NVDA')).toBeUndefined();
  });
});

describe('URL launch parameters (B)', () => {
  it('parses symbol and timeframe', () => {
    expect(parseLaunchParams('?symbol=2330.TW&tf=1D')).toEqual({ symbol: '2330.TW', timeframe: '1D' });
    expect(parseLaunchParams('?symbol=nvda&tf=1W')).toEqual({ symbol: 'NVDA', timeframe: '1W' });
    expect(parseLaunchParams('?symbol=%5ETWII')).toEqual({ symbol: '^TWII' });
    expect(parseLaunchParams('?symbol=^GSPC&tf=1M')).toEqual({ symbol: '^GSPC', timeframe: '1M' });
    expect(parseLaunchParams('?symbol=BRK-B&tf=5m')).toEqual({ symbol: 'BRK-B', timeframe: '5m' });
    expect(parseLaunchParams('?symbol=2330')).toEqual({ symbol: '2330', needsCatalog: true });
  });

  it('ignores a missing symbol and reports invalid input without throwing', () => {
    expect(parseLaunchParams('')).toBeNull();
    expect(parseLaunchParams('?tf=1D')).toBeNull();
    expect(parseLaunchParams('?symbol=%20')).toBeNull();
    expect(parseLaunchParams('?symbol=<script>alert(1)</script>')?.error).toContain('請輸入有效');
    expect(parseLaunchParams(`?symbol=${'A'.repeat(200)}`)?.error).toBeTruthy();
    expect(parseLaunchParams('?symbol=NVDA&tf=2D')).toEqual({ symbol: 'NVDA', ignoredTimeframe: '2D' });
  });

  it('accepts only Atlas timeframes and unambiguous case aliases', () => {
    expect(parseLaunchTimeframe('1d')).toBe('1D');
    expect(parseLaunchTimeframe('1h')).toBe('1H');
    expect(parseLaunchTimeframe('1M')).toBe('1M');
    expect(parseLaunchTimeframe('1m')).toBeUndefined();
    expect(parseLaunchTimeframe('15m')).toBe('15m');
    expect(parseLaunchTimeframe('1Y')).toBeUndefined();
    expect(parseLaunchTimeframe(null)).toBeUndefined();
  });
});

describe('storage isolation (D)', () => {
  it('suffixes database names only for screener base paths', () => {
    expect(isScreenerBase('/my-stock-screener/atlas/')).toBe(true);
    expect(storageName('atlas-terminal', '/my-stock-screener/atlas/')).toBe('atlas-terminal-screener');
    expect(storageName('atlas-terminal', '/lightweight-drawing-lab/')).toBe('atlas-terminal');
    expect(storageName('atlas-terminal', '/')).toBe('atlas-terminal');
    expect(storageName('atlas-terminal')).toBe('atlas-terminal');
  });
});

describe('ATLAS_TIMEFRAMES', () => {
  const all = ['5m', '15m', '30m', '1H', '1D', '1W', '1M'] as const;
  it('keeps every interval when unset or blank', () => {
    expect(parseAtlasTimeframes(undefined, all).timeframes).toEqual([...all]);
    expect(parseAtlasTimeframes('  ', all).timeframes).toEqual([...all]);
  });
  it('limits intervals in configured order', () => {
    expect(parseAtlasTimeframes('1M, 1W ,1D', all)).toEqual({ timeframes: ['1D', '1W', '1M'], warnings: [] });
    expect(parseAtlasTimeframes('1d,1h', all).timeframes).toEqual(['1H', '1D']);
  });
  it('always keeps 1D and rejects unknown values', () => {
    const selection = parseAtlasTimeframes('1W', all);
    expect(selection.timeframes).toEqual(['1D', '1W']);
    expect(selection.warnings[0]).toContain('1D');
    expect(() => parseAtlasTimeframes('1D,4H', all)).toThrow('unsupported timeframe "4H"');
    expect(() => parseAtlasTimeframes('1D,1m', all)).toThrow('"1m"');
    expect(() => parseAtlasTimeframes(',', all)).toThrow('lists no timeframe');
  });
});

describe('screener-symbols normalization', () => {
  it.each([
    ['2330', '2330.TW'],
    ['2330.tw', '2330.TW'],
    [' 6488.TWO ', '6488.TWO'],
    ['brk.b', 'BRK-B'],
    ['BRK-B', 'BRK-B'],
    ['nvda', 'NVDA'],
    ['^twii', '^TWII'],
    ['^GSPC', '^GSPC'],
  ])('%j -> %s', (input, expected) => {
    expect(normalizeScreenerTicker(input)).toBe(expected);
    expect(normalizeSymbol(expected)).toBe(expected);
  });

  it.each([null, undefined, '', '  ', '12', '1234567', 'NVDA;DROP', '^', '$$$', '<b>', 'ABCDEFGHIJKLMNOP', 42])(
    'skips invalid %j',
    (input) => {
      expect(normalizeScreenerTicker(input)).toBeNull();
    },
  );

  it('dedupes, sorts and logs skipped tickers', () => {
    const log = vi.fn();
    expect(collectSymbols(['nvda', 'NVDA', '2330', '2330.TW', '^TWII', 'BRK.B', 'bad ticker', null], log)).toEqual([
      '2330.TW',
      'BRK-B',
      'NVDA',
      '^TWII',
    ]);
    expect(log).toHaveBeenCalledWith(expect.stringContaining('bad ticker'));
  });

  it('builds the REST URL and key headers', () => {
    expect(supabaseRestUrl('https://x.supabase.co/')).toBe('https://x.supabase.co/rest/v1');
    expect(supabaseRestUrl('https://x.supabase.co/rest/v1/')).toBe('https://x.supabase.co/rest/v1');
    expect(() => supabaseRestUrl('')).toThrow('SUPABASE_URL');
    expect(supabaseHeaders('sb_secret_abc')).toEqual({ apikey: 'sb_secret_abc', Accept: 'application/json' });
    expect(supabaseHeaders('eyJhbGci.x.y')).toMatchObject({ apikey: 'eyJhbGci.x.y', Authorization: 'Bearer eyJhbGci.x.y' });
  });

  it('reads stocks (filtered by user) and index_configs', async () => {
    const urls: string[] = [];
    const fetchImpl = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      urls.push(String(url));
      expect((init?.headers as Record<string, string>).apikey).toBe('sb_publishable_k');
      if (String(url).includes('/stocks?'))
        return new Response(JSON.stringify([
          { ticker: '2330', line_user_id: 'U1' },
          { ticker: 'brk.b', line_user_id: 'U1' },
          { ticker: 'TSLA', line_user_id: 'U2' },
          { ticker: '???', line_user_id: 'U1' },
        ]));
      return new Response(JSON.stringify([{ ticker: '^TWII' }, { ticker: '^SOX', enabled: false }]));
    });
    const symbols = await fetchScreenerSymbols(
      { SUPABASE_URL: 'https://x.supabase.co', SUPABASE_ANON_KEY: 'sb_publishable_k', SUPABASE_USER_ID: 'U1' },
      fetchImpl as unknown as typeof fetch,
      () => undefined,
    );
    expect(symbols).toEqual(['2330.TW', 'BRK-B', '^TWII']);
    expect(urls[0]).toContain('https://x.supabase.co/rest/v1/stocks?');
    expect(urls[0]).toContain('line_user_id=eq.U1');
  });

  it('fails without writing when Supabase errors or returns no stocks', async () => {
    const env = { SUPABASE_URL: 'https://x.supabase.co/rest/v1', SUPABASE_SERVICE_ROLE_KEY: 'eyJk' };
    await expect(
      fetchScreenerSymbols(env, vi.fn(async () => new Response('denied', { status: 401 })) as unknown as typeof fetch, () => undefined),
    ).rejects.toThrow('HTTP 401');
    await expect(
      fetchScreenerSymbols(env, vi.fn(async () => new Response('[]')) as unknown as typeof fetch, () => undefined),
    ).rejects.toThrow('no valid stock tickers');
    await expect(fetchScreenerSymbols({ SUPABASE_URL: 'https://x' }, vi.fn() as unknown as typeof fetch)).rejects.toThrow('KEY');
  });
});
