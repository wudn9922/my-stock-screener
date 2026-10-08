# market-chart (Supabase Edge Function)

Read-only Yahoo Finance chart proxy for Atlas on GitHub Pages. The browser cannot call Yahoo directly
(no CORS), so Atlas (`atlas/src/market-data/EdgeYahooProvider.ts`) calls this function, which returns
Yahoo's **raw** chart JSON. Atlas normalizes and validates it client-side. No secrets, no database.

```
GET /functions/v1/market-chart?symbol=2330.TW&interval=1d&range=5y&events=1
```

| Parameter | Rule |
| --- | --- |
| `symbol` | US ticker (`NVDA`, `BRK-B`), Taiwan `\d{4,6}[A-Z]?.(TW|TWO)`, Yahoo index `^XXX`, or `000001.SS` / `399001.SZ`. Upper-cased; anything else → 400. |
| `interval` / `range` | Whitelisted pairs (see `validate.ts`): `5m`/`15m`/`30m` → `1d`,`5d`,`1mo`; `60m` → `5d`,`1mo`,`3mo`; `1d` → `1mo`…`10y`; `1wk`/`1mo` → `1y`…`max`. |
| `events` | `1` (default) adds `events=div,splits`; `0` omits them. |

Behaviour:

- Upstream: `https://query2.finance.yahoo.com/v8/finance/chart/<symbol>?interval=..&range=..&events=div%2Csplits&includePrePost=false`
  with a browser-like User-Agent and an 8 s timeout; on timeout/5xx/429 it retries `query1`.
- CORS: `Access-Control-Allow-Origin` is echoed only for `https://wudn9922.github.io` and
  `http://localhost:*` / `http://127.0.0.1:*`. Requests carrying any other `Origin` get 403. `OPTIONS` → 204.
  Optional env `ALLOWED_ORIGINS` (comma list) replaces the default origin list (localhost stays allowed).
- Caching: `Cache-Control: public, max-age=60` for intraday intervals, `max-age=600` for `1d`/`1wk`/`1mo`.
- Errors are sanitized JSON `{"error":{"code","message"}}` with `Cache-Control: no-store`:
  400 `invalid_symbol|invalid_interval|invalid_range|invalid_events`, 403 `origin_not_allowed`,
  404 `not_found`, 405 `method_not_allowed`, 502 `upstream_error`, 503 `rate_limited`.

`validate.ts` holds all pure logic and is unit-tested from Atlas
(`atlas/tests/market-chart-function.test.ts`, run by `npm test`).

## Deploy

```sh
supabase login                      # or export SUPABASE_ACCESS_TOKEN=<personal access token>
supabase functions deploy market-chart --no-verify-jwt --project-ref bxhqpfeberqbtxymghyt
```

`--no-verify-jwt` is required: Atlas calls the function without a Supabase key. Automatic deployment:
`.github/workflows/supabase-functions.yml` runs on pushes to `main` that touch this folder (or manually)
once the repository secret `SUPABASE_ACCESS_TOKEN` exists; without it the workflow only prints a notice.

Smoke test after deploying:

```sh
curl -i 'https://bxhqpfeberqbtxymghyt.supabase.co/functions/v1/market-chart?symbol=AAPL&interval=1d&range=1mo&events=0'
curl -i 'https://bxhqpfeberqbtxymghyt.supabase.co/functions/v1/market-chart?symbol=AAPL&interval=1d&range=99y'   # 400
```

Local run: `supabase functions serve market-chart --no-verify-jwt` (needs Docker).

## Limitations

Yahoo's chart endpoint is unofficial and may rate-limit or block cloud egress IPs (Supabase runs on
shared cloud infrastructure). Atlas then shows `暫時無法取得 <symbol> 行情，請稍後再試` and keeps any
cached data; nothing else on the site depends on this function.
