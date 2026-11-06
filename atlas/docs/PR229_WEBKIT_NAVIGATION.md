# PR #229: WebKit navigation and archive fallback

The failure is a late fallback fetch during document navigation. The production fix aborts the archive provider's document lifetime at `beforeunload`, while retaining `pagehide` and the existing BFCache `pageshow` reset. The smoke script, error assertions, browser matrix and timeouts are unchanged.

## Request owner and event sequence

The market page renders `IndexCard` → `MiniChart` → `getDailyBarResult` → `CachedMarketDataProvider` → `ArchiveFirstProvider` → `KlineArchive`. On the first visible Taiwan / US index, this loads `^TWII` / `^GSPC` archive indexes or year CSVs. A missing/unreadable archive normally falls back through `ScreenerMarketRouter.route` → `StaticSeriesProvider.hasSymbol` → `listSymbols`, requesting `report/series/index.json` to choose official series or Yahoo. This is not a request to load `^TWOII` from the warning card.

The diagnostic copy of the original smoke records `beforeunload`, native fetch initiators, request failures, `pageerror` and `pagehide`. It also found the same race on financial pages through `PeRiverCard`'s daily-price request. The order is:

1. Navigation starts and `beforeunload` fires.
2. WebKit cancels an outstanding archive request (`Load request cancelled`). Its JS promise rejects as a network failure.
3. The old provider still considers the document active because `pagehide` has not fired. The fallback requests the local series index.
4. WebKit refuses this new fetch in the departing document and reports `due to access control checks` to the page, even though the same URL is a valid same-origin HTTP 200 in an active document.
5. `pagehide` finally aborts the old lifetime, too late to prevent the fallback.

In the saved TW trace, `beforeunload` was at 2348 ms, the new series fetch at 2351 ms, and `pagehide` at 2378 ms. The complete baseline diagnostic captured four series-index fetch initiators and four page errors; the fixed diagnostic captured zero of each despite continuing to observe native archive-request cancellation.

The retained/unavailable warning UI only reads `market-warnings.json` and renders report values. It does not request series/archive data. #226/#228 already had the same `pagehide`-only guard. Their green checks do not cover this cancellation ordering; #229's failures expose that existing race. The real generated warning used for both batches is the current calculation, not a retained/unavailable synthetic fixture. Existing browser cases separately cover those UI states.

The fix makes step 2 propagate `AbortError` through the existing lifetime checks instead of entering the fallback. It does not filter WebKit errors or reinterpret arbitrary network failures as cancellations. An active document still uses the existing fallback when the archive is absent or fails. Restoring from BFCache creates a fresh lifetime; old pending work keeps its original aborted signal.

## Preview HTTP behavior

The original `serve_research_preview.py` was tested with iPhone WebKit against the real files:

- `series/index.json`: HTTP 200, `{ "symbols": ["^TWOII"] }`.
- Missing series file: HTTP 404 with an HTML error body, readable by same-origin fetch; no page error.
- An explicitly aborted fetch rejects with `AbortError`; no page error.
- A TCP client resets an in-progress large-file download. The server can log `BrokenPipeError` / `ConnectionResetError` for that connection; the next WebKit series request still returns HTTP 200. No page error.

Thus neither a missing file nor the server's interrupted-connection traceback explains the refused fallback. No server or CORS header change is needed.

## Reproduction and evidence

Both batches use PR HEAD `97ed16f889346b4c7564107e768dc51052696310`, with only the provider lifetime fix in the after build, and generated snapshot `4bf417ce28eb99a0a7049c1f4ce30a1648ab580d` used by failed run 42059838429 attempt 2. The CI's changed-input overlay was applied and its existing real `market-warnings.json` retained. `VITE_PUBLIC_BASE=/my-stock-screener/atlas/ npm run build:pages` is followed by the exact preview tree and server:

```sh
mkdir -p /tmp/research-pages/my-stock-screener
cp -a atlas/dist-pages /tmp/research-pages/my-stock-screener/atlas
ln -s "$PWD/docs/report" /tmp/research-pages/my-stock-screener/report
python3 scripts/serve_research_preview.py --port 4188 --directory /tmp/research-pages
# In atlas/, sequentially, without retries or early exit on a failure:
for iteration in $(seq 1 20); do npm run test:research-pages; done
```

Each invocation launches fresh Chromium and iPhone WebKit contexts and runs the entire unchanged smoke journey. The local result collector saves each exit code, stdout and JSON before starting the next invocation, removing stale output first. Both builds and both batches use identical report inputs and browser versions (Playwright 1.63.0, Chromium revision 1243, WebKit 26.6 revision 2359). The local OS is Debian 13, while GitHub uses Ubuntu; this is emulated iPhone WebKit, not a physical iPhone.

The Debian worker has no sudo. Official Debian browser dependencies were extracted locally; the WebKit launcher only adds their library directory. Playwright's ldconfig-only host validation is bypassed because it cannot see these local libraries; the browser engine and smoke checks are unchanged. All libraries were checked by actual browser startup and the full journeys.

Results and file hashes are recorded in `pr229-webkit-verification.json`. Full per-invocation JSON/stdout, builds, event traces and the HTTP probe remain in ignored `atlas/test-results/pr229-evidence/`. Preliminary runs from the interrupted session are excluded.

The new event-order regression fails both cases on the original source (archive absent and cancelled), then passes with the fix. It verifies no fallback before `pagehide`, rejection of new requests in the departing document, and successful requests after BFCache restoration. Independent review was not available; this is primary self-review.

Additional checks passed: all 1,920 unit tests in 154 files, lint, the regular build and both Pages builds. The existing `archive-first`, `index-volume` and `navigation-recovery` browser suites passed all 33 cases across desktop Chromium, mobile Chromium and iPhone WebKit, including retained/unavailable warning states.

## Completed 20-run comparison

| Build | Desktop Chromium | iPhone WebKit | iPhone page errors | Smoke exit 0 |
| --- | ---: | ---: | ---: | ---: |
| before | 20/20 | 0/20 | 66 | 0/20 |
| after | 20/20 | 20/20 | 0 | 20/20 |

All 66 baseline errors are the reported `series/index.json due to access control checks` error. Every after-run has empty `errors` on both browsers and passes the unchanged byte-range, real-data, source-value and overflow checks.

| Iteration | Before desktop | Before iPhone | Before iPhone errors | After desktop | After iPhone | After iPhone errors |
| ---: | --- | --- | ---: | --- | --- | ---: |
| 1 | PASS | FAIL | 4 | PASS | PASS | 0 |
| 2 | PASS | FAIL | 4 | PASS | PASS | 0 |
| 3 | PASS | FAIL | 4 | PASS | PASS | 0 |
| 4 | PASS | FAIL | 4 | PASS | PASS | 0 |
| 5 | PASS | FAIL | 4 | PASS | PASS | 0 |
| 6 | PASS | FAIL | 4 | PASS | PASS | 0 |
| 7 | PASS | FAIL | 4 | PASS | PASS | 0 |
| 8 | PASS | FAIL | 4 | PASS | PASS | 0 |
| 9 | PASS | FAIL | 3 | PASS | PASS | 0 |
| 10 | PASS | FAIL | 4 | PASS | PASS | 0 |
| 11 | PASS | FAIL | 3 | PASS | PASS | 0 |
| 12 | PASS | FAIL | 3 | PASS | PASS | 0 |
| 13 | PASS | FAIL | 3 | PASS | PASS | 0 |
| 14 | PASS | FAIL | 3 | PASS | PASS | 0 |
| 15 | PASS | FAIL | 3 | PASS | PASS | 0 |
| 16 | PASS | FAIL | 3 | PASS | PASS | 0 |
| 17 | PASS | FAIL | 2 | PASS | PASS | 0 |
| 18 | PASS | FAIL | 2 | PASS | PASS | 0 |
| 19 | PASS | FAIL | 3 | PASS | PASS | 0 |
| 20 | PASS | FAIL | 2 | PASS | PASS | 0 |
