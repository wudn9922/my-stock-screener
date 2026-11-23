# Atlas Research Terminal — V1

Atlas 是整合在每日股票報告中的網頁研究終端，提供台美股行情與選股、技術圖表與畫線、自選與持股、財報與本益比河流，以及回測與警示。可在瀏覽器使用，也能安裝為 PWA。[開啟 Atlas](https://wudn9922.github.io/my-stock-screener/atlas/)

## 本機開發與測試

在 `atlas/` 使用 Node.js 22.12 以上版本。啟動後開啟 <http://localhost:5173>。

```sh
npm ci
npm run dev
```

```sh
npm run lint
npm run build
npm test -- tests/financial-layout.test.ts
npx playwright install --with-deps chromium webkit
npm run test:site -- financials.spec.ts --project=mobile-chromium --grep "statement"
```

## 文件索引

- [更新紀錄](docs/CHANGELOG.md)
- 使用與部署：[安裝與登入](docs/PWA_SELF_USE.md)、[GitHub Pages](docs/PAGES_VERIFICATION.md)、[控制台整合](docs/UNIFIED_CONSOLE.md)、[帳號儲存權限](../docs/ACCOUNT_STORAGE_PERMISSIONS.md)
- 圖表與自選：[圖表與自選操作](docs/CHART_WATCHLIST_FOLLOWUPS.md)、[啟動畫面與自選](docs/STARTUP_WATCHLIST_MARKET_DETAILS.md)、[股票均線一致性](docs/STOCK_PARAMETER_CONSISTENCY.md)、[畫線與 ATR](docs/DRAWING_PICKER_ATR_VERIFICATION.md)、[報價與警示](docs/PR_A_UI_QUOTES_ALERTS.md)、[監控訊號](../docs/ATLAS_MONITOR_SIGNALS.md)
- 市場與選股：[市場涵蓋](docs/MARKET_COVERAGE_CHARTS.md)、[市場清單與本益比](docs/MARKET_LISTS_AND_PE.md)、[市場寬度與進出榜原因](docs/TIMEFRAME_BREADTH_WATCHLIST_REASONS.md)、[市場警示](docs/MARKET_WARNINGS.md)、[市場日曆](docs/PR_D_MARKET_CALENDAR.md)、[債券與篩選](docs/BOND_FILTER_EDITOR_FOLLOWUPS.md)、[市場與債券](docs/MARKET_BONDS_DRAWING_CONTROLS.md)、[本益比篩選](docs/SCREENER_PE_FILTER.md)
- 財報：[資料狀態與同業比較](docs/DATA_STATUS_AND_COMPARISONS.md)、[SEC 驗證](docs/SEC_V1_VALIDATION.md)、[台股驗證](docs/TAIWAN_FINANCIAL_SNAPSHOT_VERIFICATION.md)、[美股歷史回補](docs/US_FINANCIAL_HISTORY_BACKFILL.md)、[美股全市場歷史回補](docs/US_FINANCIAL_MARKET_HISTORY_BACKFILL.md)
- 持股：[持股匯入](../docs/ATLAS_HOLDINGS.md)、[持股計算](docs/HOLDINGS_ACCOUNT_METHODS.md)、[MEXC 唯讀同步](../docs/ATLAS_MEXC_SYNC.md)
- 開發：[架構決策（ADR）](docs/architecture/)、[Phase 1 驗證](docs/VERIFICATION.md)、[Phase 2 驗證](docs/PHASE2_VERIFICATION.md)、[V1 驗證](docs/V1_VERIFICATION.md)、[完整文件目錄](docs/)
