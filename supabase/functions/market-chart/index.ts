// market-chart: a read-only Yahoo Finance chart proxy for Atlas (GitHub Pages cannot call Yahoo
// directly because of CORS). GET ?symbol=&interval=&range=&events=1 → Yahoo's raw chart JSON.
// No secrets, no database access. Atlas normalizes and validates the payload client-side.
import {
  buildYahooChartUrl,
  cacheControlFor,
  classifyUpstreamStatus,
  corsHeaders,
  isAllowedOrigin,
  isChartPayload,
  parseAllowedOrigins,
  validateChartRequest,
  YAHOO_HOSTS,
} from "./validate.ts";

const allowedOrigins = parseAllowedOrigins(Deno.env.get("ALLOWED_ORIGINS"));
const UPSTREAM_TIMEOUT_MS = 8000;
const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36";

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
      "Cache-Control": "no-store",
    },
  });
}

Deno.serve(async (req: Request): Promise<Response> => {
  const origin = req.headers.get("Origin");
  // Browsers on other sites get a plain 403; server-to-server callers (no Origin) are allowed.
  if (origin && !isAllowedOrigin(origin, allowedOrigins)) {
    return errorResponse(null, 403, "origin_not_allowed", "Origin not allowed");
  }
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders(origin, allowedOrigins) });
  }
  if (req.method !== "GET") {
    return errorResponse(origin, 405, "method_not_allowed", "Use GET");
  }

  const validation = validateChartRequest(new URL(req.url).searchParams);
  if (!validation.ok) {
    return errorResponse(origin, validation.status, validation.code, validation.message);
  }
  const request = validation.value;

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
      return new Response(text, {
        status: 200,
        headers: {
          ...corsHeaders(origin, allowedOrigins),
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control": cacheControlFor(request.interval),
        },
      });
    } catch {
      // Timeout or network failure: try the next Yahoo host. Details are never echoed to clients.
      failure = classifyUpstreamStatus(502);
    }
  }
  console.warn(
    `market-chart ${request.symbol} ${request.interval}/${request.range}: ${failure.code}`,
  );
  return errorResponse(origin, failure.status, failure.code, failure.message);
});
