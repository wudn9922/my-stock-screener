import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { normalizeSymbol } from '../src/market-data/MarketDataProvider';
import { FinancialNormalizer } from '../src/fundamentals/FinancialNormalizer';
import { createSecClient, type SecClient } from '../src/fundamentals/SecClient';
import { financialSchema } from '../src/fundamentals/financialSchema';
import { financialSnapshotSchema } from '../src/fundamentals/FinancialSnapshotSchema';
import { isSecEligibleSymbol } from '../src/fundamentals/SecEligibility';
import type { CompanyFundamentals, FinancialPeriod } from '../src/fundamentals/FundamentalsProvider';

/**
 * Static SEC financials for the my-stock-screener Pages build.
 * For each symbol in scripts/market-symbols.json writes
 *   public/fundamentals/<SYMBOL>-annual.json and <SYMBOL>-quarterly.json
 * containing exactly what `/api/fundamentals` returns (FinancialNormalizer output validated by
 * financialSchema), plus public/fundamentals/manifest.json. Files carry no timestamps and are only
 * rewritten when the normalized records change, so a daily job does not churn identical data.
 */
export const DEFAULT_SEC_USER_AGENT =
  'my-stock-screener-atlas (https://github.com/wudn9922/my-stock-screener)';
export const DEFAULT_MAX_AGE_SECONDS = 20 * 60 * 60;
const periods: readonly FinancialPeriod[] = ['annual', 'quarterly'];

export type FundamentalsBuildStatus =
  | 'fresh' // SEC checked this run (or within maxAge); files reflect SEC companyfacts.
  | 'retained' // SEC failed; previously built files kept unchanged.
  | 'seed' // SEC failed and nothing was built before; versioned upstream seed pack used.
  | 'skipped' // Taiwan listing or ^ index: SEC filings cannot exist, SEC not called.
  | 'no-cik' // Not in the official SEC ticker map (e.g. many ETFs/foreign issuers); nothing written.
  | 'no-data' // CIK found, but no supported US-GAAP 10-K/10-Q periods; nothing written.
  | 'unavailable'; // SEC failed and no prior file or seed exists.

export interface FundamentalsBuildEntry {
  status: FundamentalsBuildStatus;
  /** Unix seconds of the last successful SEC companyfacts check behind these files, if known. */
  checkedAt: number | null;
  /** Unix seconds when the facts in the files were fetched from SEC, if known. */
  fetchedAt: number | null;
  changed: boolean;
  annualRecords: number;
  quarterlyRecords: number;
  cik?: string;
  issuerName?: string;
  source?: string;
  error?: string;
}

export interface FundamentalsBuildManifest {
  version: 1;
  attemptedAt: number;
  source: string;
  symbols: Record<string, FundamentalsBuildEntry>;
}

export interface BuildFundamentalsOptions {
  symbols?: readonly string[];
  symbolsFile?: string;
  outDir?: string;
  seedDirectory?: string;
  maxAgeSeconds?: number;
  now?: () => number;
  client?: Pick<SecClient, 'companyFacts'>;
  closeClient?: () => void | Promise<void>;
  normalizer?: FinancialNormalizer;
  log?: (message: string) => void;
}

export function fundamentalsFileName(symbol: string, period: FinancialPeriod): string {
  return `${symbol}-${period}.json`;
}

async function atomicWrite(path: string, text: string) {
  const temporary = `${path}.tmp`;
  await writeFile(temporary, text);
  await rename(temporary, path);
}

async function readRecords(path: string, symbol: string, period: FinancialPeriod) {
  try {
    const text = await readFile(path, 'utf8');
    const records = financialSchema.array().parse(JSON.parse(text));
    if (records.some((record) => record.symbol !== symbol || record.period !== period)) return undefined;
    return { text, records };
  } catch {
    return undefined;
  }
}

async function readOldManifest(path: string): Promise<Record<string, Partial<FundamentalsBuildEntry>>> {
  try {
    const value = JSON.parse(await readFile(path, 'utf8')) as { symbols?: unknown };
    return value.symbols && typeof value.symbols === 'object'
      ? (value.symbols as Record<string, Partial<FundamentalsBuildEntry>>)
      : {};
  } catch {
    return {};
  }
}

function finiteOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
}

function message(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

export async function buildFundamentals(options: BuildFundamentalsOptions = {}): Promise<FundamentalsBuildManifest> {
  const now = options.now ?? Date.now;
  const log = options.log ?? console.log;
  const outDir = options.outDir ?? 'public/fundamentals';
  const seedDirectory = options.seedDirectory ?? resolve('data/financial-snapshots');
  const maxAge = options.maxAgeSeconds ?? DEFAULT_MAX_AGE_SECONDS;
  const manifestPath = join(outDir, 'manifest.json');
  const rawSymbols = options.symbols ??
    (JSON.parse(await readFile(options.symbolsFile ?? 'scripts/market-symbols.json', 'utf8')) as unknown[]).map(String);
  const symbols: string[] = [];
  for (const raw of rawSymbols) {
    try {
      const symbol = normalizeSymbol(raw);
      if (!symbols.includes(symbol)) symbols.push(symbol);
    } catch {
      log(`skip invalid symbol ${JSON.stringify(raw)}`);
    }
  }
  await mkdir(outDir, { recursive: true });
  const oldManifest = await readOldManifest(manifestPath);
  const normalizer = options.normalizer ?? new FinancialNormalizer();
  let client = options.client;
  let close = options.closeClient;
  const result: Record<string, FundamentalsBuildEntry> = {};

  const ensureClient = () => {
    if (!client) {
      const connection = createSecClient(process.env.SEC_USER_AGENT?.trim() || DEFAULT_SEC_USER_AGENT);
      client = connection.client;
      close = connection.close;
    }
    return client;
  };

  try {
    for (const symbol of symbols) {
      const attemptedAt = Math.floor(now() / 1000);
      const previousEntry = oldManifest[symbol] ?? {};
      if (!isSecEligibleSymbol(symbol)) {
        result[symbol] = { status: 'skipped', checkedAt: null, fetchedAt: null, changed: false, annualRecords: 0, quarterlyRecords: 0 };
        continue;
      }
      const paths = Object.fromEntries(periods.map((period) => [period, join(outDir, fundamentalsFileName(symbol, period))])) as Record<FinancialPeriod, string>;
      const previous = {
        annual: await readRecords(paths.annual, symbol, 'annual'),
        quarterly: await readRecords(paths.quarterly, symbol, 'quarterly'),
      };
      const hasPrevious = !!previous.annual || !!previous.quarterly;
      const previousCheckedAt = finiteOrNull(previousEntry.checkedAt);
      const counts = () => ({
        annualRecords: previous.annual?.records.length ?? 0,
        quarterlyRecords: previous.quarterly?.records.length ?? 0,
      });
      const identity = () => ({
        ...(previousEntry.cik ? { cik: previousEntry.cik } : {}),
        ...(previousEntry.issuerName ? { issuerName: previousEntry.issuerName } : {}),
        ...(previousEntry.source ? { source: previousEntry.source } : {}),
      });
      if (hasPrevious && previousEntry.status === 'fresh' && previousCheckedAt && attemptedAt - previousCheckedAt < maxAge) {
        result[symbol] = { status: 'fresh', checkedAt: previousCheckedAt, fetchedAt: finiteOrNull(previousEntry.fetchedAt), changed: false, ...counts(), ...identity() };
        continue;
      }
      try {
        const company = await ensureClient().companyFacts(symbol);
        if (!company) {
          result[symbol] = { status: 'no-cik', checkedAt: attemptedAt, fetchedAt: null, changed: false, annualRecords: 0, quarterlyRecords: 0 };
          continue;
        }
        const fetchedAt = Math.floor(now() / 1000);
        const records = {} as Record<FinancialPeriod, CompanyFundamentals[]>;
        for (const period of periods) {
          records[period] = financialSchema.array().parse(normalizer.normalize(company.data, symbol, period));
          if (records[period].some((record) => record.symbol !== symbol || record.period !== period))
            throw new Error('Normalized records do not match the requested symbol/period');
        }
        if (!records.annual.length && !records.quarterly.length) {
          result[symbol] = { status: 'no-data', checkedAt: fetchedAt, fetchedAt, changed: false, annualRecords: 0, quarterlyRecords: 0, cik: company.cik };
          continue;
        }
        let changed = false;
        for (const period of periods) {
          const text = JSON.stringify(records[period]);
          if (previous[period]?.text === text) continue;
          await atomicWrite(paths[period], text);
          changed = true;
        }
        const first = records.quarterly[0] ?? records.annual[0];
        result[symbol] = {
          status: 'fresh',
          checkedAt: fetchedAt,
          // Unchanged files keep the fetch time of the facts they were built from.
          fetchedAt: changed ? fetchedAt : finiteOrNull(previousEntry.fetchedAt) ?? fetchedAt,
          changed,
          annualRecords: records.annual.length,
          quarterlyRecords: records.quarterly.length,
          cik: company.cik,
          ...(first?.issuerName ? { issuerName: first.issuerName } : {}),
          source: `https://data.sec.gov/api/xbrl/companyfacts/CIK${company.cik}.json`,
        };
      } catch (error) {
        const errorText = message(error);
        if (hasPrevious) {
          result[symbol] = { status: 'retained', checkedAt: previousCheckedAt, fetchedAt: finiteOrNull(previousEntry.fetchedAt), changed: false, ...counts(), ...identity(), error: errorText };
          continue;
        }
        try {
          const seed = financialSnapshotSchema.parse(JSON.parse(await readFile(join(seedDirectory, `${symbol}.json`), 'utf8')));
          if (seed.symbol !== symbol) throw new Error('Seed symbol mismatch');
          for (const period of periods) await atomicWrite(paths[period], JSON.stringify(seed[period]));
          result[symbol] = {
            status: 'seed', checkedAt: seed.generatedAt, fetchedAt: seed.fetchedAt, changed: true,
            annualRecords: seed.annual.length, quarterlyRecords: seed.quarterly.length,
            cik: seed.cik, issuerName: seed.issuerName, source: seed.source, error: errorText,
          };
        } catch {
          result[symbol] = { status: 'unavailable', checkedAt: null, fetchedAt: null, changed: false, annualRecords: 0, quarterlyRecords: 0, error: errorText };
        }
      }
    }
  } finally {
    try {
      await close?.();
    } catch {
      // Cleanup failure must not turn per-symbol results into a failed build.
    }
  }
  for (const [symbol, entry] of Object.entries(result)) {
    if (entry.status !== 'skipped') log(`${symbol}: ${JSON.stringify(entry)}`);
  }
  const manifest: FundamentalsBuildManifest = {
    version: 1,
    attemptedAt: Math.floor(now() / 1000),
    source: 'SEC EDGAR companyfacts normalized by Atlas FinancialNormalizer (10-K/10-Q, US-GAAP); not real-time.',
    // A run for selected symbols keeps the other symbols' previous entries.
    symbols: options.symbols
      ? { ...(oldManifest as Record<string, FundamentalsBuildEntry>), ...result }
      : result,
  };
  await atomicWrite(manifestPath, JSON.stringify(manifest));
  return manifest;
}
