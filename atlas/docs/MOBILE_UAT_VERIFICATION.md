# Mobile chart UAT correction — 2026-10-07

The five issues from the user's physical iPhone screenshot are addressed in the existing Atlas V1 source. This is a V1 correction, with the same Lightweight Charts5.2.1 engine, drawing controller and IndexedDB/export schema.

## Behavior

- Volume bars are green when close ≥ open and red otherwise. Both legacy and explicit Volume include a fixed, actual-volume20-bar SMA; its first19bars have no average point/N/A. Volume color settings style the average. Visibility/removal applies to histogram and average together; an explicit hidden Volume never resurrects legacy volume.
- SMA/EMA numerical values are shown in a passive external legend. Values follow a real crosshair candle; future/outside/no-crosshair reads the latest. Scope, hide and warmup are handled without misleading zero values. Hover uses the existing singleRAF and DOM writes; calculations stay cached and per-symbol.
- Indicator controls are in Manage indicators. Two phone legend rows fit in one44px external band; additional indicators scroll horizontally and cannot create large cards over candles.
- Zoom controls are44px rail buttons outside the plot. Native chart pinch/wheel/pan and drawing ownership are unchanged.
- Fullscreen expands the same chart host within the app-shell. Unsupported or rejected native fullscreen falls back to CSS chart focus; Safari browser chrome may remain. Dialogs stay usable; Exit remains available; Escape first closes an owned dialog/gesture then focus, while ordinary inputs retain Escape. No fullscreen preference is persisted.

## Measured layout

Same local production Pages build, headless Chromium iPhone13 viewport390×664:

| Measurement | Before | After |
| --- | ---: | ---: |
| Chart host height | 321 CSS px | 333 CSS px |
| Fullscreen chart height | unavailable | 518 CSS px |
| Two indicator action cards | 95px, overlaid | removed |
| Passive indicator legend | unavailable | 26px within44px external band |
| Zoom | 90×46px over volume | two44×44px rail actions outside host |

Visual inspection: both SMA24/58 names and values are visible; red/green volume and its average render; no zoom overlap. Additional Volume/indicator values can be reached by horizontal legend scroll. Evidence: scratch/mobile-uat-before.png, mobile-uat-after.png, mobile-uat-fullscreen.png and mobile-uat-after.json (local artifacts excluded from source publication).

## Verification

- Clean npmci: PASS.
- npmtest:138/138 PASS in18files.
- lint: PASS, zero errors/warnings (unused old chip icons removed).
- Normal production build and project-path Pages build: PASS.
- Final focused gate:8/8 PASS across desktop Chromium, mobile Chromium, iPhone WebKit and iPad WebKit (fullscreen drawing/export/re-lock and original HorizontalLine/export/lock guards). Pages gate:8/8 PASS across all4profiles, including offline settings. Final full regression:94PASS/10 original expectedSKIP/0FAIL (104cases,11.1minutes); combined with Pages8PASS:102browserPASS/10expectedSKIP/0FAIL. All4profiles pass. Skips remain the desktop-only controlled stress tests on mobile and desktop touch-only case; no new skip was added.

All baseline drawing, per-symbol indicators, locks, storage, export/import, error/provider, backtest/alert and offline tests remain enabled. No regression skips were added. New tests cover independent MA/volume-average arithmetic, bounded8-indicator layout, paired hide/reload, symbol/timeframe readout isolation, fullscreen engine identity, mobile locked drawing persistence, no scroll, orientation, visible dialogs, Escape and native rejection fallback.

Selection-resize regression found during the first full run: making the status row24px until selection resized the plot and displaced existing editing targets. Primary restored a reserved48px mobile action row in normal/focus layouts; the original endpoint/body/lock/pan/timeframe case then passed on mobile Chromium, iPhone WebKit and iPad WebKit. The new focus test checks unchanged chart height after selection/lock. Full final regression uses this corrected source.

Notification-overlay regression: taller layout exposed the export toast over the drawing action row. Mobile notices now dock at the top and their text does not intercept pointer events; Dismiss remains44px and interactive. Original export→drawing lock and new export→unlock/re-lock tests pass without dismissing the notice or forcing clicks. The selected timeframe centers within its horizontal strip after selection/width changes, so fullscreen controls do not leave active1D offscreen. A test-only invalid Unlock label was corrected to the existing Lock toggle's aria-pressed behavior; product labels were preserved.

## Review and limits

Sol6.1High plan and read-only review; Luna6Max frozen chart/UI execution. All findings were triaged by primary before changes; REVIEW_PACKAGE.md records decisions. Independent Gemini/Claude review pending, no independent sign-off. Physical iPhone/iPad Safari UAT still pending; headless WebKit does not prove physical drawing feel, browser chrome behavior, PWA install or device FPS.

The published GitHub Pages build continues to mark Demo as simulated. Yahoo and SEC require a backend and remain explicitly unavailable on static hosting. This patch does not add data services or fabricate fundamentals. Existing IndexedDB data is retained; to refresh an installed/cached app, close all its tabs/windows and reopen/reload. Do not clear website data, which would delete local settings.

## Publication

Target remains https://wudn9922.github.io/lightweight-drawing-lab/. Source commit1426afc9e15d082a052424882cdf05b7fd62e12c deployed successfully in Actions37615088219 (hostedci/test/lint/Pagesbuild/deployPASS). Actual HTTPS HTML, all referenced JS/CSS/icons/manifest/worker returned200; every asset matched the locally tested production build. Remote iPhone WebKit with TLS verification enabled passed numericSMA24/58, MA20, lockedMA reload, export, fullscreen/settings and explicitSEC unavailable; zeroAPIrequests/runtimeerrors. Its390×664 normal chart measured334px/focus518px (local Chromium333px). Evidence: mobile-uat-live-http.json and mobile-uat-live-browser.json. The follow-up documentation/evidence commit does not change frontend source or build assets. No GitHub Pages project replacement, repository initialization or V2 work.
