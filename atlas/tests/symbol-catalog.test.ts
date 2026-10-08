import { afterEach, expect, it, vi } from 'vitest';
import {
  loadSymbolCatalog,
  resolveSymbolInput,
  searchSymbolCatalog,
  symbolCatalogDocumentSchema,
  type SymbolCatalogEntry,
} from '../src/market-data/SymbolCatalog';

const twse: SymbolCatalogEntry = {
  symbol: '2330.TW',
  ticker: '2330',
  name: '台積電',
  market: 'TWSE',
  kind: 'stock',
};
const tpex: SymbolCatalogEntry = {
  symbol: '2330.TWO',
  ticker: '2330',
  name: '測試公司',
  market: 'TPEx',
  kind: 'stock',
};
const etf: SymbolCatalogEntry = {
  symbol: '0050.TW',
  ticker: '0050',
  name: '元大台灣50',
  market: 'TWSE',
  kind: 'etf',
};

afterEach(() => vi.unstubAllGlobals());

it('resolves bare Taiwan tickers only against unique official entries', () => {
  expect(resolveSymbolInput('2330', [twse])).toBe('2330.TW');
  expect(resolveSymbolInput(' 0050 ', [twse, etf])).toBe('0050.TW');
  expect(resolveSymbolInput('2330.TWO', [twse])).toBe('2330.TWO');
  expect(resolveSymbolInput(' aapl ', [twse])).toBe('AAPL');
  expect(() => resolveSymbolInput('2330', [twse, tpex])).toThrow(/2330\.TW.*2330\.TWO/);
  expect(() => resolveSymbolInput('9999', [twse])).toThrow(/explicit \.TW or \.TWO suffix/);
});

it('searches official ticker/name entries in a stable order and honors limits', () => {
  expect(searchSymbolCatalog('2330', [tpex, twse]).map((entry) => entry.symbol)).toEqual([
    '2330.TW',
    '2330.TWO',
  ]);
  expect(searchSymbolCatalog('2330.TW', [tpex, twse]).map((entry) => entry.symbol)).toEqual([
    '2330.TW',
  ]);
  expect(searchSymbolCatalog('台積', [etf, twse])).toEqual([twse]);
  expect(searchSymbolCatalog('unmatched', [twse])).toEqual([]);
  expect(searchSymbolCatalog('23', [tpex, twse], 1)).toHaveLength(1);
  expect(() => searchSymbolCatalog('23', [twse], -1)).toThrow(/non-negative integer/);
});

it('validates catalog suffixes, identity fields, source provenance, and duplicate symbols', () => {
  const document = {
    version: 1,
    fetchedAt: '2026-10-08T09:00:00.000Z',
    sources: [
      {
        market: 'TWSE',
        kind: 'issuer-directory',
        url: 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L',
        fetchedAt: '2026-10-08T09:00:00.000Z',
      },
      {
        market: 'TPEx',
        kind: 'issuer-directory',
        url: 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O',
        fetchedAt: '2026-10-08T09:00:00.000Z',
      },
    ],
    entries: [twse, etf],
  };
  expect(symbolCatalogDocumentSchema.parse(document).entries).toHaveLength(2);
  expect(
    symbolCatalogDocumentSchema.safeParse({
      ...document,
      entries: [{ ...twse, symbol: '2330.TWO' }],
    }).success,
  ).toBe(false);
  expect(
    symbolCatalogDocumentSchema.safeParse({ ...document, entries: [twse, twse] }).success,
  ).toBe(false);
});

it('loads the validated directory from BASE_URL and shares the in-flight same-origin request', async () => {
  const document = {
    version: 1,
    fetchedAt: '2026-10-08T09:00:00.000Z',
    sources: [
      {
        market: 'TWSE',
        kind: 'issuer-directory',
        url: 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L',
        fetchedAt: '2026-10-08T09:00:00.000Z',
      },
      {
        market: 'TPEx',
        kind: 'issuer-directory',
        url: 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O',
        fetchedAt: '2026-10-08T09:00:00.000Z',
      },
    ],
    entries: [twse],
  };
  const fetchMock = vi.fn(
    async (_input: RequestInfo | URL) => new Response(JSON.stringify(document), { status: 200 }),
  );
  vi.stubGlobal('fetch', fetchMock);
  const [first, second] = await Promise.all([loadSymbolCatalog(), loadSymbolCatalog()]);
  expect(first).toEqual([twse]);
  expect(second).toEqual([twse]);
  expect(first).not.toBe(second);
  expect(first[0]).not.toBe(second[0]);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls[0]?.[0]).toBe(`${import.meta.env.BASE_URL}symbols/taiwan.json`);
});
