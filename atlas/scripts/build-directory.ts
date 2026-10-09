/**
 * Builds public/symbols/directory.json (git-ignored; built in GitHub Actions) — every tradable common stock
 * on TWSE, TPEx and the US exchanges. See scripts/symbol-directory.ts for inclusion rules.
 *
 *   node scripts/build-directory.mjs                         # network
 *   node scripts/build-directory.mjs --fixtures=tests/fixtures/directory   # offline fixtures
 *   ATLAS_DIRECTORY_OUT=/tmp/directory.json node scripts/build-directory.mjs
 *
 * Per market the first source that passes a size sanity check wins:
 *   Taiwan: TWSE ISIN page (strMode=2 上市 / 4 上櫃, Big5) → TWSE/TPEx OpenAPI issuer list →
 *           previous directory (seeded from the last deployment) → tracked public/symbols/taiwan.json.
 *   US:     NasdaqTrader nasdaqlisted.txt + otherlisted.txt → previous directory.
 * The file is written atomically; if no market has any data the previous file is left untouched.
 */
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import { createSourceFetcher, fixtureDirFromEnv, type SourceFetcher } from './lib/source-fetcher';
import {
  buildDirectoryDocument,
  directoryItemsFromDocument,
  parseIsinHtml,
  taiwanItemsFromCatalog,
  taiwanItemsFromIsin,
  taiwanItemsFromIssuerList,
  usItemsFromNasdaqListed,
  usItemsFromOtherListed,
  type DirectoryItem,
} from './symbol-directory';

const OUT = process.env.ATLAS_DIRECTORY_OUT || 'public/symbols/directory.json';
/** Minimum plausible counts; a smaller result means a broken/partial source. Fixture runs use 1. */
const MINIMUM = { TWSE: 700, TPEx: 500, US: 3000 } as const;

async function readJson(path: string): Promise<unknown> {
  try {
    return JSON.parse(await readFile(path, 'utf8'));
  } catch {
    return undefined;
  }
}

type Market = 'TWSE' | 'TPEx' | 'US';
interface Attempt {
  source: string;
  load: () => Promise<DirectoryItem[]>;
}

async function firstGood(market: Market, attempts: Attempt[], minimum: number, warnings: string[]) {
  for (const attempt of attempts) {
    try {
      const items = await attempt.load();
      if (items.length >= minimum) return { items, source: attempt.source };
      warnings.push(`${market}: ${attempt.source} returned only ${items.length} rows`);
    } catch (error) {
      warnings.push(`${market}: ${attempt.source} failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  return { items: [] as DirectoryItem[], source: 'unavailable' };
}

async function buildDirectory(fetcher: SourceFetcher, previous: unknown, catalog: unknown) {
  const minimum = fetcher.offline ? { TWSE: 1, TPEx: 1, US: 1 } : MINIMUM;
  const warnings: string[] = [];
  const previousItems = directoryItemsFromDocument(previous);
  const fromPrevious = (filter: (item: DirectoryItem) => boolean) => async () => previousItems.filter(filter);
  const isin = (mode: 2 | 4, exchange: 'TWSE' | 'TPEx'): Attempt => ({
    source: `https://isin.twse.com.tw/isin/C_public.jsp?strMode=${mode}`,
    load: async () => {
      const html = await fetcher.text(`https://isin.twse.com.tw/isin/C_public.jsp?strMode=${mode}`, {
        fixture: `isin-${mode}.utf8.html`,
        encoding: 'big5',
      });
      return taiwanItemsFromIsin(parseIsinHtml(html ?? ''), exchange);
    },
  });
  const issuers = (url: string, fixture: string, exchange: 'TWSE' | 'TPEx'): Attempt => ({
    source: url,
    load: async () => {
      const rows = await fetcher.json(url, { fixture });
      if (!Array.isArray(rows)) throw new Error('unexpected payload');
      return taiwanItemsFromIssuerList(rows, exchange);
    },
  });
  const twse = await firstGood('TWSE', [
    isin(2, 'TWSE'),
    issuers('https://openapi.twse.com.tw/v1/opendata/t187ap03_L', 't187ap03_L.json', 'TWSE'),
    { source: 'previous directory', load: fromPrevious((item) => item[2] === 'TWSE') },
    { source: 'public/symbols/taiwan.json', load: async () => taiwanItemsFromCatalog(catalog, 'TWSE') },
  ], minimum.TWSE, warnings);
  const tpex = await firstGood('TPEx', [
    isin(4, 'TPEx'),
    issuers('https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O', 'mopsfin_t187ap03_O.json', 'TPEx'),
    { source: 'previous directory', load: fromPrevious((item) => item[2] === 'TPEx') },
    { source: 'public/symbols/taiwan.json', load: async () => taiwanItemsFromCatalog(catalog, 'TPEx') },
  ], minimum.TPEx, warnings);
  const us = await firstGood('US', [
    {
      source: 'https://www.nasdaqtrader.com/dynamic/SymDir/{nasdaqlisted,otherlisted}.txt',
      load: async () => {
        const nasdaq = await fetcher.text('https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt', { fixture: 'nasdaqlisted.txt' });
        const other = await fetcher.text('https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt', { fixture: 'otherlisted.txt' });
        return [...usItemsFromNasdaqListed(nasdaq ?? ''), ...usItemsFromOtherListed(other ?? '')];
      },
    },
    { source: 'previous directory', load: fromPrevious((item) => item[2] !== 'TWSE' && item[2] !== 'TPEx') },
  ], minimum.US, warnings);
  const document = buildDirectoryDocument(
    { TWSE: twse, TPEx: tpex, US: us },
    new Date(Number(process.env.ATLAS_NOW_MS) || Date.now()).toISOString(),
  );
  return { document, warnings };
}

async function main() {
  const fixtureDir = fixtureDirFromEnv();
  const fetcher = createSourceFetcher({ fixtureDir });
  try {
    const previous = await readJson(OUT);
    const catalog = await readJson('public/symbols/taiwan.json');
    const { document, warnings } = await buildDirectory(fetcher, previous, catalog);
    for (const warning of warnings) console.log(`::warning::symbol directory: ${warning}`);
    if (!document.items.length) throw new Error('No market produced any symbol; keeping the previous directory');
    await mkdir(dirname(OUT), { recursive: true });
    const body = `${JSON.stringify(document)}\n`;
    await writeFile(`${OUT}.tmp`, body, 'utf8');
    await rename(`${OUT}.tmp`, OUT);
    console.log(JSON.stringify({ path: OUT, bytes: Buffer.byteLength(body), counts: document.counts, sources: document.sources }));
  } finally {
    await fetcher.close();
  }
}

await main();
