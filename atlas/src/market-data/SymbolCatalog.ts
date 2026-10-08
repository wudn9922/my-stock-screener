import { z } from 'zod';
import { normalizeSymbol } from './MarketDataProvider';

export interface SymbolCatalogEntry {
  symbol: string;
  ticker: string;
  name: string;
  market: 'TWSE' | 'TPEx';
  kind: 'stock' | 'etf';
}

export interface SymbolCatalogSource {
  market: 'TWSE' | 'TPEx';
  kind: 'issuer-directory' | 'fund-directory';
  url: string;
  fetchedAt: string;
  sourceDate?: string;
}

export interface SymbolCatalogDocument {
  version: 1;
  fetchedAt: string;
  sources: SymbolCatalogSource[];
  entries: SymbolCatalogEntry[];
}

const entrySchema = z
  .object({
    symbol: z.string().regex(/^[0-9]{4,6}[A-Z]?\.(?:TW|TWO)$/),
    ticker: z.string().regex(/^[0-9]{4,6}[A-Z]?$/),
    name: z.string().trim().min(1).max(120),
    market: z.enum(['TWSE', 'TPEx']),
    kind: z.enum(['stock', 'etf']),
  })
  .strict()
  .refine((entry) => entry.symbol === `${entry.ticker}.${entry.market === 'TWSE' ? 'TW' : 'TWO'}`, {
    message: 'Symbol suffix must match its official exchange',
  });

const sourceSchema = z
  .object({
    market: z.enum(['TWSE', 'TPEx']),
    kind: z.enum(['issuer-directory', 'fund-directory']),
    url: z.string().url(),
    fetchedAt: z.string().datetime({ offset: true }),
    sourceDate: z.string().optional(),
  })
  .strict();

export const symbolCatalogDocumentSchema = z
  .object({
    version: z.literal(1),
    fetchedAt: z.string().datetime({ offset: true }),
    sources: z.array(sourceSchema).min(2),
    entries: z.array(entrySchema).min(1),
  })
  .strict()
  .superRefine((document, context) => {
    const seen = new Set<string>();
    for (const [index, entry] of document.entries.entries()) {
      if (seen.has(entry.symbol)) {
        context.addIssue({
          code: 'custom',
          path: ['entries', index, 'symbol'],
          message: `Duplicate symbol ${entry.symbol}`,
        });
      }
      seen.add(entry.symbol);
    }
  });

let catalogRequest: Promise<SymbolCatalogEntry[]> | null = null;

/** Fetch and validate the same-origin official Taiwan directory, sharing one in-flight load. */
export function loadSymbolCatalog(signal?: AbortSignal): Promise<SymbolCatalogEntry[]> {
  if (!catalogRequest) {
    catalogRequest = fetch(`${import.meta.env.BASE_URL}symbols/taiwan.json`, {
      cache: 'force-cache',
    })
      .then(async (response) => {
        if (!response.ok)
          throw new Error(`Taiwan symbol directory unavailable (HTTP ${response.status})`);
        const document = symbolCatalogDocumentSchema.parse(await response.json());
        return document.entries;
      })
      .catch((error: unknown) => {
        catalogRequest = null;
        throw error;
      });
  }
  const request = catalogRequest;
  const copyEntries = (entries: SymbolCatalogEntry[]) => entries.map((entry) => ({ ...entry }));
  if (!signal) return request.then(copyEntries);
  if (signal.aborted)
    return Promise.reject(signal.reason ?? new DOMException('Aborted', 'AbortError'));
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason ?? new DOMException('Aborted', 'AbortError'));
    signal.addEventListener('abort', abort, { once: true });
    request
      .then(copyEntries)
      .then(resolve, reject)
      .finally(() => signal.removeEventListener('abort', abort));
  });
}

/** Normalize a UI-entered US or explicit Taiwan ticker, resolving bare Taiwan codes only by catalog membership. */
export function resolveSymbolInput(input: string, entries: readonly SymbolCatalogEntry[]): string {
  const normalized = input.trim().toUpperCase();
  if (/^[0-9]{4,6}[A-Z]?\.(?:TW|TWO)$/.test(normalized)) return normalizeSymbol(normalized);
  if (/^[0-9]{4,6}[A-Z]?$/.test(normalized)) {
    const matches = entries.filter((entry) => entry.ticker === normalized);
    if (matches.length === 1) return matches[0]!.symbol;
    if (matches.length > 1) {
      const choices = matches
        .map((entry) => entry.symbol)
        .sort()
        .join(' or ');
      throw new Error(
        `Taiwan ticker ${normalized} is listed on more than one exchange; choose ${choices}.`,
      );
    }
    throw new Error(
      `No Taiwan listing found for ${normalized}; enter an explicit .TW or .TWO suffix.`,
    );
  }
  return normalizeSymbol(normalized);
}

/** Search official Taiwan ticker/name records, preferring exact ticker and symbol matches. */
export function searchSymbolCatalog(
  query: string,
  entries: readonly SymbolCatalogEntry[],
  limit = 30,
): SymbolCatalogEntry[] {
  if (!Number.isInteger(limit) || limit < 0)
    throw new Error('Catalog search limit must be a non-negative integer');
  if (limit === 0) return [];
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return [];
  const fullSymbol = /^[0-9]{4,6}[a-z]?\.(?:tw|two)$/.test(normalized);
  const matches = entries.filter((entry) => {
    const ticker = entry.ticker.toLocaleLowerCase();
    const symbol = entry.symbol.toLocaleLowerCase();
    const name = entry.name.toLocaleLowerCase();
    if (fullSymbol) return symbol === normalized;
    if (normalized === '.tw') return symbol.endsWith('.tw');
    if (normalized === '.two') return symbol.endsWith('.two');
    return (
      ticker.includes(normalized) ||
      name.includes(normalized) ||
      (normalized.includes('.') && symbol.includes(normalized))
    );
  });
  matches.sort((a, b) => {
    const aExact =
      a.ticker.toLocaleLowerCase() === normalized
        ? 0
        : a.symbol.toLocaleLowerCase() === normalized
          ? 1
          : 2;
    const bExact =
      b.ticker.toLocaleLowerCase() === normalized
        ? 0
        : b.symbol.toLocaleLowerCase() === normalized
          ? 1
          : 2;
    const exchangeOrder = (market: SymbolCatalogEntry['market']) => (market === 'TWSE' ? 0 : 1);
    return (
      aExact - bExact ||
      a.ticker.localeCompare(b.ticker) ||
      exchangeOrder(a.market) - exchangeOrder(b.market)
    );
  });
  return matches.slice(0, limit);
}

// ---------------------------------------------------------------------------------------------
// Full-market symbol directory (Taiwan listed/OTC common shares + US-listed operating companies).
// Generated at build time by scripts/build-directory.mjs into public/symbols/directory.json
// (git-ignored). When it is missing, the Taiwan catalog above is used as a fallback.
// ---------------------------------------------------------------------------------------------

export type SymbolDirectoryExchange =
  | 'TWSE'
  | 'TPEx'
  | 'NASDAQ'
  | 'NYSE'
  | 'NYSE American'
  | 'NYSE Arca'
  | 'Cboe BZX'
  | 'IEX';

export interface SymbolDirectoryEntry {
  /** Canonical Yahoo symbol: 2330.TW, 6488.TWO, NVDA, BRK-B. */
  symbol: string;
  /** Exchange code without suffix: 2330, NVDA, BRK-B. */
  ticker: string;
  /** Chinese short name (Taiwan) or company name (US). */
  name: string;
  exchange: SymbolDirectoryExchange;
  market: 'TW' | 'US';
}

export interface SymbolDirectory {
  /** `directory` = full-market file; `taiwan-catalog` = fallback to symbols/taiwan.json (Taiwan only). */
  source: 'directory' | 'taiwan-catalog';
  generatedAt?: string;
  entries: readonly SymbolDirectoryEntry[];
}

const DIRECTORY_EXCHANGES: readonly SymbolDirectoryExchange[] = [
  'TWSE',
  'TPEx',
  'NASDAQ',
  'NYSE',
  'NYSE American',
  'NYSE Arca',
  'Cboe BZX',
  'IEX',
];

export const symbolDirectoryDocumentSchema = z.object({
  version: z.literal(1),
  generatedAt: z.string(),
  items: z.array(z.tuple([z.string(), z.string(), z.string()])),
});

const TW_DIRECTORY_SYMBOL = /^([0-9]{4,6}[A-Z]?)\.(TW|TWO)$/;
const US_DIRECTORY_SYMBOL = /^[A-Z][A-Z0-9]{0,6}(?:-[A-Z0-9]{1,2})?$/;

/** Parses the compact directory document; malformed rows are skipped and duplicates keep the first. */
export function parseSymbolDirectoryDocument(raw: unknown): SymbolDirectoryEntry[] {
  const document = symbolDirectoryDocumentSchema.parse(raw);
  const seen = new Set<string>();
  const entries: SymbolDirectoryEntry[] = [];
  for (const [symbol, rawName, exchange] of document.items) {
    const name = rawName.trim();
    if (!name || seen.has(symbol) || !DIRECTORY_EXCHANGES.includes(exchange as SymbolDirectoryExchange)) continue;
    const tw = TW_DIRECTORY_SYMBOL.exec(symbol);
    if (tw) {
      if ((tw[2] === 'TW') !== (exchange === 'TWSE') || !['TWSE', 'TPEx'].includes(exchange)) continue;
      entries.push({ symbol, ticker: tw[1]!, name, exchange: exchange as SymbolDirectoryExchange, market: 'TW' });
    } else if (US_DIRECTORY_SYMBOL.test(symbol) && !['TWSE', 'TPEx'].includes(exchange)) {
      entries.push({ symbol, ticker: symbol, name, exchange: exchange as SymbolDirectoryExchange, market: 'US' });
    } else {
      continue;
    }
    seen.add(symbol);
  }
  return entries;
}

/** Converts the legacy Taiwan catalog (stocks and ETFs) into directory entries. */
export function directoryEntriesFromCatalog(entries: readonly SymbolCatalogEntry[]): SymbolDirectoryEntry[] {
  return entries.map((entry) => ({
    symbol: entry.symbol,
    ticker: entry.ticker,
    name: entry.name,
    exchange: entry.market,
    market: 'TW' as const,
  }));
}

/** Taiwan directory entries in the legacy catalog shape, so `resolveSymbolInput` keeps working. */
export function catalogEntriesFromDirectory(entries: readonly SymbolDirectoryEntry[]): SymbolCatalogEntry[] {
  return entries
    .filter((entry) => entry.market === 'TW')
    .map((entry) => ({
      symbol: entry.symbol,
      ticker: entry.ticker,
      name: entry.name,
      market: entry.exchange as 'TWSE' | 'TPEx',
      kind: 'stock' as const,
    }));
}

let directoryRequest: Promise<SymbolDirectory> | null = null;
let loadedDirectory: SymbolDirectory | null = null;

export interface LoadSymbolDirectoryOptions {
  /** Directory URL; default `${BASE_URL}symbols/directory.json`. */
  url?: string;
  fetcher?: typeof fetch;
}

/**
 * Loads `symbols/directory.json` once (shared in-flight request). If it is missing or invalid, falls back
 * to the Taiwan catalog (`symbols/taiwan.json`). Rejects only when both are unavailable.
 */
export function loadSymbolDirectory(signal?: AbortSignal, options: LoadSymbolDirectoryOptions = {}): Promise<SymbolDirectory> {
  if (!directoryRequest) {
    const fetcher = options.fetcher ?? ((input: RequestInfo | URL, init?: RequestInit) => fetch(input, init));
    const url = options.url ?? `${import.meta.env?.BASE_URL ?? '/'}symbols/directory.json`;
    directoryRequest = (async (): Promise<SymbolDirectory> => {
      try {
        const response = await fetcher(url, { cache: 'no-cache' });
        if (!response.ok) throw new Error(`Symbol directory unavailable (HTTP ${response.status})`);
        const raw: unknown = await response.json();
        const entries = parseSymbolDirectoryDocument(raw);
        if (!entries.length) throw new Error('Symbol directory is empty');
        return { source: 'directory', generatedAt: (raw as { generatedAt: string }).generatedAt, entries };
      } catch {
        const catalog = await loadSymbolCatalog();
        return { source: 'taiwan-catalog', entries: directoryEntriesFromCatalog(catalog) };
      }
    })()
      .then((directory) => {
        loadedDirectory = directory;
        return directory;
      })
      .catch((error: unknown) => {
        directoryRequest = null;
        throw error;
      });
  }
  const request = directoryRequest;
  if (!signal) return request;
  if (signal.aborted) return Promise.reject(signal.reason ?? new DOMException('Aborted', 'AbortError'));
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason ?? new DOMException('Aborted', 'AbortError'));
    signal.addEventListener('abort', abort, { once: true });
    request.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
  });
}

/** The directory loaded by `loadSymbolDirectory`, or null before it finished. */
export function getLoadedSymbolDirectory(): SymbolDirectory | null {
  return loadedDirectory;
}

/** Test helper: forget the loaded directory so the next load fetches again. */
export function clearSymbolDirectoryCache(): void {
  directoryRequest = null;
  loadedDirectory = null;
}

interface SearchRecord {
  entry: SymbolDirectoryEntry;
  ticker: string;
  symbol: string;
  name: string;
  words: string[];
}

const searchIndexes = new WeakMap<readonly SymbolDirectoryEntry[], SearchRecord[]>();
const CJK = /[㐀-鿿豈-﫿]/;

function searchIndex(entries: readonly SymbolDirectoryEntry[]): SearchRecord[] {
  let index = searchIndexes.get(entries);
  if (!index) {
    index = entries.map((entry) => {
      const name = entry.name.toLocaleLowerCase();
      return {
        entry,
        ticker: entry.ticker.toLocaleLowerCase(),
        symbol: entry.symbol.toLocaleLowerCase(),
        name,
        words: name.split(/[^a-z0-9㐀-鿿豈-﫿&']+/).filter(Boolean),
      };
    });
    searchIndexes.set(entries, index);
  }
  return index;
}

/**
 * Ranked search over directory entries: exact ticker/symbol, ticker prefix, name prefix, English word
 * prefix, then Chinese substring. Bare Taiwan digits (`2330`) match the Taiwan ticker exactly.
 */
export function searchSymbolDirectory(
  query: string,
  entries: readonly SymbolDirectoryEntry[],
  limit = 20,
): SymbolDirectoryEntry[] {
  if (!Number.isInteger(limit) || limit < 0) throw new Error('Directory search limit must be a non-negative integer');
  const raw = query.trim().toLocaleLowerCase();
  if (!raw || limit === 0) return [];
  // US share classes are typed with a dot (BRK.B) but Yahoo spells them with a dash (BRK-B).
  const q = /^[a-z]{1,6}\.[a-z]{1,2}$/.test(raw) ? raw.replace('.', '-') : raw;
  const cjk = CJK.test(q);
  const digits = /^[0-9]+[a-z]?$/.test(q);
  const scored: { record: SearchRecord; score: number }[] = [];
  for (const record of searchIndex(entries)) {
    let score: number;
    if (record.ticker === q || record.symbol === q) score = 0;
    else if (record.ticker.startsWith(q) || record.symbol.startsWith(q)) score = 1;
    else if (record.name.startsWith(q)) score = 2;
    else if (!cjk && q.length >= 2 && record.words.some((word) => word.startsWith(q))) score = 3;
    else if (cjk && record.name.includes(q)) score = 4;
    else continue;
    scored.push({ record, score });
  }
  scored.sort((a, b) =>
    a.score - b.score ||
    (digits ? (a.record.entry.market === 'TW' ? 0 : 1) - (b.record.entry.market === 'TW' ? 0 : 1) : 0) ||
    a.record.ticker.length - b.record.ticker.length ||
    a.record.ticker.localeCompare(b.record.ticker) ||
    a.record.symbol.localeCompare(b.record.symbol));
  return scored.slice(0, limit).map(({ record }) => record.entry);
}

/** Searches the directory loaded by `loadSymbolDirectory` (empty until it has loaded). */
export function searchSymbols(query: string, limit = 20): SymbolDirectoryEntry[] {
  return searchSymbolDirectory(query, loadedDirectory?.entries ?? [], limit);
}

/**
 * Resolves typed input to a canonical symbol: bare Taiwan digits through the directory (`2330` → `2330.TW`),
 * `BRK.B` → `BRK-B`, everything else through `normalizeSymbol`.
 */
export function resolveDirectoryInput(input: string, entries: readonly SymbolDirectoryEntry[]): string {
  const normalized = input.trim().toUpperCase();
  if (/^[A-Z]{1,6}\.[A-Z]{1,2}$/.test(normalized) && !/\.(?:TW|TWO)$/.test(normalized)) {
    return normalizeSymbol(normalized.replace('.', '-'));
  }
  return resolveSymbolInput(normalized, catalogEntriesFromDirectory(entries));
}
