import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  buildDirectoryDocument,
  cleanUsName,
  normalizeUsSymbol,
  parseIsinHtml,
  parsePipeTable,
  taiwanItemsFromCatalog,
  taiwanItemsFromIsin,
  taiwanItemsFromIssuerList,
  usExclusionReason,
  usItemsFromNasdaqListed,
  usItemsFromOtherListed,
} from '../scripts/symbol-directory.ts';
import {
  clearSymbolDirectoryCache,
  getLoadedSymbolDirectory,
  loadSymbolDirectory,
  parseSymbolDirectoryDocument,
  resolveDirectoryInput,
  searchSymbolDirectory,
  searchSymbols,
  type SymbolDirectoryEntry,
} from '../src/market-data/SymbolCatalog';

const fixture = (name: string) => readFileSync(new URL(`./fixtures/directory/${name}`, import.meta.url), 'utf8');

describe('Taiwan ISIN directory parsing', () => {
  it('parses section and data rows from the decoded ISIN page', () => {
    const rows = parseIsinHtml(fixture('isin-2.utf8.html'));
    expect(rows.find((row) => row.code === '2330')).toMatchObject({
      section: '股票',
      name: '台積電',
      isin: 'TW0002330008',
      market: '上市',
      industry: '半導體業',
      cfi: 'ESVUFR',
    });
    expect(rows.find((row) => row.code === '910322')?.section).toBe('臺灣存託憑證(TDR)');
  });

  it('keeps only 4-digit common shares (CFI ES*): no warrants, preferred, ETF, TDR, REIT', () => {
    const twse = taiwanItemsFromIsin(parseIsinHtml(fixture('isin-2.utf8.html')), 'TWSE');
    expect(twse).toEqual([
      ['1101.TW', '台泥', 'TWSE'],
      ['1256.TW', '鮮活果汁-KY', 'TWSE'],
      ['2330.TW', '台積電', 'TWSE'],
      ['2881.TW', '富邦金', 'TWSE'],
      ['2254.TW', '巨鎧精密-創', 'TWSE'],
    ]);
    const tpex = taiwanItemsFromIsin(parseIsinHtml(fixture('isin-4.utf8.html')), 'TPEx');
    expect(tpex.map((item) => item[0])).toEqual(['3105.TWO', '6488.TWO', '8069.TWO']);
    // The wrong market's page never yields rows for the other exchange.
    expect(taiwanItemsFromIsin(parseIsinHtml(fixture('isin-4.utf8.html')), 'TWSE')).toEqual([]);
  });

  it('falls back to OpenAPI issuer lists and the tracked catalog', () => {
    expect(
      taiwanItemsFromIssuerList(
        [{ 公司代號: '2330', 公司簡稱: '台積電' }, { 公司代號: '0050', 公司簡稱: 'ETF?' }, { SecuritiesCompanyCode: '6488', CompanyAbbreviation: '環球晶' }, null],
        'TWSE',
      ),
    ).toEqual([
      ['2330.TW', '台積電', 'TWSE'],
      ['0050.TW', 'ETF?', 'TWSE'],
      ['6488.TW', '環球晶', 'TWSE'],
    ]);
    const catalog = JSON.parse(readFileSync('public/symbols/taiwan.json', 'utf8'));
    const fromCatalog = taiwanItemsFromCatalog(catalog, 'TWSE');
    expect(fromCatalog.length).toBeGreaterThan(700);
    expect(fromCatalog).toContainEqual(['2330.TW', '台積電', 'TWSE']);
    expect(fromCatalog.some((item) => item[0] === '0050.TW')).toBe(false);
  });
});

describe('NasdaqTrader directory parsing', () => {
  it('parses pipe tables with the creation-time footer', () => {
    const table = parsePipeTable(fixture('nasdaqlisted.txt'));
    expect(table.fileCreationTime).toBe('1008202617:01');
    expect(table.rows[0]).toMatchObject({ Symbol: 'AAPL', ETF: 'N', 'Test Issue': 'N' });
  });

  it('keeps operating companies and ADRs; drops ETFs, warrants, rights, units, preferreds, notes, funds, SPACs, tests', () => {
    const nasdaq = usItemsFromNasdaqListed(fixture('nasdaqlisted.txt'));
    expect(nasdaq).toEqual([
      ['AAPL', 'Apple Inc.', 'NASDAQ'],
      ['ASML', 'ASML Holding N.V.', 'NASDAQ'],
      ['GOOG', 'Alphabet Inc.', 'NASDAQ'],
      ['GOOGL', 'Alphabet Inc.', 'NASDAQ'],
      ['NVDA', 'NVIDIA Corporation', 'NASDAQ'],
    ]);
    const other = usItemsFromOtherListed(fixture('otherlisted.txt'));
    expect(other).toEqual([
      ['A', 'Agilent Technologies, Inc.', 'NYSE'],
      ['BRK-B', 'Berkshire Hathaway Inc.', 'NYSE'],
      ['EPD', 'Enterprise Products Partners L.P.', 'NYSE'],
      ['FRT', 'Federal Realty Investment Trust', 'NYSE'],
      ['IMO', 'Imperial Oil Limited', 'NYSE American'],
      ['NVO', 'Novo Nordisk A/S', 'NYSE'],
      ['TSM', 'Taiwan Semiconductor Manufacturing Company Ltd.', 'NYSE'],
      ['XYZ', 'Block, Inc.', 'NYSE'],
    ]);
  });

  it('normalizes share-class spellings to Yahoo and rejects derivative suffixes', () => {
    expect(normalizeUsSymbol('BRK.B')).toBe('BRK-B');
    expect(normalizeUsSymbol('BRK/B')).toBe('BRK-B');
    expect(normalizeUsSymbol('BF.B')).toBe('BF-B');
    expect(normalizeUsSymbol('BRK$B')).toBeNull();
    expect(normalizeUsSymbol('ABR$D')).toBeNull();
    expect(normalizeUsSymbol('AAM.U')).toBeNull();
    expect(normalizeUsSymbol('AAM.WS')).toBeNull();
    expect(normalizeUsSymbol('AAM.WS.A')).toBeNull();
    expect(normalizeUsSymbol('XYZ.R')).toBeNull();
    expect(normalizeUsSymbol('AAM=')).toBeNull();
    expect(normalizeUsSymbol('nvda')).toBe('NVDA');
  });

  it('classifies security names', () => {
    expect(usExclusionReason('Energy Transfer LP Common Units representing limited partner interests')).toBeNull();
    expect(usExclusionReason('Taiwan Semiconductor Manufacturing Company Ltd. American Depositary Shares')).toBeNull();
    expect(usExclusionReason('Foo Corp. Units, each consisting of one share')).toBe('unit');
    expect(usExclusionReason('Foo Corp - Rights')).toBe('right');
    expect(usExclusionReason('Bar Inc. 8.00% Senior Notes due 2030')).toBe('fixed-income');
    expect(usExclusionReason('Eaton Vance Municipal Income Trust')).toBe('fund');
    expect(usExclusionReason('Some Capital Acquisition Corporation Class A Ordinary Shares')).toBe('spac');
    expect(cleanUsName('Microsoft Corporation - Common Stock')).toBe('Microsoft Corporation');
    expect(cleanUsName('Shopify Inc. Class A Subordinate Voting Shares')).toBe('Shopify Inc.');
  });

  it('assembles a compact, de-duplicated document', () => {
    const document = buildDirectoryDocument(
      {
        TWSE: { items: [['2330.TW', '台積電', 'TWSE']], source: 'isin' },
        TPEx: { items: [['6488.TWO', '環球晶', 'TPEx']], source: 'isin' },
        US: { items: [['NVDA', 'NVIDIA Corporation', 'NASDAQ'], ['NVDA', 'dup', 'NYSE'], ['AAPL', 'Apple Inc.', 'NASDAQ']], source: 'nasdaqtrader' },
      },
      '2026-10-08T00:00:00.000Z',
    );
    expect(document.items).toEqual([
      ['2330.TW', '台積電', 'TWSE'],
      ['6488.TWO', '環球晶', 'TPEx'],
      ['AAPL', 'Apple Inc.', 'NASDAQ'],
      ['NVDA', 'NVIDIA Corporation', 'NASDAQ'],
    ]);
    expect(document.counts).toEqual({ TWSE: 1, TPEx: 1, US: 3 });
  });
});

const entries: SymbolDirectoryEntry[] = parseSymbolDirectoryDocument({
  version: 1,
  generatedAt: '2026-10-08T00:00:00Z',
  items: [
    ['2330.TW', '台積電', 'TWSE'],
    ['2303.TW', '聯電', 'TWSE'],
    ['3105.TWO', '穩懋', 'TPEx'],
    ['6488.TWO', '環球晶', 'TPEx'],
    ['5347.TWO', '世界', 'TPEx'],
    ['NVDA', 'NVIDIA Corporation', 'NASDAQ'],
    ['NVO', 'Novo Nordisk A/S', 'NYSE'],
    ['NVR', 'NVR, Inc.', 'NYSE'],
    ['AAPL', 'Apple Inc.', 'NASDAQ'],
    ['APLE', 'Apple Hospitality REIT, Inc.', 'NYSE'],
    ['PNAP', 'Pineapple Energy', 'NASDAQ'],
    ['TSM', 'Taiwan Semiconductor Manufacturing Company Ltd.', 'NYSE'],
    ['BRK-B', 'Berkshire Hathaway Inc.', 'NYSE'],
    ['2330.TWO', 'wrong exchange', 'TWSE'],
    ['bad symbol', 'x', 'NYSE'],
    ['AAPL', 'duplicate', 'NYSE'],
    ['X', '', 'NYSE'],
  ],
});

describe('directory search (client)', () => {
  afterEach(() => {
    clearSymbolDirectoryCache();
    vi.unstubAllGlobals();
  });

  it('parses and validates the compact document', () => {
    expect(entries).toHaveLength(13);
    expect(entries[0]).toEqual({ symbol: '2330.TW', ticker: '2330', name: '台積電', exchange: 'TWSE', market: 'TW' });
    expect(entries.find((entry) => entry.symbol === 'AAPL')?.name).toBe('Apple Inc.');
  });

  it('ranks exact ticker, ticker prefix, name prefix, English word prefix, Chinese substring', () => {
    const symbols = (query: string, limit?: number) => searchSymbolDirectory(query, entries, limit).map((entry) => entry.symbol);
    expect(symbols('2330')).toEqual(['2330.TW']);
    expect(symbols('23')).toEqual(['2303.TW', '2330.TW']);
    expect(symbols('nv')).toEqual(['NVO', 'NVR', 'NVDA']);
    expect(symbols('NVDA')).toEqual(['NVDA']);
    expect(symbols('apple')).toEqual(['AAPL', 'APLE']);
    expect(symbols('semiconductor')).toEqual(['TSM']);
    expect(symbols('積電')).toEqual(['2330.TW']);
    expect(symbols('環球')).toEqual(['6488.TWO']);
    expect(symbols('brk.b')).toEqual(['BRK-B']);
    expect(symbols('6488.two')).toEqual(['6488.TWO']);
    expect(symbols('a', 2)).toHaveLength(2);
    expect(symbols('   ')).toEqual([]);
    expect(() => searchSymbolDirectory('a', entries, -1)).toThrow('non-negative');
  });

  it('resolves bare Taiwan digits and dotted US classes', () => {
    expect(resolveDirectoryInput('2330', entries)).toBe('2330.TW');
    expect(resolveDirectoryInput('6488', entries)).toBe('6488.TWO');
    expect(resolveDirectoryInput('brk.b', entries)).toBe('BRK-B');
    expect(resolveDirectoryInput('nvda', entries)).toBe('NVDA');
    expect(() => resolveDirectoryInput('9999', entries)).toThrow(/explicit \.TW or \.TWO suffix/);
  });

  it('loads directory.json once and searches the loaded entries', async () => {
    const fetcher = vi.fn(async () =>
      new Response(JSON.stringify({ version: 1, generatedAt: '2026-10-08T00:00:00Z', items: [['NVDA', 'NVIDIA Corporation', 'NASDAQ']] })),
    ) as unknown as typeof fetch;
    expect(searchSymbols('nvda')).toEqual([]);
    const [first, second] = await Promise.all([
      loadSymbolDirectory(undefined, { url: '/symbols/directory.json', fetcher }),
      loadSymbolDirectory(undefined, { url: '/symbols/directory.json', fetcher }),
    ]);
    expect(first).toBe(second);
    expect(first.source).toBe('directory');
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(getLoadedSymbolDirectory()?.entries).toHaveLength(1);
    expect(searchSymbols('nvidia').map((entry) => entry.symbol)).toEqual(['NVDA']);
  });

  it('falls back to the Taiwan catalog when directory.json is missing', async () => {
    const catalog = readFileSync('public/symbols/taiwan.json', 'utf8');
    vi.stubGlobal('fetch', vi.fn(async () => new Response(catalog)));
    const directory = await loadSymbolDirectory(undefined, {
      url: '/symbols/directory.json',
      fetcher: (async () => new Response('Not found', { status: 404 })) as typeof fetch,
    });
    expect(directory.source).toBe('taiwan-catalog');
    expect(directory.entries.length).toBeGreaterThan(1000);
    expect(searchSymbols('2330')[0]?.symbol).toBe('2330.TW');
  });
});
