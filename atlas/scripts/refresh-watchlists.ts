import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { EnvHttpProxyAgent, fetch as proxyFetch } from "undici";
import { parseReport } from "../src/report/loadReport";
import { parseUniverse, universeMarketOf } from "../src/report/universe";
import {
  mergeGrowth,
  parseGrowthFile,
  parseReportGrowth,
} from "../src/fundamentals/GrowthProvider";
import {
  evaluateWatchlist,
  recordWatchlistData,
  watchlistSchema,
} from "../src/watchlists/model";
import { parseFilters } from "../src/pages/screenerFilters";
import { loadScreenerFixedMAs } from "../src/pages/screenerFixedMAs";
import type { ParameterStock } from "../src/cloud/settingsApi";

const DEFAULT_ADMIN_LINE_ID = "Ua0037dbd5037b64fc76804c60520ad76";
const LINE_ID = /^U[0-9a-f]{32}$/;

/** A scheduled collector has the same storage consent as the interactive API. */
export function collectorStorageMode(
  user: string,
  owner: string,
  status: number,
  body: unknown,
): "none" | "device" | "cloud" {
  if (!LINE_ID.test(user)) return "none";
  const fallback = user === owner ? "cloud" : "none";
  if (status >= 400) {
    const code =
      body && typeof body === "object"
        ? (body as { code?: unknown }).code
        : undefined;
    return code === "42P01" || code === "PGRST205" ? fallback : "none";
  }
  if (!Array.isArray(body) || body.length > 1) return "none";
  if (!body.length) return fallback;
  const row = body[0] as {
    line_user_id?: unknown;
    storage_mode?: unknown;
  } | null;
  if (!row || row.line_user_id !== user) return "none";
  return row.storage_mode === "cloud" || row.storage_mode === "device"
    ? row.storage_mode
    : "none";
}

class CollectorStorageDenied extends Error {}

export function previousSourceSession(
  csv: string,
  asOf: string,
): string | null {
  const dates = [
    ...new Set(
      csv
        .split(/\r?\n/)
        .map((line) => line.split(",")[0]?.replace(/^"|"$/g, "").slice(0, 10))
        .filter(
          (day): day is string =>
            !!day && /^\d{4}-\d{2}-\d{2}$/.test(day) && day <= asOf,
        ),
    ),
  ].sort();
  return dates.at(-1) === asOf ? (dates.at(-2) ?? null) : null;
}

/** Collector CSV dates are session dates; noon UTC preserves that date in both markets. */
export function fixedMaBarsFromCsv(csv: string) {
  const rows = csv.trim().split(/\r?\n/).map(line => line.split(",").map(value => value.replace(/^"|"$/g, "")));
  const columns = rows.shift() ?? [];
  const date = columns.indexOf("Date"), close = columns.indexOf("Close");
  if (date < 0 || close < 0) return [];
  return rows.map(row => ({ time: Date.parse(`${row[date]?.slice(0, 10)}T12:00:00Z`) / 1000, close: Number(row[close]) }))
    .filter(bar => Number.isFinite(bar.time) && Number.isFinite(bar.close) && bar.close > 0)
    .sort((a, b) => a.time - b.time);
}

/** Both sources use universe_cache.py's Date,Open,High,Low,Close,Volume CSV format. */
export async function readCollectorFixedMaBars(symbol: string, root: string) {
  if (!/^[A-Z0-9^][A-Z0-9.^=-]{0,31}$/.test(symbol))
    throw new Error(`Invalid symbol: ${symbol}`);
  let csv: string;
  try {
    csv = await readFile(resolve(root, "data", `${symbol}.csv`), "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    const cacheDir = resolve(root, process.env.UNIVERSE_CACHE_DIR || ".cache/universe-full");
    csv = await readFile(resolve(cacheDir, "bars", `${symbol}.csv`), "utf8");
  }
  return fixedMaBarsFromCsv(csv);
}

/** The same evaluator as the browser, without moving lastViewed/lastOpenedAt. */
export async function refreshSavedWatchlists() {
  if (process.env.ATLAS_WATCHLIST_SYNC_ENABLED !== "1") {
    console.log(
      "Atlas cloud watchlist collection disabled; enable only after the SQL migration and service deployment.",
    );
    return;
  }
  const base = process.env.SUPABASE_URL?.replace(/\/$/, "");
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  const user = process.env.SUPABASE_USER_ID;
  if (
    !base ||
    new URL(base).protocol !== "https:" ||
    !key ||
    !user ||
    !LINE_ID.test(user)
  )
    throw new Error(
      "Cloud watchlist collection requires SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and SUPABASE_USER_ID.",
    );
  const root = resolve(import.meta.dirname, "../..");
  const json = async (file: string) =>
    JSON.parse(await readFile(resolve(root, file), "utf8")) as unknown;
  const [reportResult, universeResult, rawGrowth] = await Promise.all([
    json("docs/report/latest.json").then((raw) => parseReport(raw)),
    json("docs/report/universe.json").then((raw) => parseUniverse(raw)),
    json("docs/report/growth.json"),
  ]);
  if (reportResult.status !== "ok" || universeResult.status !== "ok")
    throw new Error(
      "Report/universe unavailable; existing cloud snapshots were preserved.",
    );
  const growth = parseReportGrowth(rawGrowth);
  let yahooGrowth = null;
  try {
    yahooGrowth = parseGrowthFile(
      await json("atlas/public/valuation/us-growth.json"),
    );
  } catch {
    try {
      yahooGrowth = parseGrowthFile(
        await json("docs/atlas/valuation/us-growth.json"),
      );
    } catch {
      /* Optional US supplement. */
    }
  }
  const dispatcher =
    process.env.HTTPS_PROXY || process.env.HTTP_PROXY
      ? new EnvHttpProxyAgent()
      : undefined;
  const authHeaders = {
    apikey: key,
    ...(key.startsWith("eyJ") ? { Authorization: `Bearer ${key}` } : {}),
    "Content-Type": "application/json",
  };
  const requireCloud = async () => {
    const policyUrl = new URL(`${base}/rest/v1/atlas_storage_access`);
    policyUrl.search = new URLSearchParams({
      select: "line_user_id,storage_mode",
      line_user_id: `eq.${user}`,
      limit: "2",
    }).toString();
    const response = await proxyFetch(policyUrl, {
      dispatcher,
      headers: authHeaders,
      signal: AbortSignal.timeout(15000),
    });
    const mode = collectorStorageMode(
      user,
      process.env.ADMIN_LINE_USER_ID?.trim() || DEFAULT_ADMIN_LINE_ID,
      response.status,
      await response.json(),
    );
    if (mode !== "cloud")
      throw new CollectorStorageDenied(
        "Cloud storage is disabled or permission is unavailable.",
      );
  };
  const request = async (url: URL, method = "GET", body?: unknown) => {
    // Fresh check also covers revocation while a long-running collection is in progress.
    await requireCloud();
    const response = await proxyFetch(url, {
      method,
      dispatcher,
      headers: { ...authHeaders, Prefer: "return=representation" },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(20000),
    });
    if (!response.ok)
      throw new Error(
        `Cloud watchlist ${method} failed (HTTP ${response.status}); existing snapshots preserved.`,
      );
    return response.json();
  };
  let updated = 0,
    skipped = 0;
  try {
    const url = new URL(`${base}/rest/v1/atlas_watchlists`);
    url.search = new URLSearchParams({
      select: "id,line_user_id,definition,updated_at",
      line_user_id: `eq.${user}`,
      limit: "30",
    }).toString();
    const rows = (await request(url)) as {
      id: string;
      line_user_id: string;
      definition: unknown;
      updated_at: string;
    }[];
    if (!Array.isArray(rows))
      throw new Error("Cloud watchlist response is not a list.");
    let fixedStocks: Promise<ParameterStock[]> | undefined;
    for (const row of rows) {
      try {
        if (row.line_user_id !== user) throw new Error("List owner mismatch");
        const list = watchlistSchema.parse(row.definition);
        if (list.id !== row.id) throw new Error("List ID mismatch");
        if (list.cloud && list.cloud.userId !== user)
          throw new Error("Linked account mismatch");
        const universe = universeResult.universe.markets[list.market];
        const group = reportResult.report.groups.find(
          (entry) => entry.key === list.group && entry.market === list.market,
        );
        const useUniverse =
          list.kind === "manual" || universeMarketOf(list.group) !== null;
        const items = useUniverse ? universe?.items : group?.items;
        const asOf = (
          useUniverse
            ? universe?.asOf
            : group?.items.find((entry) => entry.asOf)?.asOf
        )?.slice(0, 10);
        if (!items || !asOf) throw new Error("Source/date unavailable");
        let fixedMAs;
        if (parseFilters(list.filters).ema20) {
          if (!fixedStocks) {
            const stockUrl = new URL(`${base}/rest/v1/stocks`);
            stockUrl.search = new URLSearchParams({ select: "id,ticker,group_id,ma1,ma2,ma3,ma4", line_user_id: `eq.${user}`, group_id: "eq.ungrouped" }).toString();
            fixedStocks = request(stockUrl).then(body => {
              if (!Array.isArray(body)) throw new Error("Fixed parameters unavailable");
              return body as ParameterStock[];
            });
          }
          fixedMAs = await loadScreenerFixedMAs(items, await fixedStocks, symbol => readCollectorFixedMaBars(symbol, root));
        }
        const members = evaluateWatchlist(
          list,
          items,
          list.market === "US" ? mergeGrowth(yahooGrowth, growth) : growth,
          fixedMAs,
        );
        let previousSession: string | null = null;
        try {
          const csv = await readFile(
            resolve(root, `data/${list.market === "TW" ? "^TWII" : "SPY"}.csv`),
            "utf8",
          );
          previousSession = previousSourceSession(csv, asOf);
        } catch {
          /* Without a source calendar the UI conservatively counts observed weekdays only. */
        }
        const definition = recordWatchlistData(
          list,
          members,
          asOf,
          previousSession,
        );
        const patchUrl = new URL(`${base}/rest/v1/atlas_watchlists`);
        patchUrl.search = new URLSearchParams({
          id: `eq.${list.id}`,
          line_user_id: `eq.${user}`,
          updated_at: `eq.${row.updated_at}`,
        }).toString();
        const written = await request(patchUrl, "PATCH", { definition });
        if (Array.isArray(written) && written.length === 1) updated++;
        else skipped++;
      } catch (error) {
        if (error instanceof CollectorStorageDenied) {
          console.log(
            "Atlas cloud collection stopped after storage permission changed; no further personal writes.",
          );
          break;
        }
        skipped++;
      }
    }
    console.log(
      `Atlas cloud watchlists: ${updated} daily observations saved; ${skipped} unavailable/conflicting definitions preserved.`,
    );
  } catch (error) {
    if (!(error instanceof CollectorStorageDenied)) throw error;
    console.log(
      "Atlas cloud watchlist collection skipped: this account has no cloud storage grant.",
    );
  } finally {
    await dispatcher?.close();
  }
}

if (
  process.argv[1] &&
  pathToFileURL(resolve(process.argv[1])).href === import.meta.url
) {
  refreshSavedWatchlists().catch((error) => {
    console.error(
      error instanceof Error ? error.message : "Cloud watchlist refresh failed",
    );
    process.exitCode = 1;
  });
}
