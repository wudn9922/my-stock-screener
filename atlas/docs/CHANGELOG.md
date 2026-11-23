# Atlas 更新紀錄

以下段落依更新時間由新到舊收錄，保留原文與連結寫法。舊連結可從搬移前的 README 開啟。[搬移前的 README](https://github.com/wudn9922/my-stock-screener/blob/620cfb39bd0bbd64c23e85b41becc9f3d415d0f7/atlas/README.md)

個股圖表的星號改為「加入清單」：一次勾選圖表收藏、自選清單與分組；「我的 › 清單」合併原自訂分組與自選清單，分組成員的均線仍可修改。既有資料原地保留、不搬移。

清單更新時間顯示在清單工具列右側；名單日與來源說明仍在頁尾。[PR-A 操作與驗證](docs/PR_A_UI_QUOTES_ALERTS.md)。

歡迎畫面取代啟動黑屏，預設進入自選；清單採代號大字的緊湊版面，說明移至頁尾。個股新增同期量比與盤前盤後入口，搜尋支援 ANT 等 IPO 申報資料。來源、有限覆蓋與操作見 [使用說明](docs/STARTUP_WATCHLIST_MARKET_DETAILS.md)。

各週期可獨立自訂／固定均線與畫線；美股市場寬度加入附件規則的歷史上下限與指數訊號，自選清單新增可展開的進出榜條件證據。詳見 [操作、資料來源與限制](docs/TIMEFRAME_BREADTH_WATCHLIST_REASONS.md)。

美股熱力圖中文化；匯率／原物料新增預設清單與完整圖表工具，個股 K 線下方新增本益比走勢；儲存提示統一移至資料健康上方，選股清單加高。詳見 [使用方式、來源與保存](docs/MARKET_LISTS_AND_PE.md)。

市場寬度改用全市場日 K；選股新增連續看圖，個股的建議均線／波動率整合到同一列，研究頂部顯示全市場 RS，題材 K 線新增成交量。詳見 [市場範圍與看圖操作](docs/MARKET_COVERAGE_CHARTS.md)。

全球五個分頁縮短為單列；日美債共用「摘要／合理區間／利差」順序。選股切換群組或重新進入時自動清空暫時條件；畫線編輯不再直接聚焦顏色，儲存後即關閉。詳見 [本次操作修正](docs/BOND_FILTER_EDITOR_FOLLOWUPS.md)。

市場頂部分類整併為「大盤／全球／題材」，保留各國市場；全球新增日債利差，美日 10 年債加入附件算法的合理區間。新畫線預設 2px，點線可在圖內鎖定、刪除或修改。詳見 [來源、公式與驗證](docs/MARKET_BONDS_DRAWING_CONTROLS.md)。

已保存的股票均線現在與分組／監控採用相同來源，原分組參數優先；編輯原筆可明確合併舊無分組重複筆。自選連續看圖以六個月開啟，帶入已保存或清單均線，並移除自選上方指數列。詳見 [股票均線一致性](docs/STOCK_PARAMETER_CONSISTENCY.md)。

自選分組可直接在行情列表上方切換，並可按「連續看全部股票圖表」往下看日 K 與成交量。
切換股票時價格軸回到該股票的資料範圍；財務與其他個股頁共用四個選項的排版。
細節與驗證見 [圖表與自選操作修正](docs/CHART_WATCHLIST_FOLLOWUPS.md)。

自選的「LINE 熱門」旁可切換「突破／跌破」，以組別詳細連續捲動查看已成功推送到
Discord 的股票日 K、成交量與觸發均線；獨立唯讀服務需維護者手動啟用。
資料範圍、授權與操作見 [監控訊號說明](../docs/ATLAS_MONITOR_SIGNALS.md)。

MEXC read-only API synchronization follows in PR B after #199, with server-encrypted
credentials, cost review and visible-page polling. See [its manual setup runbook](../docs/ATLAS_MEXC_SYNC.md)
before enabling the website key-entry form; no deployment or SQL application is automatic.

The screener site's 自選 page now offers 清單 / 持股. Holdings use browser-only CSV/XLSX
or pasted-table import, editable previews, account snapshot replacement and dated
market prices for P&L. Guest data is RAM-only; device and cloud follow existing
account authorization. See [the holdings runbook](../docs/ATLAS_HOLDINGS.md) for
format limits and the maintainer-only SQL → user-settings → frontend rollout.

Atlas screener can be installed for free from Safari/Chrome as a home-screen
Web App: open https://wudn9922.github.io/my-stock-screener/atlas/ and use
**顯示設定 → 安裝 Atlas**, or Safari **分享 → 加入主畫面**. No store,
paid wrapper, Apple membership or weekly re-signing is required. The online-first
PWA follows the existing GitHub Pages deployment and account storage grants.
See the [installation, login and updates guide](docs/PWA_SELF_USE.md).

The `/my-stock-screener/atlas/` deployment uses account-authorized storage; the older `lightweight-drawing-lab` release notes below predate that policy. See [account storage and rollout](../docs/ACCOUNT_STORAGE_PERMISSIONS.md) and [ADR-016](docs/architecture/ADR-016-account-storage-authorization.md).

## Current my-stock-screener deployment and storage

This checkout publishes Atlas at <https://wudn9922.github.io/my-stock-screener/atlas/>; the repository-level README describes its `Atlas Research Terminal` workflow and data flow. At this path, storage is authorized per verified LINE account: `none` uses RAM, `device` keeps account-scoped browser data, and `cloud` also uses the existing Supabase sync. Authorized device/cloud users can explicitly import the untouched legacy browser settings from the console. See [account storage and rollout](../docs/ACCOUNT_STORAGE_PERMISSIONS.md).

The `/lightweight-drawing-lab/` URL, commit IDs, Actions runs, and public verification results in the archive below belong to an earlier deployment in a different repository; they do not verify the current `/my-stock-screener/atlas/` deployment.

## Historical lightweight-drawing-lab release records (archive)

Earlier V1 and Pages verification results are historical. The SEC snapshot, Taiwan market, and price-axis extension was deployed and passed public HTTPS and iPhone WebKit verification at this archived checkpoint. Commit [3feb6337aeab4e686c0f61e259bece7250be3d40](https://github.com/wudn9922/lightweight-drawing-lab/commit/3feb6337aeab4e686c0f61e259bece7250be3d40) completed [Actions run 37785146220](https://github.com/wudn9922/lightweight-drawing-lab/actions/runs/37785146220) successfully at 2026-10-08T13:36:11Z. All 16 seeded SEC packs were served and validated; NVO, QQQ, SPY, and TSM remained unavailable after SEC 403/cooldown. Physical-device UAT and independent Gemini/Claude review were pending at that checkpoint.

### Previous GitHub Pages deployment: `/lightweight-drawing-lab/`

The same Atlas source supports the project path `/lightweight-drawing-lab/`. The current deployed source is commit `3feb6337aeab4e686c0f61e259bece7250be3d40`; Actions run 37785146220 completed deployment at 2026-10-08T13:36:11Z. **Open [Atlas](https://wudn9922.github.io/lightweight-drawing-lab/) in your browser** for the deployed app. The Pages build is separate from the normal local build:

```sh
npm ci
npm run build:pages
npm run test:pages
```

`dist-pages/` contains only static files. `.github/workflows/pages.yml` builds and deploys it after a commit to `main`; the repository is already configured with **Settings → Pages → Source → GitHub Actions**. The verified URL is `https://wudn9922.github.io/lightweight-drawing-lab/`. To preview locally, run `VITE_STATIC_HOSTING=1 VITE_PUBLIC_BASE=/lightweight-drawing-lab/ npx vite preview --outDir dist-pages --port 4175` and open that project path. `npm run test:pages` starts its own isolated preview, so stop a manual server on 4175 before running the tests.

GitHub Pages cannot run the existing live Node Yahoo/SEC proxies. The deployed build serves delayed Yahoo market snapshots (including SMCI/NFLX); live Yahoo requests still require a backend. The earlier run 37764054644 received SEC company-ticker HTTP 403 and cooldown, so all 20 symbols were unavailable and AAPL returned 404; the 16 versioned seed packs resolved that missing-pack condition in the current release. TLS-verified public checks at 2026-10-08T13:38:04.608649+00:00 confirmed all assets byte-equal to the deployed build, the font license, and all 16 SEC packs with valid schema, audit lineage, source, nullable fields, and original 09:19 fetched timestamps. The public iPhone WebKit smoke passed at 2026-10-08T13:37:56.193Z with zero API requests or runtime errors. Four unseeded SEC symbols—NVO, QQQ, SPY, and TSM—remain unavailable after SEC 403/cooldown; no fresh runner data is claimed for them. Future refresh is best effort, and SEC packs are marked stale after 24 hours. The HTTP record reports partial Taiwan coverage because `0050.TW` lacks 1W/1M. The latest hosted 26-symbol market refresh in Actions run 37785146220 reports 20 available, 2 partial (`0050.TW` lacks 1W/1M; AMZN 1M was omitted after source quote agreement failed), and 4 retained-stale (`NVO`, `XOM`, `SPY`, `TSM`). Retained market packs preserve and display their original observation dates and are not presented as current quotes. XOM's retained market quote state is separate from its SEC seed, which has two quarterly facts and zero annual facts. WebKit verified TWD and native Taiwan weekly/monthly values; AAPL quarterly and annual Revenue audit UI; persistence/reload/export v4 for three timeframe-owned locked SMAs and a future horizontal drawing. Pack raw/derived provenance, including FCF lineage, passed HTTP validation; WebKit did not assert the FCF UI. Root inspected public chart and financial-panel screenshots and found axis labels including 2550/2600 and date labels coherent. See the [live HTTP evidence](docs/taiwan-financial-live-http.json) and [live browser evidence](docs/taiwan-financial-live-browser.json). Demo, drawings, indicators, Watchlist, local persistence, research backtests, foreground alerts and settings backup remain available on Pages. Yahoo backend-unavailable behavior does not silently switch providers or use fabricated prices. The normal `npm run dev` / `npm run build` / `npm run preview` paths retain their existing provider behavior.

The manifest, icons, assets, worker scope and offline caches follow the project path. Cache cleanup is confined to this registration scope; other GitHub Pages apps' caches are preserved. PWA installation requires HTTPS and a first online load. IndexedDB belongs to the hosting **origin**, not the path: another Atlas app on the same `username.github.io` origin shares its database; local Cloud settings are not transferred automatically. Export locally and Import on Pages to transfer them. See [hosting ADR](docs/architecture/ADR-010-static-hosting.md) and [hosting verification](docs/PAGES_VERIFICATION.md).

### Archived extension checkpoint: SEC snapshots, Taiwan symbols, and price axis

The current extension source commit [3feb6337aeab4e686c0f61e259bece7250be3d40](https://github.com/wudn9922/lightweight-drawing-lab/commit/3feb6337aeab4e686c0f61e259bece7250be3d40) was published non-force with 269 reviewed paths. [Actions run 37785146220](https://github.com/wudn9922/lightweight-drawing-lab/actions/runs/37785146220) deployed successfully at 2026-10-08T13:36:11Z. The preceding run 37764054644 had SEC company-ticker HTTP 403/cooldown and AAPL 404; the current deployment serves 16 validated seeded packs. TLS-verified HTTPS at 2026-10-08T13:38:04.608649+00:00 confirmed all deployed assets byte-equal, the font license, and all 16 SEC packs' schema, audit, source, nulls, and original 09:19 timestamps. The HTTP report is `PASS_WITH_REPORTED_PARTIAL_TAIWAN_COVERAGE`: `0050.TW` still lacks 1W/1M. NVO, QQQ, SPY, and TSM remain unavailable after runner SEC 403/cooldown; no fresh runner facts are claimed for them. The public iPhone WebKit smoke passed at 2026-10-08T13:37:56.193Z with zero API requests and runtime errors. It verified TWD and native weekly/monthly Taiwan values, AAPL quarterly and annual Revenue audit UI, and persistence/reload/export v4 for three timeframe-owned locked SMAs and a future horizontal drawing. Pack raw/derived provenance, including FCF lineage, passed HTTP validation; public WebKit did not assert the FCF UI. Root inspected the public chart and financial-panel screenshots; axis values including 2550 and 2600 and the date labels appeared coherent. See [HTTP evidence](docs/taiwan-financial-live-http.json) and [WebKit evidence](docs/taiwan-financial-live-browser.json).

The collector reuses `FinancialNormalizer` without changing quarter derivation, EPS handling, nullable metrics, provenance, or audit lineage. Snapshot schema v1 is separate from the version-4 user settings/export format. Generated per-symbol output and the collector manifest live in `public/financial-data/` and remain Git-ignored. The 16 normalized public SEC packs in `data/financial-snapshots/` are intentionally versioned bootstrap facts: AAPL, MSFT, NVDA, TSLA, AMD, META, GOOGL, AMZN, SMCI, NFLX, JPM, WMT, XOM, AVGO, PLTR, and MU. Four allowlist entries remain unavailable. XOM uses current CIK `0002115436` and has two quarterly facts and zero annual facts in the seed; the pack does not claim annual coverage.

The collector validates seed packs and uses one only when the matching cache entry is missing or invalid, or when the seed's `fetchedAt` is strictly newer. It preserves the original `fetchedAt`, `generatedAt`, null values, and audit metadata, and never downgrades a newer valid cache. When the 24-hour TTL expires, the cache is marked stale and refresh is attempted; a failed refresh retains the last valid pack. A seed-write failure is isolated per issuer, and later issuers continue; the `EISDIR` plus SEC-403 continuation case has been exercised. A local mocked-403 run also confirmed 16 seed-backed symbols remain `fresh`, the four unseeded symbols (`NVO`, `QQQ`, `SPY`, `TSM`) remain `unavailable`, only four SEC calls are made, and seed timestamps compare deeply equal after collection. This is local mocked-response evidence, not hosted SEC access. The collector does not rotate User-Agents or IPs and does not use a proxy workaround. The fixed SEC client continues to identify the project truthfully; no metrics are synthesized.

Pages loads validated same-origin packs through `FinancialSnapshotProvider`; its disposable IndexedDB cache is bounded to 32 symbols and reports stale/offline state. The SEC collector filters the mixed market-symbol list to the 20 US-profile symbols, excluding Taiwan from SEC requests and the SEC manifest. Issuers or ETFs without supported SEC companyfacts remain `unsupported`; per-symbol outcomes remain `fresh`, `retained`, `unavailable`, or `unsupported`. Live SEC API use remains behind the existing backend.

Taiwan coverage keeps the existing 20 US symbols and adds delayed market snapshots for `2330.TW`, `0050.TW`, `2317.TW`, `2454.TW`, `6488.TWO`, and `8069.TWO`. The official TWSE/TPEx symbol directory has 2,251 entries. The latest hosted 26-symbol market refresh reports 20 available, 2 partial (`0050.TW` lacks 1W/1M; AMZN 1M was omitted after source quote agreement failed), and 4 retained-stale (`NVO`, `XOM`, `SPY`, `TSM`). Retained packs preserve and display their original observation dates and do not imply a current quote. The older local snapshot check had reported 25 available and `0050.TW` partial. XOM's retained market state is separate from its SEC seed, which has two quarterly facts and zero annual facts. Five selected Taiwan symbols have all seven timeframes. A bare Taiwan ticker resolves only when the directory identifies one listing; enter `.TW` or `.TWO` when a code is ambiguous. Taiwan uses TWD, `Asia/Taipei`, and a 09:00–13:30 session; no holiday calendar, Taiwan SEC facts, or TSM ADR alias is fabricated. See [ADR-013](docs/architecture/ADR-013-static-fundamentals-and-taiwan.md) and the [Taiwan/SEC snapshot verification checkpoint](docs/TAIWAN_FINANCIAL_SNAPSHOT_VERIFICATION.md).

Yahoo may provide an actual Taiwan terminal-auction sample exactly at 13:30. When validated, it is marked as a `sessionCloseObservations` entry, a flat-OHLC/zero-volume `CLOSED INSTANT` sample, not an ordinary fabricated interval. Research includes it only when the mark is valid and `asOf` is at or after its timestamp. Taiwan intraday bar counts may include this close observation; US calendars and counts are unchanged.

The axis design uses `Atlas Narrow Axis`, a 70%-horizontal-scale Barlow Condensed Regular derivative under SIL Open Font License 1.1 ([license](src/assets/fonts/OFL.txt)). It targets minimum width zero, 11px labels, `minMove: 0.01`, and redundant trailing-zero trimming only. The corrected font scales glyph advances and outlines (SHA-256 `5e465e809833ef0fa73c5a65827e921c0e02aba1facc263d606838c1bd126d1f`); Sol independently passed all 694 glyph geometries. The corrected integrated axis check passed all four profiles, and fresh read-only measurement showed at least 30.8% width reduction for ordinary samples. The earlier overlapping-font measurements are superseded. The font also affects chart time-axis dates; company UI fonts remain unchanged. Startup preloads the font and waits up to five seconds before falling back to the system font, where the width reduction is not guaranteed.

Latest gates: 225 unit tests across 29 files; lint, typecheck, normal/Pages builds; targeted collector/provider unit suite 18; normal browser 126 passed / 10 original skips in 12.8 minutes; Pages browser 32 passed / 0 failed in 3.3 minutes. Combined browser result: 158 passed / 10 original expected skips. Corrected axis measurement remains at least 30.8% width reduction for ordinary samples across four profiles; all 694 glyph geometries passed. Bootstrap execution used 16 seed-backed `fresh` SEC packs and left NVO/QQQ/SPY/TSM unavailable after SEC 403; no fresh runner facts are claimed for those four. The latest hosted 26-symbol market refresh reports 20 available, 2 partial (`0050.TW` lacks 1W/1M; AMZN 1M was omitted after source quote agreement failed), and 4 retained-stale (`NVO`, `XOM`, `SPY`, `TSM`); retained packs preserve their observation dates and are not current-quote claims. The earlier local market check had reported 25 available and `0050.TW` partial. The official Taiwan directory has 2,251 entries. BOOT-01/02 are accepted and closed. Extension engineering, deployment, and public proof are complete; physical-device UAT and independent Gemini/Claude review remain pending. Next: open the existing public URL and try the financial and Taiwan flows. Reopen the site if cached, but do not clear site data. No V2 work is authorized.

### Archived checkpoint: Drawing picker and ATR strokes

The drawing side rail has been removed. A 44×44px **Drawing Tools** button with an icon and **繪圖** in the chart toolbar opens a categorized modal outside the plot. At widths up to 1099px, the duplicate delayed-snapshot CTA is hidden while the provider selector remains visible. Categories provide access to all ten existing drawing tools. The modal also exposes Select/Pan, magnet, undo/redo, zoom, future area, and reset view through their existing actions. Opening it cancels an unfinished gesture; choosing a tool closes it. The chart/controller stay mounted and the modal does not resize the chart. On mobile, category buttons and the selected category's tools use a safe-area-aware sheet with bounded scrolling.

New drawings and new SMA/EMA instances now default to **2 CSS pixels**, unless the user saved a custom default. The UI retains ATR mode (**0.02 × Wilder ATR(14)**, measured in price space and converted through the public price scale) and fixed 1–4 CSS-pixel mode. Insufficient, invalid, or flat OHLC data uses the stored pixel fallback. Completed ATR drawing strokes are rendered between 0.5 and 4 CSS pixels (and at least one raster pixel); native SMA/EMA widths round to 1–4 pixels. Preview strokes remain 1px and selected strokes at least 2.5px; edit handles are unchanged. These limits mean rendered width is not exactly 0.02 ATR at every zoom. Saved drawings, MAs, locks, and defaults retain their existing widths unless the user changes modes. Volume stays in pixel units and does not use ATR.

Historical deployment checkpoint: at that time, the public URL served commit `0e4317a108fb9e6acc4b688355d2c55ac7dbaaf1`; Actions run 37741318516 succeeded, and HTTPS asset/hash verification passed. The public iPhone WebKit smoke passed at 2026-10-08T07:09:15.433Z. The 178-test unit suite, lint/typecheck, both builds and 24 targeted browser cases passed. Focused mobile-uat and v1-journey coverage passed 20 cases across all four profiles. The final normal browser gate passed 122 cases with 10 original expected skips and no failures; the Pages gate passed all 20 cases. Combined: 142 passed / 10 original expected skips / 0 failed. Read the [HTTP evidence](docs/picker-atr-live-http.json) and [browser evidence](docs/picker-atr-live-browser.json). The picker, ATR stroke settings/defaults, and compact mobile chart controls are documented in [the picker and ATR verification record](docs/DRAWING_PICKER_ATR_VERIFICATION.md). Existing saved widths/settings were not forced to ATR; ATR could be selected in a drawing or SMA/EMA setting. Independent external review and physical-device UAT remained pending at this checkpoint.

### Archived checkpoint: Monthly/timeframe ownership and real prices (2026-10-07)

Select **Yahoo · 延遲快照** in Market data source to view actual Yahoo prices on Pages; **Demo is fictional, not current market data**. Snapshots are refreshed by the existing free public GitHub Actions workflow on commits/manual dispatch and best-effort hourly weekday schedules. They are not streaming or guaranteed current. Source, quote timestamp, retrieval timestamp and split-adjusted price basis remain visible; aged/offline data is marked. Yahoo already adjusts quote OHLC for splits: SMCI/NFLX are never divided a second time. Dividend-adjusted `adjclose` is not used. Native weekly/monthly history remains unchanged; only the current in-progress period is rebuilt from validated daily OHLCV and carries derivation provenance (`本週 K由日 K彙總` / `本月 K由日 K彙總`). A latest-session daily row is never added again to native aggregate volume.

Covered tickers: AAPL, MSFT, NVDA, TSLA, AMD, META, GOOGL, AMZN, NVO, SMCI, NFLX, JPM, WMT, XOM, SPY, QQQ, AVGO, PLTR, MU, TSM. Unsupported tickers or failed intervals show unavailable rather than invented prices. Maintain `scripts/market-symbols.json` to extend the public allowlist. To collect locally before building Pages:

```sh
node scripts/refresh-market.mjs
npm run build:pages
```

Generated `public/market-data/` files are not committed; the workflow collects and publishes them, retaining previous successful packs via its cache when daily refresh fails. A failed interval is omitted, and a pack never mixes new adjustment data with old intervals. No key/account/card is required. This is an unofficial prototype; production availability and redistribution rights are not guaranteed.

**1M is calendar-month data**, including future whitespace and conservative monthly closed-bar research. MA/EMA/Volume, drawings, presets, alerts and undo history are independently owned by symbol + timeframe. Version3 migration preserves every ID/anchor/style/lock: old global MAs go to the last preferred timeframe (their creation timeframe was never stored), and drawings go to the first anchor's timeframe. The panel explains the inferred MA home; all buckets remain in Export. Version4 adds optional ATR width intent while retaining pixel fallbacks and does not rewrite saved widths. JSON1/2/3 imports are validated then atomically upgraded; invalid imports leave current settings intact. Do not clear browser site data. See [ADR-011](docs/architecture/ADR-011-timeframe-ownership-and-snapshot-prices.md), [ADR-012](docs/architecture/ADR-012-drawing-picker-and-atr-strokes.md), and [timeframe verification](docs/TIMEFRAME_PRICE_VERIFICATION.md).
