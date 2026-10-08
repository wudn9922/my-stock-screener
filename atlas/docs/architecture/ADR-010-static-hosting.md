# ADR-010 — GitHub Pages as a static Atlas hosting target

Status: accepted by primary engineer, 2026-10-07. Scope: the user's authorized GitHub commit and browser-accessible website; no V2 features or replacement of accepted V1 engines.

## Decision

Keep the existing React/TypeScript/Vite application, Lightweight Charts 5.2.1 and drawing/storage/provider boundaries. A separate `build:pages` sets `VITE_STATIC_HOSTING=1`, sets the public base to `/lightweight-drawing-lab/`, typechecks, and writes only `dist-pages/`. Normal root builds still produce `dist/` and the server bundle. Public-base changes affect HTML, manifest icons and worker registration, not canonical drawing anchors or stored settings.

Pages cannot serve Node API routes. Exclude Yahoo/SEC proxy plugins from static builds and fail those providers before fetching. Disable new Yahoo selections and show backend-required financial UI. Existing valid backups import atomically without modifying provider preferences; users may switch to Demo explicitly. Static Yahoo bypasses the market cache because a cached hit must not conceal absent backend availability. Normal Yahoo/Demo caching is unchanged. No client secrets, fabricated finances, third-party CORS workaround or paid hosting dependency is added.

The worker derives all shell/assets URLs from its registration scope, excludes same-origin APIs, and deletes only old caches with that scope's encoded prefix. The build revision remains part of the cache name. Manifest start URL, ID, scope and icons are relative. The existing native-wrapper service-worker guard remains intact.

A free GitHub Actions workflow builds/checks `main` and deploys the static artifact. Repository Pages enablement and integration workflow permissions must be verified; committing files does not prove that the website is live. Do not force-overwrite an existing branch or initialize a second local checkout.

## Verification and limits

Normal V1 regressions remain mandatory. Additional isolated production tests exercise the project subpath, all four browser profiles, touch drawing, MA isolation/locks, export/import, unavailable providers with zero API calls, worker cache scope, and offline reopening. Physical Safari UAT and external independent review remain pending.

The static site supports simulated prices and research, not live market/fundamentals access. A future separately hosted HTTPS backend requires its own authorized deployment. IndexedDB is origin-scoped: GitHub Pages project paths do not isolate two Atlas installations under the same owner origin. This hosting change intentionally preserves the accepted database name/schema; use Export/Import when moving from another origin.

## Authorized UAT extension (2026-10-07)

[ADR-011](ADR-011-timeframe-ownership-and-snapshot-prices.md) supersedes historical cross-timeframe defaults and Demo-only static prices. 1M is calendar-aware. Drawings/indicators/history/volume now belong to symbol+timeframe with validated atomic v3 migration. Yahoo quote OHLC is source-split-adjusted, never divided again; real static delayed snapshots and as-of metadata are published by the existing hourly weekday Actions workflow. SEC still needs a backend. Previous unadjusted wording is historical and must not guide new price normalization.
