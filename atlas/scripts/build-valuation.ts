/**
 * Builds public/valuation/us.json and public/valuation/tw.json (git-ignored; built in GitHub Actions).
 * Only what P/E and P/E TTM need — see scripts/valuation.ts for the method and its limitations.
 *
 *   node scripts/build-valuation.mjs [--market=us|tw|all] [--fixtures=tests/fixtures/valuation]
 *   Env: SEC_USER_AGENT (default "my-stock-screener-atlas (https://github.com/wudn9922/my-stock-screener)"),
 *        ATLAS_VALUATION_OUT_DIR (default public/valuation), ATLAS_TODAY (YYYY-MM-DD, fixtures/tests),
 *        ATLAS_FIXTURE_DIR (same as --fixtures).
 *
 * A market whose sources fail keeps its previous file (the workflow seeds it from the last deployment);
 * the script then exits 1 so the workflow shows a warning.
 */
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { createSourceFetcher, fixtureDirFromEnv, type SourceFetcher } from './lib/source-fetcher';
import { directoryItemsFromDocument } from './symbol-directory';
import {
  addFrame,
  annualYearsToFetch,
  buildTaiwanRecords,
  computeUsValuation,
  frameName,
  parseCompanyTickers,
  parseFramePoints,
  parseTaiwanAnnualEps,
  parseTaiwanClose,
  parseTaiwanPe,
  quarterSlotsToFetch,
  type CompanyEpsFacts,
  type ValuationDocument,
  type ValuationRecord,
} from './valuation';

const OUT_DIR = process.env.ATLAS_VALUATION_OUT_DIR || 'public/valuation';
const SEC_USER_AGENT = process.env.SEC_USER_AGENT?.trim() || 'my-stock-screener-atlas (https://github.com/wudn9922/my-stock-screener)';
const CONCEPTS = ['EarningsPerShareDiluted', 'EarningsPerShareBasic'] as const;

function today(): { ms: number; iso: string } {
  const fixed = process.env.ATLAS_TODAY;
  const ms = fixed && /^\d{4}-\d{2}-\d{2}$/.test(fixed) ? Date.parse(`${fixed}T12:00:00Z`) : Date.now();
  return { ms, iso: new Date(ms).toISOString().slice(0, 10) };
}

async function readJson(path: string): Promise<unknown> {
  try {
    return JSON.parse(await readFile(path, 'utf8'));
  } catch {
    return undefined;
  }
}

async function writeAtomic(path: string, value: unknown): Promise<number> {
  const body = `${JSON.stringify(value)}\n`;
  await mkdir(OUT_DIR, { recursive: true });
  await writeFile(`${path}.tmp`, body, 'utf8');
  await rename(`${path}.tmp`, path);
  return Buffer.byteLength(body);
}

async function buildUs(fetcher: SourceFetcher, todayMs: number, allowed: ReadonlySet<string> | null) {
  const tickers = parseCompanyTickers(
    await fetcher.json('https://www.sec.gov/files/company_tickers.json', { fixture: 'company_tickers.json', headers: { 'User-Agent': SEC_USER_AGENT } }),
  );
  if (!tickers.size) throw new Error('company_tickers.json returned no tickers');
  const facts: Record<(typeof CONCEPTS)[number], Map<number, CompanyEpsFacts>> = {
    EarningsPerShareDiluted: new Map(),
    EarningsPerShareBasic: new Map(),
  };
  let frames = 0;
  for (const concept of CONCEPTS) {
    const periods: { name: string; slot?: number }[] = [
      ...quarterSlotsToFetch(todayMs).map((slot) => ({ name: frameName(slot), slot })),
      ...annualYearsToFetch(todayMs).map((year) => ({ name: `CY${year}` })),
    ];
    for (const period of periods) {
      const url = `https://data.sec.gov/api/xbrl/frames/us-gaap/${concept}/USD-per-shares/${period.name}.json`;
      const raw = await fetcher.json(url, {
        fixture: `frames-${concept}-${period.name}.json`,
        headers: { 'User-Agent': SEC_USER_AGENT, Accept: 'application/json' },
        emptyOn: [404],
      });
      if (raw === null) continue;
      frames++;
      addFrame(facts[concept], parseFramePoints(raw), period.slot);
    }
  }
  if (!frames) throw new Error('SEC returned no frames');
  const items: Record<string, ValuationRecord> = {};
  const ciks = new Set([...facts.EarningsPerShareDiluted.keys(), ...facts.EarningsPerShareBasic.keys()]);
  for (const cik of ciks) {
    const symbols = (tickers.get(cik) ?? []).filter((symbol) => !allowed || allowed.has(symbol));
    if (!symbols.length) continue;
    const record = computeUsValuation(facts.EarningsPerShareDiluted.get(cik), facts.EarningsPerShareBasic.get(cik), todayMs);
    if (!record) continue;
    for (const symbol of symbols) items[symbol] = record;
  }
  return { items, frames };
}

async function buildTaiwanExchange(
  fetcher: SourceFetcher,
  exchange: 'TWSE' | 'TPEx',
  previous: Record<string, ValuationRecord>,
  fallbackDate: string,
) {
  const urls = exchange === 'TWSE'
    ? {
        pe: ['https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL', 'BWIBBU_ALL.json'],
        close: ['https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL', 'STOCK_DAY_ALL.json'],
        eps: ['https://openapi.twse.com.tw/v1/opendata/t187ap14_L', 't187ap14_L.json'],
      }
    : {
        pe: ['https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis', 'tpex_mainboard_peratio_analysis.json'],
        close: ['https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes', 'tpex_mainboard_daily_close_quotes.json'],
        eps: ['https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap14_O', 'mopsfin_t187ap14_O.json'],
      };
  const pe = parseTaiwanPe(await fetcher.json(urls.pe[0]!, { fixture: urls.pe[1]! }));
  // Prices and full-year EPS are optional: without them only the exchange P/E is published.
  const close = await fetcher.json(urls.close[0]!, { fixture: urls.close[1]! }).then(parseTaiwanClose, () => new Map());
  const annual = await fetcher.json(urls.eps[0]!, { fixture: urls.eps[1]! }).then(parseTaiwanAnnualEps, () => new Map());
  const previousForExchange = Object.fromEntries(Object.entries(previous).filter(([, record]) => record.source === exchange));
  return {
    items: buildTaiwanRecords({ exchange, pe, close, annual, previous: previousForExchange, fallbackDate }),
    stats: { pe: pe.length, close: close.size, annualEps: annual.size },
  };
}

async function main() {
  const marketArg = process.argv.find((arg) => arg.startsWith('--market='))?.slice('--market='.length) ?? 'all';
  const markets = marketArg === 'all' ? ['us', 'tw'] : [marketArg];
  if (markets.some((market) => !['us', 'tw'].includes(market))) throw new Error(`Unknown --market=${marketArg}`);
  const fixtureDir = fixtureDirFromEnv();
  const offline = !!fixtureDir;
  const { ms: todayMs, iso: todayIso } = today();
  const generatedAt = new Date(Number(process.env.ATLAS_NOW_MS) || Date.now()).toISOString();
  let failed = false;

  if (markets.includes('us')) {
    // SEC fair-access policy: ≤ 10 requests/second with a descriptive User-Agent; we stay near 6/s.
    const fetcher = createSourceFetcher({ fixtureDir, userAgent: SEC_USER_AGENT, minIntervalMs: 150 });
    try {
      const directory = directoryItemsFromDocument(await readJson(process.env.ATLAS_DIRECTORY_PATH || 'public/symbols/directory.json'));
      const usSymbols = directory.filter((item) => !['TWSE', 'TPEx'].includes(item[2])).map((item) => item[0]);
      const allowed = usSymbols.length >= (offline ? 1 : 1000) ? new Set(usSymbols) : null;
      const { items, frames } = await buildUs(fetcher, todayMs, allowed);
      const count = Object.keys(items).length;
      if (count < (offline ? 1 : 1000)) throw new Error(`only ${count} US records`);
      const document: ValuationDocument = {
        version: 1,
        market: 'US',
        generatedAt,
        source: 'SEC XBRL frames API (us-gaap EarningsPerShareDiluted, fallback EarningsPerShareBasic, USD/share)',
        notes: 'Calendar-aligned frames; EPS TTM = latest 4 contiguous quarters (a missing fiscal Q4 is derived from the annual value); epsAnnual = latest fiscal year.',
        items,
      };
      const bytes = await writeAtomic(join(OUT_DIR, 'us.json'), document);
      console.log(JSON.stringify({ market: 'US', records: count, frames, restrictedToDirectory: !!allowed, bytes }));
    } catch (error) {
      failed = true;
      console.log(`::warning::US valuation not rebuilt (previous file kept): ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      await fetcher.close();
    }
  }

  if (markets.includes('tw')) {
    const fetcher = createSourceFetcher({ fixtureDir, minIntervalMs: 500 });
    try {
      const previousDocument = (await readJson(join(OUT_DIR, 'tw.json'))) as Partial<ValuationDocument> | undefined;
      const previous = (previousDocument?.items ?? {}) as Record<string, ValuationRecord>;
      const items: Record<string, ValuationRecord> = {};
      const stats: Record<string, unknown> = {};
      for (const exchange of ['TWSE', 'TPEx'] as const) {
        try {
          const result = await buildTaiwanExchange(fetcher, exchange, previous, todayIso);
          const count = Object.keys(result.items).length;
          if (count < (offline ? 1 : 500)) throw new Error(`only ${count} records`);
          Object.assign(items, result.items);
          stats[exchange] = { records: count, ...result.stats };
        } catch (error) {
          failed = true;
          const kept = Object.entries(previous).filter(([, record]) => record.source === exchange);
          Object.assign(items, Object.fromEntries(kept));
          stats[exchange] = { kept: kept.length };
          console.log(`::warning::${exchange} valuation not rebuilt (kept ${kept.length} previous records): ${error instanceof Error ? error.message : String(error)}`);
        }
      }
      if (!Object.keys(items).length) throw new Error('no Taiwan records');
      const document: ValuationDocument = {
        version: 1,
        market: 'TW',
        generatedAt,
        source: 'TWSE OpenAPI BWIBBU_ALL / STOCK_DAY_ALL / t187ap14_L; TPEx OpenAPI tpex_mainboard_peratio_analysis / daily_close_quotes / mopsfin_t187ap14_O',
        notes: 'exchangePeTtm is the exchange P/E (latest four quarters); epsTtm = close / exchangePeTtm; epsAnnual only when a full-year (Q4) statement was published, kept until the next one.',
        items,
      };
      const bytes = await writeAtomic(join(OUT_DIR, 'tw.json'), document);
      console.log(JSON.stringify({ market: 'TW', records: Object.keys(items).length, stats, bytes }));
    } catch (error) {
      failed = true;
      console.log(`::warning::Taiwan valuation not rebuilt (previous file kept): ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      await fetcher.close();
    }
  }
  if (failed) process.exitCode = 1;
}

await main();
