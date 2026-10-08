export const ATLAS_SYMBOL_REGEX: RegExp;
export const DEFAULT_INDEX_TICKERS: string[];
export function normalizeScreenerTicker(raw: unknown): string | null;
export function collectSymbols(rawTickers: Iterable<unknown>, log?: (message: string) => void): string[];
export function supabaseRestUrl(baseUrl: string | undefined): string;
export function supabaseHeaders(key: string): Record<string, string>;
export function fetchScreenerSymbols(
  env?: Record<string, string | undefined>,
  fetchImpl?: typeof fetch,
  log?: (message: string) => void,
): Promise<string[]>;
