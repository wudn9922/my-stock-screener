/**
 * Builds public/valuation/pe-river-tw.json and public/valuation/pe-river-us.json: for every stock with a
 * financials file, where the current P/E sits on its own P/E river (本益比河流圖) — the same `buildPeRiver`
 * model the stock page draws, fed with weekly bars from the permanent K-line archive.
 *
 * Inputs (env): ATLAS_ARCHIVE_DIR (a kline-archive checkout, default ../.cache/kline-archive),
 *               ATLAS_FINANCIALS_DIR (default ../docs/report/financials),
 *               ATLAS_VALUATION_OUT_DIR (default public/valuation).
 * Read-only: nothing in the archive or the financials directory is modified. A market whose run cannot
 * read the archive at all keeps the previously deployed file (the step only replaces a file it rebuilt).
 *
 * The stock page also appends older Yahoo weekly bars (up to 10 years); this build uses the archive alone
 * (2021 onwards), so a stock's percentile here can differ slightly from the chart. The file says so.
 */
import { mkdir, readFile, readdir, rename, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { parseFinancials } from '../src/fundamentals/FinancialHistory';
import { buildPeRiver } from '../src/fundamentals/peRiver';
import { KlineArchive } from '../src/market-data/KlineArchive';
import { archivePeriod } from '../src/market-data/ArchiveFirstProvider';
import { PE_RIVER_FILE_VERSION, type PeRiverFile, type PeRiverMarket } from '../src/fundamentals/peRiverRank';

const ARCHIVE_DIR = resolve(process.env.ATLAS_ARCHIVE_DIR || '../.cache/kline-archive');
const FINANCIALS_DIR = resolve(process.env.ATLAS_FINANCIALS_DIR || '../docs/report/financials');
const OUT_DIR = resolve(process.env.ATLAS_VALUATION_OUT_DIR || 'public/valuation');
const BASE = 'archive-local:/';
const CONCURRENCY = 16;

/** Serves the archive checkout to KlineArchive; anything else (e.g. the closure report) is a 404. */
const localFetch: typeof fetch = async (input) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
  if (!url.startsWith(BASE)) return new Response(null, { status: 404 });
  // Paths arrive URL-encoded once more than on disk (KlineArchive encodes '%' again for HTTP).
  const path = decodeURIComponent(url.slice(BASE.length));
  try {
    return new Response(await readFile(join(ARCHIVE_DIR, path), 'utf8'), { status: 200 });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return new Response(null, { status: 404 });
    throw error;
  }
};

async function rankOne(file: string, archive: KlineArchive) {
  const parsed = parseFinancials(JSON.parse(await readFile(join(FINANCIALS_DIR, file), 'utf8')), file);
  if (parsed.status !== 'ok') return { status: 'no-financials' as const };
  const history = parsed.data;
  const symbol = history.symbol;
  const daily = await archive.getDaily(symbol);
  if (!daily) return { status: 'no-archive' as const, symbol, market: history.market };
  const weekly = archivePeriod(daily, symbol, '1W');
  const last = weekly.bars.at(-1);
  // Same data date as the stock page (loadWeeklyPrices): quote time, then retrieval time, then the last bar.
  const asOfSec = weekly.quote?.asOf ?? weekly.asOf ?? last?.time ?? 0;
  const result = buildPeRiver(history, weekly.bars.map((bar) => ({ time: bar.time, close: bar.close })),
    { asOfSec, priceBasis: weekly.priceBasis });
  if (result.status !== 'ok') return { status: result.reason, symbol, market: history.market };
  const current = result.model.current;
  if (!current) return { status: 'no-current' as const, symbol, market: history.market };
  return {
    status: 'ok' as const,
    symbol,
    market: history.market,
    lastDate: weekly.archive?.lastDate ?? null,
    row: [Math.round(current.percentile * 10) / 10, result.model.short ? 1 : 0] as [number, 0 | 1],
  };
}

async function writeJson(path: string, value: unknown) {
  const body = JSON.stringify(value);
  await writeFile(`${path}.tmp`, body, 'utf8');
  await rename(`${path}.tmp`, path);
  return body.length;
}

async function main() {
  const files = (await readdir(FINANCIALS_DIR)).filter((name) => name.endsWith('.json')).sort();
  const archive = new KlineArchive({ baseUrl: BASE, fetcher: localFetch, cache: false, timeoutMs: 60_000 });
  const items: Record<PeRiverMarket, Record<string, [number, 0 | 1]>> = { TW: {}, US: {} };
  const lastDates: Record<PeRiverMarket, string> = { TW: '', US: '' };
  const counts: Record<string, number> = {};
  let failures = 0;
  let next = 0;
  await Promise.all(Array.from({ length: CONCURRENCY }, async () => {
    while (next < files.length) {
      const file = files[next++]!;
      try {
        const outcome = await rankOne(file, archive);
        counts[outcome.status] = (counts[outcome.status] ?? 0) + 1;
        if (outcome.status === 'ok') {
          items[outcome.market][outcome.symbol] = outcome.row;
          if (outcome.lastDate && outcome.lastDate > lastDates[outcome.market]) lastDates[outcome.market] = outcome.lastDate;
        }
      } catch {
        failures++;
        counts.error = (counts.error ?? 0) + 1;
      }
    }
  }));
  console.log(`本益比河流分位：${files.length} 個財報檔`, JSON.stringify(counts));
  await mkdir(OUT_DIR, { recursive: true });
  for (const market of ['TW', 'US'] as const) {
    const total = Object.keys(items[market]).length;
    if (!total) {
      console.warn(`${market}：沒有任何股票算出目前河流分位，保留上次部署的檔案`);
      continue;
    }
    const doc: PeRiverFile = {
      version: PE_RIVER_FILE_VERSION,
      market,
      generatedAt: new Date().toISOString(),
      priceThrough: lastDates[market] || null,
      basis: '永久封存週線（2021 年起）；個股頁另併 Yahoo 較早週線，分位可能略有差異',
      items: items[market],
    };
    const bytes = await writeJson(join(OUT_DIR, `pe-river-${market.toLowerCase()}.json`), doc);
    console.log(`${market}：${total} 檔，${bytes} bytes`);
  }
  // Unreadable inputs are a partial failure for the workflow's notice, never silent.
  if (failures > files.length / 2) process.exitCode = 1;
}

await main();
