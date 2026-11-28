// market-chart: read-only Yahoo history with a Taiwan MIS overlay (GitHub Pages cannot call Yahoo
// directly because of CORS). GET ?symbol=&interval=&range=&events=1 → Yahoo's raw chart JSON.
// GET ?quotes=AAPL,2330.TW → { results: { SYMBOL: slim chart JSON }, errors: { SYMBOL: code } }
// with the last month of daily rows per symbol (the site's once-a-minute live refresh).
// GET ?warrantQuotes=066186.TW,714060.TWO → Taiwan MIS best bids and asks.
// Every request needs a signed-in Atlas user (X-Atlas-Token: LINE ID token or atlasdev. device token)
// and passes the per-user / site-wide usage quota in access.ts. Atlas normalizes and validates the
// payload client-side.
import { createClient } from "@supabase/supabase-js";
import { resolveLineUser } from "../_shared/deviceSession.ts";
import { verifyLineIdTokenWith } from "../_shared/lineIdToken.ts";
import { adminLineId, getStorageAccess } from "../_shared/storageAccess.ts";
import {
  ATLAS_TOKEN_HEADER,
  consumeQuotaRpc,
  createMarketChartGate,
  type GateFailure,
} from "./access.ts";
import {
  buildYahooChartUrl,
  cacheControlFor,
  type ChartRequest,
  classifyUpstreamStatus,
  corsHeaders,
  fillDailyFromIntraday,
  INTRADAY_FILL_REQUEST,
  isAllowedOrigin,
  isChartPayload,
  needsIntradayFill,
  parseAllowedOrigins,
  QUOTE_CACHE_SECONDS,
  quoteChartRequest,
  slimChartPayload,
  validateChartRequest,
  validateQuotesRequest,
  validateWarrantQuotesRequest,
  YAHOO_HOSTS,
} from "./validate.ts";
import {
  inMisSession,
  misChannel,
  misOnlyChart,
  overlayMis,
  TwseMisClient,
} from "./twse-mis.ts";

const allowedOrigins = parseAllowedOrigins(Deno.env.get("ALLOWED_ORIGINS"));
const UPSTREAM_TIMEOUT_MS = 8000;
const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36";

const DATABASE_TIMEOUT_MS = 5000;

/** Sign-in + quota gate, or null when the server configuration is incomplete (every request → 503). */
function createGate() {
  const url = Deno.env.get("SUPABASE_URL");
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  let adminId: string;
  try {
    adminId = adminLineId(Deno.env.get("ADMIN_LINE_USER_ID"));
  } catch {
    console.error("market-chart: ADMIN_LINE_USER_ID is invalid");
    return null;
  }
  if (!url || !key) {
    console.error("market-chart: Supabase server configuration is missing");
    return null;
  }
  const database = createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
    global: {
      fetch: (input, init) =>
        fetch(input, {
          ...init,
          signal: init?.signal ?? AbortSignal.timeout(DATABASE_TIMEOUT_MS),
        }),
    },
  });
  const channelId = Deno.env.get("LINE_LOGIN_CHANNEL_ID");
  return createMarketChartGate({
    resolveUser: (token) =>
      resolveLineUser(
        database,
        token,
        (idToken) => verifyLineIdTokenWith(idToken, { channelId }),
      ),
    getStorageMode: async (userId) =>
      (await getStorageAccess(database, userId, adminId)).storageMode,
    consumeQuota: (request) => consumeQuotaRpc(database, request),
    adminId,
  });
}
const gate = createGate();
const GATE_UNAVAILABLE: GateFailure = {
  ok: false,
  status: 503,
  body: {
    ok: false,
    code: "AUTH_UNAVAILABLE",
    error: "登入驗證服務暫時無法使用，請稍後再試。",
    retryAfterSeconds: 60,
  },
  retryAfterSeconds: 60,
};

function gateResponse(origin: string | null, failure: GateFailure): Response {
  const headers: Record<string, string> = {
    ...corsHeaders(origin, allowedOrigins),
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "private, no-store",
  };
  if (failure.retryAfterSeconds !== undefined) {
    headers["Retry-After"] = String(failure.retryAfterSeconds);
  }
  return new Response(JSON.stringify(failure.body), {
    status: failure.status,
    headers,
  });
}

function errorResponse(
  origin: string | null,
  status: number,
  code: string,
  message: string,
): Response {
  return new Response(JSON.stringify({ error: { code, message } }), {
    status,
    headers: {
      ...corsHeaders(origin, allowedOrigins),
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "private, no-store",
    },
  });
}

type Failure = ReturnType<typeof classifyUpstreamStatus>;
type ChartOutcome = { ok: true; text: string; payload: unknown } | {
  ok: false;
  failure: Failure;
};

/** One Yahoo chart request, query2 first; timeouts, 5xx and 429 retry query1. */
async function fetchChart(request: ChartRequest): Promise<ChartOutcome> {
  let failure = classifyUpstreamStatus(502);
  for (const host of YAHOO_HOSTS) {
    try {
      const upstream = await fetch(buildYahooChartUrl(host, request), {
        headers: {
          "User-Agent": USER_AGENT,
          Accept: "application/json,text/plain,*/*",
          "Accept-Language": "en-US,en;q=0.9",
        },
        signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
      });
      if (!upstream.ok) {
        failure = classifyUpstreamStatus(upstream.status);
        await upstream.body?.cancel();
        if (!failure.retryOtherHost) break;
        continue;
      }
      const text = await upstream.text();
      let payload: unknown;
      try {
        payload = JSON.parse(text);
      } catch {
        failure = classifyUpstreamStatus(502);
        continue;
      }
      if (!isChartPayload(payload)) {
        failure = classifyUpstreamStatus(404);
        break;
      }
      return { ok: true, text, payload };
    } catch {
      // Timeout or network failure: try the next Yahoo host. Details are never echoed to clients.
      failure = classifyUpstreamStatus(502);
    }
  }
  return { ok: false, failure };
}

/** Daily request whose empty recent sessions are rebuilt from 5-minute bars (see validate.ts). */
async function fetchDaily(request: ChartRequest): Promise<ChartOutcome> {
  const outcome = await fetchChart(request);
  if (
    !outcome.ok || request.interval !== "1d" ||
    !needsIntradayFill(outcome.payload)
  ) return outcome;
  const minute = await fetchChart({
    symbol: request.symbol,
    ...INTRADAY_FILL_REQUEST,
    events: false,
  });
  if (!minute.ok) return outcome;
  const { payload, filled } = fillDailyFromIntraday(
    outcome.payload,
    minute.payload,
  );
  if (!filled.length) return outcome;
  console.log(
    `market-chart ${request.symbol} ${request.range}: filled ${
      filled.join(",")
    } from 5m bars`,
  );
  return { ok: true, text: JSON.stringify(payload), payload };
}

// Recent quote rows per symbol, shared by every viewer reaching this instance.
const quoteCache = new Map<string, { at: number; payload: unknown }>();
const QUOTE_CONCURRENCY = 6;
const mis = new TwseMisClient();

async function quoteBatch(
  origin: string | null,
  value: string,
): Promise<Response> {
  const validation = validateQuotesRequest(value);
  if (!validation.ok) {
    return errorResponse(
      origin,
      validation.status,
      validation.code,
      validation.message,
    );
  }
  const results: Record<string, unknown> = {};
  const errors: Record<string, string> = {};
  const now = Date.now();
  // One MIS batch for the Taiwan members; Yahoo history and #224 fills run unchanged in parallel.
  const taiwan = mis.get(validation.symbols);
  const pending = validation.symbols.filter((symbol) => {
    const cached = quoteCache.get(symbol);
    if (cached && now - cached.at < QUOTE_CACHE_SECONDS * 1000) {
      results[symbol] = cached.payload;
    }
    return !(symbol in results);
  });
  let rateLimited = false;
  const worker = async () => {
    for (let symbol = pending.shift(); symbol; symbol = pending.shift()) {
      // After Yahoo rate-limits once, stop asking it for the rest of this batch.
      if (rateLimited) {
        errors[symbol] = "rate_limited";
        continue;
      }
      const outcome = await fetchDaily(quoteChartRequest(symbol));
      if (!outcome.ok) {
        errors[symbol] = outcome.failure.code;
        if (outcome.failure.code === "rate_limited") rateLimited = true;
        continue;
      }
      try {
        const payload = slimChartPayload(outcome.payload);
        quoteCache.set(symbol, { at: Date.now(), payload });
        results[symbol] = payload;
      } catch {
        errors[symbol] = "upstream_error";
      }
    }
  };
  await Promise.all(Array.from({ length: QUOTE_CONCURRENCY }, worker));
  for (const [symbol, snapshot] of await taiwan) {
    const payload = symbol in results
      ? overlayMis(results[symbol], snapshot)
      : misOnlyChart(snapshot);
    if (payload) {
      results[symbol] = payload;
      delete errors[symbol];
    }
  }
  if (quoteCache.size > 2000) quoteCache.clear();
  if (Object.keys(errors).length) {
    console.warn(
      `market-chart quotes: ${
        Object.entries(errors).map(([s, c]) => `${s}=${c}`).join(" ")
      }`,
    );
  }
  return new Response(
    JSON.stringify({ asOf: Math.floor(Date.now() / 1000), results, errors }),
    {
      status: 200,
      headers: {
        ...corsHeaders(origin, allowedOrigins),
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": `private, max-age=${QUOTE_CACHE_SECONDS}`,
      },
    },
  );
}

Deno.serve(async (req: Request): Promise<Response> => {
  const origin = req.headers.get("Origin");
  // Browsers on other sites get a plain 403. Callers without an Origin still need a valid token below.
  if (origin && !isAllowedOrigin(origin, allowedOrigins)) {
    return errorResponse(null, 403, "origin_not_allowed", "Origin not allowed");
  }
  if (req.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: corsHeaders(origin, allowedOrigins),
    });
  }
  if (req.method !== "GET") {
    return errorResponse(origin, 405, "method_not_allowed", "Use GET");
  }
  const access = gate
    ? await gate.check(req.headers.get(ATLAS_TOKEN_HEADER))
    : GATE_UNAVAILABLE;
  if (!access.ok) return gateResponse(origin, access);

  const params = new URL(req.url).searchParams;
  if (params.has("warrantQuotes")) {
    const validation = validateWarrantQuotesRequest(params);
    if (!validation.ok) {
      return errorResponse(
        origin,
        validation.status,
        validation.code,
        validation.message,
      );
    }
    return new Response(
      JSON.stringify(await mis.getWarrantQuotes(validation.symbols)),
      {
        status: 200,
        headers: {
          ...corsHeaders(origin, allowedOrigins),
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control": "private, no-store",
        },
      },
    );
  }
  const quotes = params.get("quotes");
  if (quotes !== null) return quoteBatch(origin, quotes);

  const validation = validateChartRequest(params);
  if (!validation.ok) {
    return errorResponse(
      origin,
      validation.status,
      validation.code,
      validation.message,
    );
  }
  const request = validation.value;
  const taiwan = request.interval === "1d"
    ? mis.get([request.symbol])
    : Promise.resolve(new Map());
  const outcome = await fetchDaily(request);
  const snapshot = (await taiwan).get(request.symbol);
  // A full-history chart request never answers with a lone MIS bar: a failed Yahoo history stays an
  // error so Atlas keeps its cached/archived history. Only the ?quotes= feed (merged) uses misOnlyChart.
  if (outcome.ok) {
    const payload = snapshot ? overlayMis(outcome.payload, snapshot) : null;
    return new Response(
      payload ? JSON.stringify(payload) : outcome.text,
      {
        status: 200,
        headers: {
          ...corsHeaders(origin, allowedOrigins),
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control":
            request.interval === "1d" && misChannel(request.symbol) &&
              inMisSession(Date.now())
              ? "private, max-age=15"
              : cacheControlFor(request.interval),
        },
      },
    );
  }
  const failure = outcome.failure;
  console.warn(
    `market-chart ${request.symbol} ${request.interval}/${request.range}: ${failure.code}`,
  );
  return errorResponse(origin, failure.status, failure.code, failure.message);
});
