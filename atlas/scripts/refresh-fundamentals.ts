import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { getMarketProfile, normalizeSymbol } from '../src/market-data/MarketDataProvider';
import { FinancialNormalizer } from '../src/fundamentals/FinancialNormalizer';
import { createSecClient, type SecClient } from '../src/fundamentals/SecClient';
import { financialSnapshotSchema, type FinancialSnapshot } from '../src/fundamentals/FinancialSnapshotSchema';

const TTL_SECONDS = 24 * 60 * 60;
const DEFAULT_USER_AGENT = 'Atlas Research Terminal/1.0 (+https://github.com/wudn9922/lightweight-drawing-lab)';
const manifestStatuses = ['fresh', 'retained', 'unavailable', 'unsupported'] as const;

export type FinancialRefreshStatus = (typeof manifestStatuses)[number];
export interface FinancialRefreshEntry {
  status: FinancialRefreshStatus;
  attemptedAt: number;
  generatedAt: number | null;
  fetchedAt: number | null;
  quarterlyRecords: number;
  annualRecords: number;
  packRetained?: boolean;
  error?: string;
}
export interface FinancialRefreshSummary {
  version: 1;
  attemptedAt: number;
  source: string;
  symbols: Record<string, FinancialRefreshEntry>;
}
export interface FinancialRefreshOptions {
  requested?: readonly string[];
  allowlist?: readonly string[];
  dataDir?: string;
  seedDirectory?: string;
  manifestFile?: string;
  now?: () => number;
  client?: Pick<SecClient, 'companyFacts'>;
  closeClient?: () => void | Promise<void>;
  normalizer?: FinancialNormalizer;
}

function sourceFor(cik: string) {
  return `https://data.sec.gov/api/xbrl/companyfacts/CIK${cik}.json`;
}

function readOldManifest(value: unknown): Record<string, FinancialRefreshEntry> {
  if (!value || typeof value !== 'object' || !('symbols' in value) || !value.symbols || typeof value.symbols !== 'object') return {};
  const entries: Record<string, FinancialRefreshEntry> = {};
  for (const [symbol, raw] of Object.entries(value.symbols)) {
    if (!raw || typeof raw !== 'object') continue;
    const item = raw as Partial<FinancialRefreshEntry>;
    if (
      !manifestStatuses.includes(item.status as FinancialRefreshStatus) ||
      !Number.isFinite(item.attemptedAt) ||
      !(item.generatedAt === null || Number.isFinite(item.generatedAt)) ||
      !(item.fetchedAt === null || Number.isFinite(item.fetchedAt)) ||
      !Number.isInteger(item.quarterlyRecords) ||
      !Number.isInteger(item.annualRecords)
    ) continue;
    entries[symbol] = item as FinancialRefreshEntry;
  }
  return entries;
}

async function atomicWrite(path: string, value: unknown) {
  const temporary = `${path}.tmp`;
  await writeFile(temporary, JSON.stringify(value));
  await rename(temporary, path);
}

async function readSnapshot(path: string, expectedSymbol: string): Promise<FinancialSnapshot | undefined> {
  try {
    const snapshot = financialSnapshotSchema.parse(JSON.parse(await readFile(path, 'utf8')));
    return snapshot.symbol === expectedSymbol ? snapshot : undefined;
  } catch {
    return undefined;
  }
}

function entryFor(status: FinancialRefreshStatus, attemptedAt: number, snapshot?: FinancialSnapshot): FinancialRefreshEntry {
  return {
    status,
    attemptedAt,
    generatedAt: snapshot?.generatedAt ?? null,
    fetchedAt: snapshot?.fetchedAt ?? null,
    quarterlyRecords: snapshot?.quarterly.length ?? 0,
    annualRecords: snapshot?.annual.length ?? 0,
    ...(status === 'unsupported' && snapshot ? { packRetained: true } : {}),
  };
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

/** Collects allowlisted issuers as one companyfacts fetch and atomically replaces complete packs. */
export async function refreshFundamentals(options: FinancialRefreshOptions = {}): Promise<FinancialRefreshSummary> {
  const now = options.now ?? Date.now;
  const dataDir = options.dataDir ?? 'public/financial-data';
  const seedDirectory = options.seedDirectory ?? resolve('data/financial-snapshots');
  const manifestFile = options.manifestFile ?? join(dataDir, 'manifest.json');
  const allowlist = options.allowlist
    ? options.allowlist.map(normalizeSymbol)
    : (JSON.parse(await readFile('scripts/market-symbols.json', 'utf8')) as unknown[])
      .map((value) => normalizeSymbol(String(value)))
      .filter((symbol) => getMarketProfile(symbol).market === 'US');
  if (new Set(allowlist).size !== allowlist.length) throw new Error('SEC snapshot allowlist contains duplicate symbols');
  const requested = options.requested?.length ? options.requested.map(normalizeSymbol) : [...allowlist];
  if (requested.some((symbol) => !allowlist.includes(symbol))) {
    throw new Error('Requested symbol is not in the public financial snapshot allowlist');
  }
  await mkdir(dataDir, { recursive: true });
  const oldManifest = await readFile(manifestFile, 'utf8')
    .then((json) => readOldManifest(JSON.parse(json)))
    .catch(() => ({}));
  const symbols: Record<string, FinancialRefreshEntry> = Object.fromEntries(
    Object.entries(oldManifest).filter(([symbol]) => getMarketProfile(symbol).market === 'US'),
  );
  const normalizer = options.normalizer ?? new FinancialNormalizer();
  let client = options.client;
  let close = options.closeClient;
  if (!client) {
    const connection = createSecClient(process.env.SEC_USER_AGENT ?? DEFAULT_USER_AGENT);
    client = connection.client;
    close = connection.close;
  }
  try {
    for (const symbol of requested) {
      const attemptedAt = Math.floor(now() / 1000);
      const path = join(dataDir, `${symbol}.json`);
      if (getMarketProfile(symbol).market !== 'US') {
        const entry = entryFor('unsupported', attemptedAt);
        entry.error = 'SEC financial snapshots currently support US market tickers only.';
        symbols[symbol] = entry;
        continue;
      }

      let previous = await readSnapshot(path, symbol);
      const seedPath = join(seedDirectory, `${symbol}.json`);
      if (resolve(seedPath) !== resolve(path)) {
        const seed = await readSnapshot(seedPath, symbol);
        if (seed && (!previous || seed.fetchedAt > previous.fetchedAt)) {
          try {
            await atomicWrite(path, seed);
            previous = seed;
          } catch {
            // A seed copy failure is isolated to this issuer; continue with existing data or SEC.
          }
        }
      }
      if (previous && attemptedAt - previous.generatedAt < TTL_SECONDS) {
        symbols[symbol] = entryFor('fresh', attemptedAt, previous);
        continue;
      }
      try {
        const company = await client.companyFacts(symbol);
        if (!company) {
          const entry = entryFor('unsupported', attemptedAt, previous);
          entry.error = 'Ticker is absent from the official SEC company_tickers mapping.';
          symbols[symbol] = entry;
          continue;
        }
        const cik = company.cik;
        if (!/^[0-9]{10}$/.test(cik) || Number((company.data as { cik?: unknown } | null)?.cik) !== Number(cik)) {
          throw new Error('SEC companyfacts identity did not match the official ticker mapping.');
        }
        const fetchedAt = Math.floor(now() / 1000);
        const quarterly = normalizer.normalize(company.data, symbol, 'quarterly')
          .sort((a, b) => b.periodEnd.localeCompare(a.periodEnd) || b.filingDate.localeCompare(a.filingDate))
          .slice(0, 100);
        const annual = normalizer.normalize(company.data, symbol, 'annual')
          .sort((a, b) => b.periodEnd.localeCompare(a.periodEnd) || b.filingDate.localeCompare(a.filingDate))
          .slice(0, 100);
        if (quarterly.length + annual.length === 0) {
          const entry = entryFor('unsupported', attemptedAt, previous);
          entry.error = 'No supported periods. This collector maps US-GAAP companyfacts from 10-K/10-Q filings; other forms and taxonomies may be unsupported.';
          symbols[symbol] = entry;
          continue;
        }
        const issuerName = quarterly[0]?.issuerName ?? annual[0]?.issuerName;
        if (!issuerName?.trim()) throw new Error('SEC companyfacts did not provide an issuer name.');
        const snapshot = financialSnapshotSchema.parse({
          version: 1,
          symbol,
          cik,
          issuerName,
          source: sourceFor(cik),
          fetchedAt,
          generatedAt: Math.floor(now() / 1000),
          quarterly,
          annual,
        });
        await atomicWrite(path, snapshot);
        symbols[symbol] = entryFor('fresh', attemptedAt, snapshot);
      } catch (error) {
        const entry = entryFor(previous ? 'retained' : 'unavailable', attemptedAt, previous);
        entry.error = errorText(error);
        symbols[symbol] = entry;
      }
    }
  } finally {
    try {
      await close?.();
    } catch {
      // A cleanup failure must not turn individual issuer failures into a failed refresh.
    }
  }
  const summary: FinancialRefreshSummary = {
    version: 1,
    attemptedAt: Math.floor(now() / 1000),
    source: 'SEC EDGAR companyfacts; mapped US-GAAP 10-K/10-Q facts, with provenance retained.',
    symbols,
  };
  await atomicWrite(manifestFile, summary);
  return structuredClone(summary);
}
