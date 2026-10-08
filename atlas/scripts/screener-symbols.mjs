// Builds scripts/market-symbols.json (the refresh-market allowlist) from the my-stock-screener
// Supabase tables `stocks` and `index_configs`. Node built-ins only (global fetch).
//
// Env: SUPABASE_URL (with or without /rest/v1), SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY,
//      optional SUPABASE_USER_ID (filters stocks.line_user_id).
// On any failure, or when Supabase returns no usable symbol, exits non-zero and leaves the
// existing market-symbols.json untouched.
import { readFile, rename, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

/** Mirrors CANONICAL_SYMBOL_REGEX in src/market-data/MarketProfile.ts (US | Taiwan | ^index). */
export const ATLAS_SYMBOL_REGEX =
  /^(?:[A-Z][A-Z0-9.^-]{0,14}|[0-9]{4,6}[A-Z]?\.(?:TW|TWO)|\^[A-Z0-9][A-Z0-9.-]{0,14})$/;

/** Screener defaults (main.py get_default_index_configs), used only if index_configs is unavailable. */
export const DEFAULT_INDEX_TICKERS = ['^TWII', '^TWOII', '^GSPC', '^DJI', '^IXIC', '^RUT', '^SOX'];

const PAGE_SIZE = 1000;
const MAX_PAGES = 50;

/**
 * Same rules as the screener's normalize_custom_ticker, plus `.TW` for bare digit codes:
 * upper-case; keep `.TW` / `.TWO`; pure digits → `<code>.TW`; keep `^` indices;
 * other (US) tickers use `-` for share classes (BRK.B → BRK-B). Returns null when invalid.
 */
export function normalizeScreenerTicker(raw) {
  if (raw === null || raw === undefined) return null;
  const ticker = String(raw).trim().toUpperCase();
  if (!ticker) return null;
  let symbol;
  if (ticker.endsWith('.TW') || ticker.endsWith('.TWO')) symbol = ticker;
  else if (/^[0-9]+$/.test(ticker)) symbol = `${ticker}.TW`;
  else if (ticker.startsWith('^')) symbol = ticker;
  else symbol = ticker.replaceAll('.', '-');
  return ATLAS_SYMBOL_REGEX.test(symbol) ? symbol : null;
}

/** Normalizes, logs and skips invalid tickers, de-duplicates and sorts. */
export function collectSymbols(rawTickers, log = console.log) {
  const symbols = new Set();
  for (const raw of rawTickers) {
    const symbol = normalizeScreenerTicker(raw);
    if (symbol) symbols.add(symbol);
    else if (raw !== null && raw !== undefined && String(raw).trim())
      log(`skip invalid ticker: ${JSON.stringify(String(raw).slice(0, 40))}`);
  }
  return [...symbols].sort();
}

export function supabaseRestUrl(baseUrl) {
  const base = String(baseUrl ?? '').trim().replace(/\/+$/, '');
  if (!base) throw new Error('SUPABASE_URL is not set');
  return base.endsWith('/rest/v1') ? base : `${base}/rest/v1`;
}

/** New sb_secret_/sb_publishable_ keys go only in `apikey`; legacy JWT keys also as Bearer. */
export function supabaseHeaders(key) {
  const headers = { apikey: key, Accept: 'application/json' };
  if (key.startsWith('eyJ')) headers.Authorization = `Bearer ${key}`;
  return headers;
}

async function getRows(rest, table, query, headers, fetchImpl) {
  const rows = [];
  for (let page = 0; page < MAX_PAGES; page++) {
    const params = new URLSearchParams(query);
    params.set('limit', String(PAGE_SIZE));
    params.set('offset', String(page * PAGE_SIZE));
    const response = await fetchImpl(`${rest}/${table}?${params}`, {
      headers,
      signal: AbortSignal.timeout(20000),
    });
    if (!response.ok) {
      const body = (await response.text().catch(() => '')).slice(0, 300);
      throw new Error(`Supabase ${table} HTTP ${response.status} ${body}`);
    }
    const data = await response.json();
    if (!Array.isArray(data)) throw new Error(`Supabase ${table} returned a non-array response`);
    rows.push(...data);
    if (data.length < PAGE_SIZE) return rows;
  }
  throw new Error(`Supabase ${table} has more than ${PAGE_SIZE * MAX_PAGES} rows`);
}

/** Fetches stock and index tickers from Supabase and returns the normalized allowlist. */
export async function fetchScreenerSymbols(env = process.env, fetchImpl = globalThis.fetch, log = console.log) {
  const rest = supabaseRestUrl(env.SUPABASE_URL);
  const key = String(env.SUPABASE_SERVICE_ROLE_KEY || env.SUPABASE_ANON_KEY || '').trim();
  if (!key) throw new Error('SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY is not set');
  const headers = supabaseHeaders(key);
  const userId = String(env.SUPABASE_USER_ID ?? '').trim();

  const stockQuery = { select: 'ticker,line_user_id' };
  if (userId) stockQuery.line_user_id = `eq.${userId}`;
  const stocks = (await getRows(rest, 'stocks', stockQuery, headers, fetchImpl)).filter(
    (row) => !userId || String(row?.line_user_id ?? '').trim() === userId,
  );
  log(`stocks: ${stocks.length} rows${userId ? ' for SUPABASE_USER_ID' : ''}`);

  let indexTickers;
  try {
    const indices = await getRows(rest, 'index_configs', { select: '*' }, headers, fetchImpl);
    indexTickers = indices.filter((row) => row?.enabled !== false).map((row) => row?.ticker);
    log(`index_configs: ${indices.length} rows`);
  } catch (error) {
    log(`index_configs unavailable (${error instanceof Error ? error.message : error}); using screener defaults`);
    indexTickers = [];
  }
  if (!indexTickers.length) indexTickers = DEFAULT_INDEX_TICKERS;
  const stockSymbols = collectSymbols(stocks.map((row) => row?.ticker), log);
  if (!stockSymbols.length) throw new Error('Supabase returned no valid stock tickers');
  return collectSymbols([...stockSymbols, ...indexTickers], log);
}

async function writeAtomic(path, value) {
  const temporary = `${path}.tmp`;
  await writeFile(temporary, value);
  await rename(temporary, path);
}

async function main() {
  const output = resolve(dirname(fileURLToPath(import.meta.url)), 'market-symbols.json');
  try {
    const symbols = await fetchScreenerSymbols();
    const prior = await readFile(output, 'utf8').then(JSON.parse).catch(() => []);
    const previous = Array.isArray(prior) ? prior : [];
    await writeAtomic(output, JSON.stringify(symbols));
    const added = symbols.filter((symbol) => !previous.includes(symbol));
    const removed = previous.filter((symbol) => !symbols.includes(symbol));
    console.log(`market-symbols.json: ${symbols.length} symbols (+${added.length} / -${removed.length})`);
  } catch (error) {
    console.error(`screener-symbols failed; keeping existing market-symbols.json: ${error instanceof Error ? error.message : error}`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main();
