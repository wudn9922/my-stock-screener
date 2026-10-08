# Atlas in my-stock-screener

This directory is a vendored copy of **Atlas Research Terminal** (Vite + React 19 + TypeScript,
lightweight-charts 5.2.1), adapted so the daily screener report can open a chart, drawings and
SEC financials for any ticker in an iframe.

## Origin

- Source repository: <https://github.com/wudn9922/lightweight-drawing-lab> (branch `main`)
- Vendored commit: `7a2d61098f20c0aae6042b8bf8a27e1ad0041238`
  (2026-10-08, "Record Taiwan and SEC snapshot deployment verification"). This upstream commit
  already contains Taiwan `.TW/.TWO` support and upstream SEC financial packs (`financial-data/`).
- Copied: every tracked file except `.github/` (the upstream Pages workflow must not run here).
  Generated folders (`node_modules/`, `dist*/`, `public/market-data/`, `public/financial-data/`,
  `public/fundamentals/`, test reports) are not copied and are git-ignored.
- `THIRD_PARTY_NOTICES.md`, the font license (`src/assets/fonts/OFL.txt`,
  `public/licenses/`), tests, lint config, lockfile, `.agent-skills/` and `docs/` are kept.
  Upstream has no top-level `LICENSE` file.

Deployment: `https://wudn9922.github.io/my-stock-screener/atlas/` (built into `docs/atlas/`).
The report opens `atlas/?symbol=<TICKER>&tf=<TIMEFRAME>`, for example
`atlas/?symbol=2330.TW&tf=1D`, `atlas/?symbol=NVDA&tf=1W`, `atlas/?symbol=%5ETWII&tf=1D`.

## Modifications

**A. Ticker rule.** `CANONICAL_SYMBOL_REGEX` (`src/market-data/MarketProfile.ts`) now also accepts
Yahoo indices `^[A-Z0-9][A-Z0-9.-]{0,14}` (`^TWII`, `^TWOII`, `^GSPC`, `^SOX`, …) next to the
existing US grammar and explicit Taiwan symbols (`2330.TW`, `6488.TWO`, `00632R.TW`). Bare digits
(`2330`) are still not canonical; the search box and URL resolve them through the official Taiwan
directory, as upstream does. `^TWII`/`^TWOII` use the Taiwan (TWSE/TPEx, TWD, Asia/Taipei)
profile; other indices use the US profile. The same regex drives `normalizeSymbol`, the market
snapshot schema, the settings schema and (new) the SEC snapshot schema. The error text is now
market-neutral: `請輸入有效的股票或指數代號（例如 NVDA、BRK-B、2330.TW、6488.TWO、^TWII）`.
The Yahoo and SEC backends still validate through `normalizeSymbol` and URL-encode the symbol;
the SEC backend now answers `[]` for Taiwan listings and indices without contacting SEC
(`src/fundamentals/SecEligibility.ts`).

**B. URL parameters.** `src/app/LaunchParams.ts` (pure, unit-tested) parses `symbol` and `tf`
(`timeframe` alias). After the store is ready, `App.tsx` applies it once per page load through the
same `switchSymbol` path as the search box. On static hosting a `symbol` parameter also selects
the `snapshot` provider (Yahoo 延遲快照; Demo prices are fictional). The timeframe is applied when
the provider supports it and the symbol's snapshot contains it; otherwise a notice explains the
fallback. Invalid input shows the existing notice and leaves the chart unchanged.

**C. Static financials.** `SecEdgarProvider` in static mode reads
`${BASE_URL}fundamentals/<SYMBOL>-<annual|quarterly>.json`, validates with
`financialSchema.array()`, keeps the symbol/period mismatch check and the in-memory cache, and
reads optional freshness from `fundamentals/manifest.json` (no "live" label for static files).
A 404 (or an SPA `index.html` fallback) shows
`此股票沒有預先下載的 SEC 財報（台股、指數與 ETF 不適用；美股資料每日更新）`. Taiwan listings and
indices never request a file. Screener builds use this layout by default; other static builds keep
upstream's `financial-data/` packs (`VITE_STATIC_FUNDAMENTALS=pack|per-period` overrides). The
footer text on screener builds describes the daily snapshots.

**D. Browser storage isolation.** IndexedDB names are per origin, and the original Atlas runs on
the same origin (`/lightweight-drawing-lab/`). For a `VITE_PUBLIC_BASE` beginning with
`/my-stock-screener/`, every database gets a `-screener` suffix (`atlas-terminal-screener`,
`atlas-market-cache-screener`, `atlas-financial-snapshots-v1-screener`); other builds keep the
original names (`src/app/HostingMode.ts`). The service worker was already scoped: it registers with
`scope: BASE_URL`, and its Cache Storage names embed the registration scope, so activation only
removes caches of the same deployment. No `localStorage` is used.

**E. Index file names.** Snapshot files keep the raw symbol (`market-data/^TWII.json`) and are
fetched URL-encoded (`%5ETWII.json`). Verified with `vite preview` and a plain static server under
`/my-stock-screener/atlas/`; GitHub Pages decodes percent-encoded paths the same way.

## Commands (run from `atlas/`)

```sh
npm ci
# Allowlist from Supabase `stocks` + `index_configs` → scripts/market-symbols.json.
# Env: SUPABASE_URL (with or without /rest/v1), SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY,
#      optional SUPABASE_USER_ID. Exits non-zero and keeps the old file on any failure.
node scripts/screener-symbols.mjs
# Delayed Yahoo snapshots → public/market-data/<SYMBOL>.json + manifest.json.
# Optional ATLAS_TIMEFRAMES (comma list, e.g. 1D,1W,1M) limits intervals; 1D is always collected.
ATLAS_TIMEFRAMES=1D,1W,1M node scripts/refresh-market.mjs
# SEC financials → public/fundamentals/<SYMBOL>-annual.json, <SYMBOL>-quarterly.json, manifest.json.
# Env: SEC_USER_AGENT (default "my-stock-screener-atlas (https://github.com/wudn9922/my-stock-screener)"),
#      FUNDAMENTALS_MAX_AGE_HOURS (default 20; reuse a recent successful check without calling SEC).
node scripts/build-fundamentals.mjs
VITE_PUBLIC_BASE=/my-stock-screener/atlas/ npm run build:pages   # → dist-pages/
```

`build-fundamentals` skips `.TW/.TWO` and `^` symbols without contacting SEC, writes nothing for
tickers without a CIK or without supported 10-K/10-Q facts, keeps previously built files when SEC
fails, and otherwise falls back to the versioned upstream seed packs in `data/financial-snapshots/`
(real SEC facts, original fetch times preserved). Files contain no timestamps and are rewritten
only when the normalized records change. The manifest records `status` per symbol
(`fresh`, `retained`, `seed`, `skipped`, `no-cik`, `no-data`, `unavailable`).

Checks: `npm test`, `npm run lint`, `npx tsc -b`. Screener-specific unit tests are in
`tests/screener-integration.test.ts`.
