import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { EnvHttpProxyAgent, fetch as proxyFetch } from 'undici';
import { parseReport } from '../src/report/loadReport';
import { parseUniverse, universeMarketOf } from '../src/report/universe';
import { mergeGrowth, parseGrowthFile, parseReportGrowth } from '../src/fundamentals/GrowthProvider';
import { evaluateWatchlist, recordWatchlistData, watchlistSchema } from '../src/watchlists/model';

export function previousSourceSession(csv: string, asOf: string): string | null {
  const dates = [
    ...new Set(
      csv
        .split(/\r?\n/)
        .map((line) => line.split(',')[0]?.replace(/^"|"$/g, '').slice(0, 10))
        .filter((day): day is string => !!day && /^\d{4}-\d{2}-\d{2}$/.test(day) && day <= asOf),
    ),
  ].sort();
  return dates.at(-1) === asOf ? (dates.at(-2) ?? null) : null;
}

/** The same evaluator as the browser, without moving lastViewed/lastOpenedAt. */
export async function refreshSavedWatchlists() {
  if (process.env.ATLAS_WATCHLIST_SYNC_ENABLED !== '1') {
    console.log('Atlas cloud watchlist collection disabled; enable only after the SQL migration and service deployment.');
    return;
  }
  const base = process.env.SUPABASE_URL?.replace(/\/$/, '');
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  const user = process.env.SUPABASE_USER_ID;
  if (!base || new URL(base).protocol !== 'https:' || !key || !user)
    throw new Error('Cloud watchlist collection requires SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and SUPABASE_USER_ID.');
  const root = resolve(import.meta.dirname, '../..');
  const json = async (file: string) => JSON.parse(await readFile(resolve(root, file), 'utf8')) as unknown;
  const [reportResult, universeResult, rawGrowth] = await Promise.all([
    json('docs/report/latest.json').then((raw) => parseReport(raw)),
    json('docs/report/universe.json').then((raw) => parseUniverse(raw)),
    json('docs/report/growth.json'),
  ]);
  if (reportResult.status !== 'ok' || universeResult.status !== 'ok')
    throw new Error('Report/universe unavailable; existing cloud snapshots were preserved.');
  const growth = parseReportGrowth(rawGrowth);
  let yahooGrowth = null;
  try {
    yahooGrowth = parseGrowthFile(await json('atlas/public/valuation/us-growth.json'));
  } catch {
    try {
      yahooGrowth = parseGrowthFile(await json('docs/atlas/valuation/us-growth.json'));
    } catch {
      /* Optional US supplement. */
    }
  }
  const dispatcher = process.env.HTTPS_PROXY || process.env.HTTP_PROXY ? new EnvHttpProxyAgent() : undefined;
  const request = async (url: URL, method = 'GET', body?: unknown) => {
    const response = await proxyFetch(url, {
      method,
      dispatcher,
      headers: { apikey: key, Authorization: `Bearer ${key}`, 'Content-Type': 'application/json', Prefer: 'return=representation' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(20000),
    });
    if (!response.ok) throw new Error(`Cloud watchlist ${method} failed (HTTP ${response.status}); existing snapshots preserved.`);
    return response.json();
  };
  let updated = 0,
    skipped = 0;
  try {
    const url = new URL(`${base}/rest/v1/atlas_watchlists`);
    url.search = new URLSearchParams({ select: 'id,definition,updated_at', line_user_id: `eq.${user}`, limit: '30' }).toString();
    const rows = (await request(url)) as { id: string; definition: unknown; updated_at: string }[];
    if (!Array.isArray(rows)) throw new Error('Cloud watchlist response is not a list.');
    for (const row of rows) {
      try {
        const list = watchlistSchema.parse(row.definition);
        if (list.id !== row.id) throw new Error('List ID mismatch');
        const universe = universeResult.universe.markets[list.market];
        const group = reportResult.report.groups.find((entry) => entry.key === list.group && entry.market === list.market);
        const useUniverse = list.kind === 'manual' || universeMarketOf(list.group) !== null;
        const items = useUniverse ? universe?.items : group?.items;
        const asOf = (useUniverse ? universe?.asOf : group?.items.find((entry) => entry.asOf)?.asOf)?.slice(0, 10);
        if (!items || !asOf) throw new Error('Source/date unavailable');
        const members = evaluateWatchlist(list, items, list.market === 'US' ? mergeGrowth(yahooGrowth, growth) : growth);
        let previousSession: string | null = null;
        try {
          const csv = await readFile(resolve(root, `data/${list.market === 'TW' ? '^TWII' : 'SPY'}.csv`), 'utf8');
          previousSession = previousSourceSession(csv, asOf);
        } catch {
          /* Without a source calendar the UI conservatively counts observed weekdays only. */
        }
        const definition = recordWatchlistData(list, members, asOf, previousSession);
        const patchUrl = new URL(`${base}/rest/v1/atlas_watchlists`);
        patchUrl.search = new URLSearchParams({
          id: `eq.${list.id}`,
          line_user_id: `eq.${user}`,
          updated_at: `eq.${row.updated_at}`,
        }).toString();
        const written = await request(patchUrl, 'PATCH', { definition });
        if (Array.isArray(written) && written.length === 1) updated++;
        else skipped++;
      } catch {
        skipped++;
      }
    }
    console.log(`Atlas cloud watchlists: ${updated} daily observations saved; ${skipped} unavailable/conflicting definitions preserved.`);
  } finally {
    await dispatcher?.close();
  }
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  refreshSavedWatchlists().catch((error) => {
    console.error(error instanceof Error ? error.message : 'Cloud watchlist refresh failed');
    process.exitCode = 1;
  });
}
