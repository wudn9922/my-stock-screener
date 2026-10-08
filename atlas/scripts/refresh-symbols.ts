import { mkdir, rename, writeFile } from 'node:fs/promises';
import { fetch, EnvHttpProxyAgent } from 'undici';
import type {
  SymbolCatalogDocument,
  SymbolCatalogEntry,
  SymbolCatalogSource,
} from '../src/market-data/SymbolCatalog';
import { symbolCatalogDocumentSchema } from '../src/market-data/SymbolCatalog';

const sources = [
  {
    market: 'TWSE',
    kind: 'issuer-directory',
    url: 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L',
  },
  {
    market: 'TPEx',
    kind: 'issuer-directory',
    url: 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O',
  },
  {
    market: 'TWSE',
    kind: 'fund-directory',
    url: 'https://openapi.twse.com.tw/v1/opendata/t187ap47_L',
  },
] as const satisfies ReadonlyArray<Pick<SymbolCatalogSource, 'market' | 'kind' | 'url'>>;

const dispatcher = new EnvHttpProxyAgent();
const fetchedAt = new Date().toISOString();

function recordValue(record: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && String(value).trim()) return String(value).trim();
  }
  return '';
}

function sourceDate(records: readonly Record<string, unknown>[]): string | undefined {
  const first = records[0];
  if (!first) return undefined;
  return recordValue(first, '出表日期', 'Date', 'DateOfInformation') || undefined;
}

async function fetchDirectory(source: (typeof sources)[number]): Promise<{
  rows: Record<string, unknown>[];
  metadata: SymbolCatalogSource;
}> {
  const response = await fetch(source.url, {
    dispatcher,
    signal: AbortSignal.timeout(60000),
    headers: { 'User-Agent': 'Mozilla/5.0 AtlasResearchTerminal/1.0' },
  });
  if (!response.ok)
    throw new Error(`${source.market} directory request failed (HTTP ${response.status})`);
  const value: unknown = await response.json();
  if (
    !Array.isArray(value) ||
    value.some((row) => typeof row !== 'object' || row === null || Array.isArray(row))
  ) {
    throw new Error(`${source.market} directory returned an unexpected payload`);
  }
  const rows = value as Record<string, unknown>[];
  return {
    rows,
    metadata: {
      ...source,
      fetchedAt,
      ...(sourceDate(rows) ? { sourceDate: sourceDate(rows) } : {}),
    },
  };
}

function officialEntry(
  tickerValue: string,
  nameValue: string,
  market: SymbolCatalogEntry['market'],
  kind: SymbolCatalogEntry['kind'],
): SymbolCatalogEntry {
  const ticker = tickerValue.trim().toUpperCase();
  const name = nameValue.trim();
  if (
    !/^[0-9]{4,6}[A-Z]?$/.test(ticker) ||
    !name ||
    [...name].some((character) => character.charCodeAt(0) < 32)
  ) {
    throw new Error(
      `Official ${market} directory contains an invalid ticker/name record: ${JSON.stringify({ ticker, name })}`,
    );
  }
  return { ticker, symbol: `${ticker}.${market === 'TWSE' ? 'TW' : 'TWO'}`, name, market, kind };
}

function buildEntries(
  twseIssuers: Record<string, unknown>[],
  tpexIssuers: Record<string, unknown>[],
  twseFunds: Record<string, unknown>[],
): SymbolCatalogEntry[] {
  if (twseIssuers.length < 500 || tpexIssuers.length < 300 || twseFunds.length < 100) {
    throw new Error(
      `Official directory unexpectedly shrank (${twseIssuers.length} TWSE issuers, ${tpexIssuers.length} TPEx issuers, ${twseFunds.length} TWSE funds)`,
    );
  }
  const etfs = twseFunds.flatMap((row) => {
    const type = recordValue(row, '基金類型');
    // The official fund table also lists futures-trust funds; add only funds
    // whose published type explicitly identifies a stock ETF or exchange-traded fund.
    if (!type.includes('股票型基金') && !type.includes('交易所交易基金')) return [];
    return [
      officialEntry(recordValue(row, '基金代號'), recordValue(row, '基金簡稱'), 'TWSE', 'etf'),
    ];
  });
  if (etfs.length < 100)
    throw new Error(
      `Official TWSE ETF classification unexpectedly shrank to ${etfs.length} records`,
    );
  const entries = [
    ...twseIssuers.map((row) =>
      officialEntry(recordValue(row, '公司代號'), recordValue(row, '公司簡稱'), 'TWSE', 'stock'),
    ),
    ...tpexIssuers.map((row) =>
      officialEntry(
        recordValue(row, 'SecuritiesCompanyCode'),
        recordValue(row, 'CompanyAbbreviation'),
        'TPEx',
        'stock',
      ),
    ),
    ...etfs,
  ].sort((a, b) => a.symbol.localeCompare(b.symbol));

  const seen = new Set<string>();
  for (const entry of entries) {
    if (seen.has(entry.symbol))
      throw new Error(`Official sources contain a duplicate listing for ${entry.symbol}`);
    seen.add(entry.symbol);
  }
  return entries;
}

try {
  const responses = await Promise.all(sources.map(fetchDirectory));
  const entries = buildEntries(responses[0]!.rows, responses[1]!.rows, responses[2]!.rows);
  const document: SymbolCatalogDocument = symbolCatalogDocumentSchema.parse({
    version: 1,
    fetchedAt,
    sources: responses.map((response) => response.metadata),
    entries,
  });
  const path = 'public/symbols/taiwan.json';
  await mkdir('public/symbols', { recursive: true });
  await writeFile(`${path}.tmp`, `${JSON.stringify(document)}\n`, 'utf8');
  await rename(`${path}.tmp`, path);
  console.log(
    JSON.stringify({
      path,
      fetchedAt,
      entries: entries.length,
      byMarket: {
        TWSE: entries.filter((entry) => entry.market === 'TWSE').length,
        TPEx: entries.filter((entry) => entry.market === 'TPEx').length,
      },
      byKind: {
        stock: entries.filter((entry) => entry.kind === 'stock').length,
        etf: entries.filter((entry) => entry.kind === 'etf').length,
      },
      sources: document.sources,
    }),
  );
} finally {
  await dispatcher.close();
}
