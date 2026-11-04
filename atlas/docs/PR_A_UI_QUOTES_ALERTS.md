# PR-A：報價與圖表介面

分支自 main `b0ca518232` 開始；每項獨立提交。既有登入／帳號、Supabase 函式／SQL、GCP 監控與永久行情資料均不修改。不執行正式排程、部署或通知。

## A1 清單更新時間

`board-live-time` 放回清單工具列右側，同列顯示；名單日期與來源留在頁尾。
時間只訂閱既有 LiveQuoteFeed 的批次接收事件，沒有新增報價輪詢。

驗證：新增單元測試 1 項、瀏覽器測試 5 項全通過（桌機、390px Chromium／WebKit、375px Chromium／WebKit）。延遲第一批報價以比較前後工具列高度；驗證高度不變、清單可視區 ≥80%、無水平溢出，快轉一分鐘後時間改變。

畫面：[桌機](screenshots/pr-a/a1-desktop.png)、[iPhone 390px](screenshots/pr-a/a1-iphone390.png)、[iPhone 375px](screenshots/pr-a/a1-iphone375.png)。瀏覽器模擬不代表實機驗收。

375px 測試另外執行 `npx playwright test --config e2e-site/playwright.compact.config.ts`，CI 平行執行，原有三種裝置的完整回歸不縮減。

## A2 正常交易報價狀態

標頭、K 線來源列、清單工具列共用格式：盤中「延遲報價 · 更新於 HH:MM:SS」，其餘時間「已收盤 · 最後成交 MM/DD HH:MM」（台灣時間）。成交時間來自正常交易 quote，盤前盤後不參與；未提供時間顯示 `—`。保留 Demo 模擬標示。最後成交時間不會因重新抓取而變成抓取時間；K 棒數值未改變也會更新來源列的接收時間。

單元測試涵蓋台美時段、收盤後補抓寬限、週末、夏令時間及無效時間；19 項相關單元測試、30 項瀏覽器回歸（五種尺寸／引擎）與 typecheck 通過。休市日規則在獨立 PR-D 補入。
畫面：[桌機](screenshots/pr-a/a2-desktop.png)、[iPhone](screenshots/pr-a/a2-iphone390.png)。
