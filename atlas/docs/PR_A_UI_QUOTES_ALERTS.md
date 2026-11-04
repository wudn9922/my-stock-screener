# PR-A：報價與圖表介面

分支自 main `b0ca518232` 開始；每項獨立提交。既有登入／帳號、Supabase 函式／SQL、GCP 監控與永久行情資料均不修改。不執行正式排程、部署或通知。

## A1 清單更新時間

`board-live-time` 放回清單工具列右側，同列顯示；名單日期與來源留在頁尾。
時間只訂閱既有 LiveQuoteFeed 的批次接收事件，沒有新增報價輪詢。

驗證：新增單元測試 1 項、瀏覽器測試 5 項全通過（桌機、390px Chromium／WebKit、375px Chromium／WebKit）。延遲第一批報價以比較前後工具列高度；驗證高度不變、清單可視區 ≥80%、無水平溢出，快轉一分鐘後時間改變。

畫面：[桌機](screenshots/pr-a/a1-desktop.png)、[iPhone 390px](screenshots/pr-a/a1-iphone390.png)、[iPhone 375px](screenshots/pr-a/a1-iphone375.png)。瀏覽器模擬不代表實機驗收。

375px 測試另外執行 `npx playwright test --config e2e-site/playwright.compact.config.ts`，CI 平行執行，原有三種裝置的完整回歸不縮減。
