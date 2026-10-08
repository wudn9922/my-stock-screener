# Monthly, timeframe isolation and actual delayed prices — verification

Environment: same Atlas Cloud workspace, Node24/npm11, 2026-10-08. Same workspace and source tree; no archive extraction, project recreation, paid dependency, or frontend credentials.

Current status: **Current-price extension implementation and verification 100% complete.** P1-MONTH-01, P1-SCOPE-02, P1-CURRENT-03, and P2-CURRENT-04 are closed after primary triage and Sol read-only review. Corrected source commit [ee10f6743e92de8cb1eb9bf994ce26235eb3eac0](https://github.com/wudn9922/lightweight-drawing-lab/commit/ee10f6743e92de8cb1eb9bf994ce26235eb3eac0) was published without force to `main` (205 files); [Actions run 37711284860](https://github.com/wudn9922/lightweight-drawing-lab/actions/runs/37711284860) succeeded with deployment. The corrected site is [wudn9922.github.io/lightweight-drawing-lab](https://wudn9922.github.io/lightweight-drawing-lab/). TLS-verified HTTP asset/hash verification passed; the iPhone WebKit live check passed at 2026-10-08T01:18:28.720Z. Evidence is in [the HTTP record](timeframe-price-live-http.json) and [the browser record](timeframe-price-live-browser.json); the browser check validated current 1D/1W/1M quote/bar alignment. Physical-device UAT and independent Gemini/Claude review remain pending. Do not treat Playwright WebKit as physical Safari or mark overall V1 externally reviewed.

### P1-CURRENT-03 — source fix deployed; live proof passed

The previous live SMCI pack reported a $44.94 regular-market quote as of 2026-10-07 20:00 UTC, while its native current 1M aggregate still closed at $43.46 on 2026-10-06. The accepted source fix heals only a validated missing latest daily close from matching regular-market metadata, requires the fresh daily latest bar's UTC date and close to match the pack quote, and reconstructs only the current 1W/1M bucket from validated daily OHLCV. Native historical bars are preserved; provenance records healing and current-period derivation. Snapshot v3 rejects 1D/latest-current-period bars whose UTC date/bucket or close disagree with the quote. On a failed interval guard the interval is omitted; if fresh daily quote data is missing or inconsistent, preserve the entire prior snapshot with its old timestamps. The collector fetches 1D after 1W/1M so the quote metadata is at least as recent as the native payload. Sol's read-only review accepted and closed the source finding.

### P2-CURRENT-04 — accepted proxy cache retry

A fresh native 1W/1M response can be newer than a still-fresh cached raw daily payload. The proxy now retries the fixed-host 1D10y request once, with the same timeout signal, only when the helper reports that the cached daily metadata is older than the native period response. It replaces the cached payload and retries normalization once; unrelated errors do not trigger a retry. The focused middleware regression passes.

### Current correction gates

- Clean `npm ci`: PASS.
- Full `npm test`: 167 passed across 20 files in 9.43 seconds.
- `npm run lint`: PASS; `npm run typecheck`: PASS.
- Schema-v3 local refresh: all 20 symbols available with all seven supported timeframes. Refreshed SMCI daily/1W/1M close is $44.94, with 1W volume 90,284,251 and 1M volume 161,285,951. Refreshed NFLX daily/1W/1M close is $69.70, with 1W volume 102,053,115 and 1M volume 182,309,915.
- Normal and Pages builds: PASS, producing `index-DV6Ur9iz.js` and `index-B36DL1MA.js`; chart and storage chunks are unchanged.
- Normal browser gate: 98 passed, 10 original expected skips, 0 failed (12.2 minutes).
- Pages browser gate: 20 passed, 0 failed (2.1 minutes); combined browser result: 118 passed, 10 original expected skips, 0 failed.
- Corrected source commit `ee10f6743e92de8cb1eb9bf994ce26235eb3eac0` on `main`; Actions run `37711284860`: deployment PASS.
- TLS-verified HTTP asset/hash verification: PASS; see the HTTP record.
- iPhone WebKit live smoke: PASS at 2026-10-08T01:18:28.720Z. Both quote and current 1D/1W/1M closes align for SMCI ($44.94) and NFLX ($69.70), as of 2026-10-07 20:00 UTC. All current OHLCV values are captured in the browser record.
- The live smoke also passed 1D/1W/1M SMA and drawing isolation, locked MA reload, monthly-future drawing locked reload, and settings export v3; it reported zero backend API requests and zero runtime errors. HTTP assets match the production build.

The first current-correction Pages run had four failures caused by the test fixture builder using an older filtered NFLX monthly capture without October 1/2. The fixture builder now uses actual current NFLX daily/weekly/monthly captures with provenance; all assertions remain and no product guard was loosened. The final Pages run passed all 20 cases. The original offline/PWA cases still run with the service worker enabled, and the existing 10 expected skips were not changed.

### Live OHLCV observations

These values were observed from actual same-origin Yahoo delayed snapshots during the live smoke at 2026-10-08T01:18:28.720Z. Quotes are as of 2026-10-07 20:00 UTC; they are historical regular-session observations, not realtime or fixed future prices.

| Symbol | Period | Open | High | Low | Close | Volume |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| SMCI | 1D | 42.52 | 45.765 | 42.46 | 44.94 | 40,358,151 |
| SMCI | 1W | 43.57 | 45.765 | 42.46 | 44.94 | 90,284,251 |
| SMCI | 1M | 40.89 | 45.765 | 40.13 | 44.94 | 161,285,951 |
| NFLX | 1D | 68.80 | 69.79 | 68.405 | 69.70 | 29,990,515 |
| NFLX | 1W | 67.05 | 69.79 | 66.54 | 69.70 | 102,053,115 |
| NFLX | 1M | 69.41 | 69.79 | 66.54 | 69.70 | 182,309,915 |

The quote-to-close check, timeframe/drawing/SMA isolation, locked reload, monthly-future drawing, and export v3 assertions all passed. The private smoke harness was adjusted to capture the persisted expected snapshot after locking and keep gestures inside the plot; these were harness-only changes, and application source did not change after the passing gates. Full evidence is in [the TLS HTTP record](timeframe-price-live-http.json) and [the iPhone WebKit record](timeframe-price-live-browser.json). The WebKit profile is automated browser evidence, not physical-device UAT.

## Implemented and reviewed

Calendar1M, conservative nextmonthET00 closed-bar boundary,600Demo monthlyhistory, nativeYahoo1mo10y; v3strict symbol+timeframeownership, atomiclegacy1/2 migration/import, preservedIDs/anchors/styles/locks, migrationhomeaudit, scopedpanels/presets/Volume/alerts andbucketundo. Existing drawing gestures/oneLWC5.2.1 unchanged. QuoteOHLC splitbasis+metadata+versionedcache, SnapshotProvider/Schema3 andhourlyweekdayActions20tickerrefresh, noDemo fallback foractualpricefailures. Current-period source correction is deployed from `ee10f6743e92de8cb1eb9bf994ce26235eb3eac0`; run 37711284860 succeeded. Live close alignment and the listed browser journeys pass; physical-device UAT and external review remain pending. SeeADR011.

Source review: GPT6.1SolHigh read-only review and primary triage closed P1-MONTH-01 native aggregate correction, P1-SCOPE-02 timeframe isolation, P1-CURRENT-03 current daily/weekly/monthly quote consistency, and P2-CURRENT-04 stale raw-daily proxy retry. Focused middleware and full local gates pass. Corrected source `ee10f6743e92de8cb1eb9bf994ce26235eb3eac0` is deployed by successful run 37711284860; TLS HTTP and iPhone WebKit evidence validates current quote/bar alignment. Independent Gemini/Claude review and physical-device UAT remain pending.

## Previous deployed baseline gates (historical; not proof of current-price consistency)

- `npm ci`: PASS, clean locked installation.
- `npm test`: PASS, 156 tests across 19 files.
- `npm run lint`: PASS.
- `npm run typecheck`: PASS.
- `npm run build`: PASS, TypeScript and normal production build.
- `npm run build:pages`: PASS, static project-path build.
- Normal browser gate: 98 passed, 10 original expected skips, 0 failed (11.5 minutes).
- Pages browser gate: 16 passed, 0 failed (1.8 minutes).
- Combined browser result: 114 passed, 10 expected skips, 0 failed.

An earlier Pages run had three test-only failures: mobile quote rows were hidden until the Watchlist drawer opened, and the service worker served the app shell instead of the mocked unknown-symbol 404. A mobile-aware quote helper and service-worker blocking for only the snapshot-mock test fixed that harness. On the latest correction, the first four Pages failures came from the old filtered NFLX monthly fixture missing October 1/2; real current NFLX captures now supply the full daily aggregation range. Original offline/PWA tests still use the service worker; assertions and skips were not reduced. Legacy v1/v2 migration fixtures remain intact while expectations account for timeframe-scope migration. Yahoo cache assertions obtain provider identity from YahooProvider and retain TTL, stale-offline, and range coverage.

Earlier concurrent browser attempts produced artifact-path conflicts. The final pre-publication gates ran sequentially with isolated normal and Pages output directories and cover P1-CURRENT-03. At that checkpoint the correction still needed publication and live proof; this was completed by commit `ee10f6743e92de8cb1eb9bf994ce26235eb3eac0`. HTTP asset/hash verification passed; the browser evidence is timestamped 2026-10-08T01:18:28.720Z.

## Actual market-source inspection

Historical pre-correction inspection refreshed 20 allowlist tickers to snapshot schema2 with seven native intervals. SMCI/NFLX daily/nativeweekly/nativemonthly actual responses are filtered fixture files under tests/fixtures/market; provenance JSON records original URLs/capture times/SHA256. The pinned 2026-10-06 fixture quotes SMCI $43.46 and NFLX $68.69 are historical evidence, not current prices. Prior live quote observations were SMCI $44.94 and NFLX $69.70, both as of 2026-10-07 20:00 UTC; they are observations from that run, not values to assume indefinitely.

The prior October native aggregates retained SMCI close $43.46 and volume 120,881,200 (weekly volume 49,879,500), and NFLX monthly volume 152,129,500 / weekly 71,872,700. Those figures describe the captured payload only; they do not validate that the latest bucket matches a newer daily quote. Already split-adjusted quote OHLC remains undivided, `adjclose` is ignored, and future bars are never fabricated. The current schema-v3 refresh now has all 20 symbols and all seven timeframes; refreshed SMCI/NFLX quote and volume values are recorded in Current correction gates above.

## Limitations

Market data uses 20 fixed-ticker, delayed regular-session Yahoo snapshots from an unofficial prototype; it is not realtime. Unknown symbols or failed intervals remain explicitly unavailable. SEC backend access is unavailable on Pages. Demo prices remain fictional. Legacy global MA creation timeframe cannot be recovered; migration assigns the symbol's last preferred timeframe and preserves the audit explanation. Physical iPhone/iPad touch, loupe, pinch, scroll, PWA, safe-area, and long-session UAT remains pending; Playwright WebKit is not physical Safari. Independent external review remains pending; no FPS claim is made.
