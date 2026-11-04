# PR-C：封存優先，Yahoo 補最新

本 PR 從 main `c3668287ae` 開分支，C1～C5 各一個 commit；沒有合併、部署、執行正式排程或寫入永久資料。PR-A 的介面修正與 PR-B 的補檔另行審查；本讀取器仍相容現有 manifest v1，未補到的 ETF／新上市代號繼續用原資料來源。

## 行為與限制

| 場景 | 行為 |
| --- | --- |
| 日 K 有封存 | 先顯示封存；批次 LiveQuoteFeed 補今天／缺少日期 |
| 週／月 K | 原 calendarPeriodStart 日 K 聚合；更早歷史背景補 Yahoo，失敗保留封存 |
| quote 502／逾時／驗證失敗 | K 線保留，顯示「即時報價暫時無法取得，顯示至 YYYY-MM-DD 收盤」，可重試 |
| 分 K 失敗 | 原因與「改看日 K」按鈕，不顯示空白 |
| 重疊價格差 > 2% | 整段改讀 Yahoo，來源註明，失敗保留較短的 Yahoo 片段，不混用調整基準 |
| 無封存／清單讀檔失敗 | 原 Yahoo／官方序列路由，保留原有錯誤處理 |
| 自選小走勢 | 六個月、最多 126 根、最多兩年檔；日漲跌比較最後兩根 |
| 個股側邊清單 | 可見列共用一個批次 feed，盤中每分鐘更新，離開取消 |
| 連續看圖 | 可見圖每五分鐘更新；同代號有一分鐘訂閱時取較短間隔；捲回重用暖快取 |

長均線仍需要六個月以前的 warm-up，這種小圖會保留額外封存歷史；市場小圖原有長均線與縮放歷史也保留。六個月是連續檢視的預設顯示範圍。原有盤中分 K 流程保留，沒有增加輪詢迴圈。

封存請求固定指向 `raw.githubusercontent.com/wudn9922/my-stock-screener/kline-archive/`；只接受 manifest 成員與預期路徑。v2 SHA 驗證、年度／日期排序、OHLC、quarantine 與未知成交量均有檢查。年檔存入原有 account-scoped 行情 IndexedDB；未授權持久化時沿用記憶體，不修改登入或儲存授權程式。台股張／美股股仍由原顯示函式處理。

PR-B 另封存的全球指數保留 global/，本次 tw/us 讀取不把其他幣別誤標為美股；世界資產仍走既有路由。五年覆蓋由 [PR-B 操作說明](https://github.com/wudn9922/my-stock-screener/blob/4636c2514f5289c5af992e3855d7997e7ca2b528/docs/KLINE_ARCHIVE_EXPANSION.md) 的維護者補檔決定，本 PR 沒有執行正式補檔。

日 K 的日期轉換保留既有畫線時間：股票與 Yahoo 指數使用市場開盤時間（含美國夏令時間），唯一原有官方序列 ^TWOII 保留 UTC 日期時間。已核對部署的 series/index.json 只有 ^TWOII，且增加美股冬／夏令、上市／上櫃、官方指數的舊畫線定位測試。正規化版本有獨立快取 key。

## 測試

新增單元涵蓋 v1/v2、SHA、未知量、排序／壞日期、IDB 跨實例與跨日更新、同頁合併請求、缺年檔、兩種 timestamp 的同交易日、分割失配與完整 Yahoo 替換、週／月失敗保留、背景事件、P/E 較早歷史、形成中 K 棒持續更新、可見列清理、五分鐘間隔／短訂閱優先／捲回快取，以及真正懸住請求的 timeout。

新瀏覽器案例：日／週／月 502、逾時、無效資料；分 K 回日 K；47 檔清單零五年請求、126 點與日漲跌不變；缺檔與網路退路；IPO 只讀實際年度；側邊台美混合批次與分鐘更新；小圖可見訂閱與五分鐘更新。桌機、390px Chromium／iPhone WebKit、375px Chromium／iPhone WebKit 都測。

既有 nav-inventory、workspace-refinements、chart-watchlist-followups、market-coverage-charts、market-chart-controls、watchlist-board、live-quotes、pe-river、data-integrity、closed-market 及原繪圖實驗室 provider／cache／提醒／離線旅程均保留門檻。CI 保留原全部命令，另加新案例與 375px job。實際結果與計數列在 PR 說明；沒有宣稱實機 iPhone 或獨立審查通過。

## 額外舊離線相容性

未改前端的 PR-B 基準與本 PR 用獨立新瀏覽器重現：shell 只快取 11 個直接資源，沒有 ChartPage；離線重開兩者皆失敗，缺少圖表 JS/CSS。現在建置時列出公開 chunk 供原非選股站點 worker 安裝，原離線斷網／關伺服器測試仍照舊執行。選股站點既有記憶體／worker 退役策略不變。沒有快取帳戶 API 或交接資訊，也沒有修改登入／帳號、Supabase、GCP 或永久行情。

## 截圖

全部是假的行情與帳號 fixture。每項有桌機、390px Chromium／WebKit、375px Chromium／WebKit，放在 [screenshots/pr-c](screenshots/pr-c)。

| 案例 | 桌機 | iPhone 375px |
| --- | --- | --- |
| 斷線保留封存（C1/C2） | [圖](screenshots/pr-c/c2-desktop-chromium.png) | [圖](screenshots/pr-c/c2-iphone-webkit-375.png) |
| 六個月自選（C3） | [圖](screenshots/pr-c/c3-desktop-chromium.png) | [圖](screenshots/pr-c/c3-iphone-webkit-375.png) |
| 批次側邊清單（C4） | [圖](screenshots/pr-c/c4-desktop-chromium.png) | [圖](screenshots/pr-c/c4-iphone-webkit-375.png) |
| 五分鐘小圖（C5） | [圖](screenshots/pr-c/c5-desktop-chromium.png) | [圖](screenshots/pr-c/c5-iphone-webkit-375.png) |
