# Verification — Phase 1 archive and Phase 2

The Phase 1 results below are historical. Current Phase 2 results are recorded in [PHASE2_VERIFICATION.md](PHASE2_VERIFICATION.md).

## Phase 1 verification

Executed in Codex Cloud, Debian 13, Node 24.19.0 / npm 11.9.0 on 2026-10-06. No paid APIs, licenses, subscriptions or credit cards were used.

## Required commands

| Command         | Result                                    |
| --------------- | ----------------------------------------- |
| `npm ci`        | PASS, lockfile installation               |
| `npm test`      | PASS, 29 unit/regression tests in 4 files |
| `npm run lint`  | PASS, zero warnings/errors                |
| `npm run build` | PASS, TypeScript + Vite production build  |

Build emits non-fatal annotation warnings from Zod's published source. Bundles are split into chart, storage, React and application chunks; no >500kB chunk warning remains. This is not an interaction benchmark.

## Unit/regression coverage

- Chart transforms: anchor round trip under pan/zoom/resize, null public coordinate conversion, provider-history view restoration.
- Future mapper: fractional logical mapping, cross-timeframe timestamp remap, time-only future whitespace, regular-session/weekend handling.
- Trend Line: release-position P1/P2, realtime preview, endpoint editing, snapshot body movement, generous touch hitboxes, lock/pan ownership, no edit on tap, offset-preserving touch grab.
- Horizontal Line: one gesture creation, movement/edit, future and lock behavior (after Trend Line's initial 10-test gate passed).
- Magnet: O/H/L/C snap, OFF, future disabled, whole-line prohibited.
- History: create/edit/move/delete/lock/unlock, undo/redo, branching.
- SMA: lookback, source/time alignment, period 1/invalid/longer than history, per-symbol isolation and lock domain rules.
- IndexedDB: AAPL 24/58 vs NVDA 43/56, symbol drawings isolation, locks, reload, queued persistence failures, export/import, malformed/duplicate/mismatched data rejection, stale locked drawing commit.
- Providers: deterministic OHLCV for 5m/1H/1D, normalized/sorted/deduplicated Yahoo data and missing OHLC rejection.

## Browser coverage

`npm run test:browser` runs Playwright against desktop Chromium (1440×900), mobile Chromium (iPhone 13 viewport), iPhone WebKit and iPad Pro 11 WebKit. Core tests cover:

- AAPL/NVDA switching, drawing isolation, multiple SMA settings and locked state after reload.
- Press → Drag → Release, edits, snapshot movement, undo/redo, locked-line pan and timeframe changes.
- Horizontal Line, future endpoint, UI lock guards, export download and import restoration.
- Watchlist add/reorder/remove persistence.
- Touch loupe placement on either finger side, endpoint editing and pointercancel; no page scroll.
- No horizontal overflow and rendered-layout screenshots.
- Explicit prototype provider errors and restoring the Demo chart.
- Production shell + SMA persistence after offline reload.
- 2,500 candles, 8 SMA instances and 100 drawings remain operable during crosshair movement and zoom, without runtime errors.

**Final complete run: 32 passed, 4 expected skips, 0 failed (36 project cases, 2.4 minutes).** A subsequent safe-area/scrollable-rail shell adjustment was checked with the layout and production-offline cases again (8 passed); the production build and lint/typecheck were rerun. Desktop touch test is intentionally skipped (not a mobile project); the controlled stress measurement is intentionally limited to desktop Chromium (three other projects skip it).

### Test methodology limits

Mobile Chromium drives real CDP `Input.dispatchTouchEvent` sequences, including touchCancel. WebKit drives native captured mouse pointers while emulating the touch modality for loupe drag coverage. These are **not physical iPhone/iPad Safari tests**, and do not establish real hardware smoothness or touch accuracy.

Chromium PWA testing uses Playwright's actual network-offline mode. Playwright WebKit's `context.setOffline(true)` prevents navigation before the service worker handles it and reports an internal error. WebKit's regression instead starts a dedicated production server, caches the shell/settings, terminates the server, then reloads. All requests to that server then fail with connection refused; the cached app and SMA restore. This does not mask a product fetch error: it independently verifies the failed-network fallback.

Cloud WebKit system dependencies were downloaded and extracted into `/tmp` (no system package install). Its downloaded WPE launcher was adjusted to preserve the inherited `LD_LIBRARY_PATH`. `PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1` avoids the ldconfig-only host-check false negative; actual browser startup and regressions validate the loader. Standard installations should use `npx playwright install --with-deps chromium webkit` without this environment workaround.

## Market data smoke

The real prototype proxy returned AAPL **1D: 1,254 bars** in this environment after enabling inherited proxy handling through Undici EnvHttpProxyAgent. This is a smoke result, not an availability guarantee or a production license claim. UI marks Yahoo as unofficial prototype / delayed / as-of data. Demo is default and always clearly simulated. Free intraday availability/limits remain provider-dependent.

## Performance evidence

See [raw measurement](performance-measurement.json). It records 119 requestAnimationFrame gaps during a controlled headless Chromium stress scenario (2,500 bars, 8 SMAs, 100 drawings), plus runtime-error results. These are **scheduler intervals**, not measured paint/GPU FPS, device latency or a claim of TradingView-equivalent performance. Physical-device loupe screenshot cost and drawing feel still require user testing. No FPS is reported.

## Preview and deliverables

Development server: http://localhost:5173

Production preview: http://localhost:4173

Both bind to 0.0.0.0 in this environment. No externally reachable public URL has been provided by the environment tools. Use Codex Cloud port-forward/Preview for 4173/5173 when offered, or run `npm ci && npm run dev` locally. No GitHub Pages project was created.

Screenshots: `scratch/desktop-chromium.png`, `scratch/mobile-chromium.png`, `scratch/iphone-webkit.png`, `scratch/ipad-webkit.png`, `scratch/stress-100-drawings.png`.

The independent reviewer was unavailable. [REVIEW_PACKAGE.md](../REVIEW_PACKAGE.md) provides the source map, review scope and primary-engineer triage template; no Gemini/Claude review sign-off is claimed. Phase 2 was subsequently authorized and implemented; see the current Phase 2 verification log.
