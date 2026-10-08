# Atlas in my-stock-screener

This directory is a vendored copy of **Atlas Research Terminal** (Vite + React 19 + TypeScript,
lightweight-charts 5.2.1), adapted so the daily screener report can open a chart and drawings for any Taiwan or US stock or
index. Prices are fetched live (delayed) through a Supabase Edge Function; no per-stock data files are
stored in git or deployed.

## Origin

- Source repository: <https://github.com/wudn9922/lightweight-drawing-lab> (branch `main`)
- Vendored commit: `7a2d61098f20c0aae6042b8bf8a27e1ad0041238`
  (2026-10-08, "Record Taiwan and SEC snapshot deployment verification"). This upstream commit
  already contains Taiwan `.TW/.TWO` support and upstream SEC financial packs (`financial-data/`).
- Copied: every tracked file except `.github/` (the upstream Pages workflow must not run here).
  Generated folders (`node_modules/`, `dist*/`, `public/market-data/`, `public/financial-data/`,
  `public/fundamentals/`, `public/symbols/directory.json`, `public/valuation/`, test reports) are not
  copied and are git-ignored. The upstream SEC seed packs (`data/financial-snapshots/`) were removed.
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

**C. Financial statements removed.** The static SEC statement pipeline (`scripts/build-fundamentals.*`,
`scripts/refresh-fundamentals.*`, the deployed `fundamentals/` and `financial-data/` folders and the
11 MB `data/financial-snapshots/` seeds) was removed; the site shows P/E and P/E TTM instead (see
*Data architecture*). The local-backend SEC code (`src/fundamentals/Sec*`, `secProxy`, `server/`) and the
modules still imported by `src/ui/FinancialPanel.tsx` (`FinancialSnapshotProvider`,
`FinancialSnapshotSchema`, the static mode of `SecEdgarProvider`) are kept until the UI no longer
imports them; on Pages they simply find no files.

**D. Browser storage isolation.** IndexedDB names are per origin, and the original Atlas runs on
the same origin (`/lightweight-drawing-lab/`). For a `VITE_PUBLIC_BASE` beginning with
`/my-stock-screener/`, every database gets a `-screener` suffix (`atlas-terminal-screener`,
`atlas-market-cache-screener`, `atlas-financial-snapshots-v1-screener`); other builds keep the
original names (`src/app/HostingMode.ts`). The service worker was already scoped: it registers with
`scope: BASE_URL`, and its Cache Storage names embed the registration scope, so activation only
removes caches of the same deployment. No `localStorage` is used.

**E. Index file names.** Files named after a raw index symbol (`report/series/^TWOII.json`, formerly
`market-data/^TWII.json`) are fetched URL-encoded (`%5ETWOII.json`); GitHub Pages decodes
percent-encoded paths.

## Data architecture

Nothing per stock is stored in git or deployed. Three kinds of data:

| Data | Where it comes from | Module |
| --- | --- | --- |
| Prices (all stocks/indices, 5m–1M) | Live, on demand: Supabase Edge Function `market-chart` → Yahoo chart API (delayed, unofficial) | `src/market-data/EdgeYahooProvider.ts` |
| Official index series (e.g. `^TWOII` 櫃買指數 from TPEx) | Written daily by the Python report job: `docs/report/series/index.json` + `series/<symbol>.json` | `src/market-data/StaticSeriesProvider.ts` |
| Symbol search (all TW + US common stocks) | Built in Actions: `public/symbols/directory.json` | `loadSymbolDirectory` / `searchSymbols` in `src/market-data/SymbolCatalog.ts` |
| P/E, P/E TTM | Built in Actions: `public/valuation/us.json`, `tw.json` | `src/fundamentals/ValuationProvider.ts` |

### Market data

`createScreenerMarketProvider(options?)` (`src/market-data/createScreenerMarketProvider.ts`) returns one
`MarketDataProvider` (id `screener`): symbols listed in `${reportBase}series/index.json` go to
`StaticSeriesProvider` (1D from the file, 1W = ISO week from Monday, 1M = calendar month, aggregated
client-side; other intervals unsupported → `getSymbolTimeframes` returns `['1D','1W','1M']` so the UI
disables them); every other symbol goes to `EdgeYahooProvider`. The router is wrapped in the existing
IndexedDB `CachedMarketDataProvider` (60 s TTL, stale fallback when offline). `reportBase` defaults to
`${BASE_URL}../report/` → `/my-stock-screener/report/`.

`EdgeYahooProvider(proxyUrl = VITE_MARKET_PROXY_URL)` requests
`<proxy>?symbol=&interval=&range=&events=0|1` with the intervals of `YahooIntervals.ts` and normalizes
the raw Yahoo JSON with the same pure functions as the local backend (`normalizeYahooResponse`,
`normalizeYahooEvents`, and for 1W/1M `normalizeYahooCalendarResponse`, which rebuilds the current
week/month from verified daily rows; if that verification fails the native aggregates are shown and the
source says so). One daily download serves 1D bars, the quote and dividends/splits. In-flight requests
are shared (the network request is aborted only when every caller aborted), raw payloads are kept in
memory for 60 s (intraday) / 5 min (daily+), requests time out after 15 s. Errors:
`暫時無法取得 <symbol> 行情，請稍後再試` (transient, `MarketDataUnavailableError`),
`找不到 <symbol> 的行情資料…` (404), `尚未設定行情代理網址…` (no URL at build time). World indices
quoted in a non-USD currency (`^N225`, `^FTSE`) are accepted as index points.

### Symbol directory

`scripts/build-directory.mjs` → `public/symbols/directory.json`:
`{"version":1,"generatedAt":"…","counts":{…},"sources":{…},"items":[["2330.TW","台積電","TWSE"],["NVDA","NVIDIA Corporation","NASDAQ"],…]}`.

- Taiwan: official ISIN pages `https://isin.twse.com.tw/isin/C_public.jsp?strMode=2` (上市) and `strMode=4`
  (上櫃), Big5/MS950 HTML; keeps 4-digit codes with CFI `ES*` in the 股票/創新板 sections (includes `-KY`
  primary listings). Fallbacks in order: TWSE `t187ap03_L` / TPEx `mopsfin_t187ap03_O` issuer lists, the
  previously deployed directory, the tracked `public/symbols/taiwan.json`.
- US: NasdaqTrader `nasdaqlisted.txt` + `otherlisted.txt`; drops `ETF=Y`, `Test Issue=Y`, warrants,
  rights, units (MLP common units kept), preferreds, notes/bonds, closed-end/fund-like names, SPAC
  shells; `BRK.B`/`BRK/B` → `BRK-B`. ADRs of operating companies (TSM, NVO, ASML) are included.
- Excluded on purpose: ETFs/ETNs, warrants, preferreds, REIT beneficiary certificates and **TDRs**
  (Taiwan depositary receipts: few, thinly traded, EPS not comparable).
- Expected size ≈ 8,000 rows (≈1,060 TWSE + ≈880 TPEx + ≈6,000 US) ≈ 330–380 KB (≈100–120 KB gzip).

Client: `loadSymbolDirectory(signal?)` loads it once (falls back to the Taiwan catalog when the file is
missing), `searchSymbols(query, limit = 20)` ranks exact ticker → ticker prefix → name prefix → English
word prefix → Chinese substring; `resolveDirectoryInput('2330', entries)` → `2330.TW`. The old
`loadSymbolCatalog` / `searchSymbolCatalog` / `resolveSymbolInput` API is unchanged.

### Valuation (P/E)

`scripts/build-valuation.mjs` → `public/valuation/us.json` and `tw.json`, each
`{"version":1,"market":"US|TW","generatedAt":…,"source":…,"notes":…,"items":{"<SYMBOL>":{"epsTtm","epsAnnual","fiscalYear","exchangePeTtm"(TW),"asOf":"YYYY-MM-DD","source":"SEC frames|TWSE|TPEx","basis"?:"basic"}}}`.

- US: SEC XBRL **frames** (`/api/xbrl/frames/us-gaap/EarningsPerShareDiluted/USD-per-shares/CY2026Q2.json`,
  fallback `EarningsPerShareBasic`): 11 quarterly + 4 annual frames per concept (≈30 requests at ≈6/s,
  `User-Agent` from `SEC_USER_AGENT`), CIK → tickers via `company_tickers.json`, limited to directory
  symbols. EPS TTM = latest four contiguous quarters (latest within ~a year); a missing fiscal Q4 (only in
  the 10-K) is derived as annual − the other three quarters of that fiscal year; EPS annual = latest
  fiscal year (≤ 2 years old). Limitations: frames are calendar-aligned (SEC maps each fiscal period to
  the closest calendar quarter/year), derived Q4 ignores share-count changes, quarters before/after a
  stock split may not be restated consistently, and foreign filers reporting under IFRS or in non-USD
  (TSM, NVO, many ADRs) have no us-gaap USD EPS → no P/E. Expected ≈ 4,500–5,500 records ≈ 450–550 KB.
- Taiwan: TWSE `BWIBBU_ALL` and TPEx `tpex_mainboard_peratio_analysis` P/E (exchange P/E uses the latest
  four quarters → `exchangePeTtm`); EPS TTM = close / P/E using TWSE `STOCK_DAY_ALL` / TPEx
  `tpex_mainboard_daily_close_quotes` of the same date. Annual EPS: TWSE `t187ap14_L` / TPEx
  `mopsfin_t187ap14_O` only publish the latest quarter's cumulative EPS, so `epsAnnual` is set when that
  quarter is Q4 (spring) and carried over from the previous file afterwards; until the first spring run
  it is `null`. Loss makers (blank P/E) are omitted. Expected ≈ 1,800 records ≈ 200 KB.

Client: `getValuation(symbol)` / `new ValuationProvider(base).getValuation(symbol)` → `Valuation | null`
(indices → null; each market file loaded once, a failed load retried after 60 s); `computePe(price, eps)`
→ `number | null` (null for missing or non-positive EPS); `describePe` adds `negativeEarnings` and a
reason; `summarizeValuation(valuation, price)` returns both P/E figures.

## Commands (run from `atlas/`)

```sh
npm ci
node scripts/build-directory.mjs                     # → public/symbols/directory.json (network)
node scripts/build-valuation.mjs [--market=us|tw]    # → public/valuation/{us,tw}.json (network)
#   Env: SEC_USER_AGENT, ATLAS_VALUATION_OUT_DIR, ATLAS_DIRECTORY_OUT, ATLAS_TODAY (YYYY-MM-DD)
# Offline, from the unit-test fixtures:
node scripts/build-directory.mjs --fixtures=tests/fixtures/directory
ATLAS_TODAY=2026-10-08 node scripts/build-valuation.mjs --fixtures=tests/fixtures/valuation
VITE_PUBLIC_BASE=/my-stock-screener/atlas/ \
VITE_MARKET_PROXY_URL=https://bxhqpfeberqbtxymghyt.supabase.co/functions/v1/market-chart \
  npm run build:pages                                # → dist-pages/
```

Both data scripts keep the previous output when a source fails (sanity minimums per market) and
print `::warning::` lines. The old per-symbol tools (`scripts/screener-symbols.mjs`,
`scripts/refresh-market.mjs` → `public/market-data/`, `SnapshotProvider`) still work locally but are no
longer run by the workflow.

Checks: `npm test`, `npm run lint`, `npx tsc -b`.

## Workflows

- `.github/workflows/atlas.yml` (concurrency `atlas-research-terminal`): after a successful
  "Daily Stock Screener", on manual dispatch, or on pushes to `main` touching `atlas/**`. Tests → seeds
  `directory.json` / `valuation/` from the deployed `docs/atlas/` → builds directory and valuation
  (`continue-on-error`) → `build:pages` with `VITE_MARKET_PROXY_URL` → replaces only `docs/atlas/` →
  commits and pushes with `git pull --rebase --autostash` and 3 retries. Generated data lives only in
  git-ignored paths, and any tracked file the build touched is restored before pulling (the earlier
  failure: `screener-symbols.mjs` rewrote the tracked `scripts/market-symbols.json`, blocking the rebase).
- `.github/workflows/supabase-functions.yml`: deploys `market-chart` on pushes touching
  `supabase/functions/market-chart/**` or on dispatch; skipped with a notice when
  `SUPABASE_ACCESS_TOKEN` is missing.

## Required setup (repository owner)

1. Deploy the Edge Function once: add the repository secret **`SUPABASE_ACCESS_TOKEN`** (Supabase →
   Account → Access Tokens) and run "Deploy Supabase Edge Functions", or run locally
   `supabase functions deploy market-chart --no-verify-jwt --project-ref bxhqpfeberqbtxymghyt`.
2. Optional repository variables: `MARKET_PROXY_URL` (function URL; default
   `https://bxhqpfeberqbtxymghyt.supabase.co/functions/v1/market-chart`), `SUPABASE_PROJECT_REF`
   (default `bxhqpfeberqbtxymghyt`), `SEC_USER_AGENT` (contact string SEC asks for).
3. Run "Atlas Research Terminal" manually once to publish the directory and valuation files.
