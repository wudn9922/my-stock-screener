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
