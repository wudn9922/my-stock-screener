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

## A3 直接顯示盤前盤後價格

美股的延長時段價格直接列在主價格／狀態下方；點此行在標頭下方展開來源，內容參與版面排版，不浮在 K 線上。本益比列不再放盤前盤後按鈕。自選美股在漲跌下方顯示延長時段價，列高最多 56px；沒有有效成交時不留空白，缺少原因仍可在「行情資料說明」查閱。

讀取 `report/extended-quotes/{symbol}.json`，與既有 LiveQuoteFeed 帶回的 pre/post meta 比較成交時間；較新的有效值優先。靜態快照由同頁共用、有數量上限的快取讀取，沒有新增輪詢。延長時段成交若早於正常報價，就不以較晚價格計算漲跌幅；跨日會標出紐約日期。

2026-11-04 唯讀查詢 Nasdaq 實際回應（沒有執行正式排程，也沒有寫入既有行情檔）：

| 月份 | filed.rows | upcoming.upcomingTable.rows | priced.rows |
| --- | ---: | ---: | ---: |
| 2026-11 | 0 | 0 | 1 |
| 2026-10（結構交叉檢查） | 29 | 0 | 17 |

本月的 `priced` 是 NARA；舊程式只拿 priced 更新已存在的公司，導致它沒有加入輸出。現在三個區塊都能新增紀錄，定價日仍不充當掛牌日。三類都沒有紀錄時才顯示「本月行事曆暫無資料」；連線失敗另保留原有原因。回應欄位的假資料 fixture 位於 `tests/fixtures/market-extras/`。

7 項 Python、17 項相關前端單元測試、typecheck 通過。50 項相關瀏覽器回歸通過，另以 77.6／41.4 的實際顯示長度重跑 20 項：桌機、390px／375px Chromium、390px／375px iPhone WebKit 均通過。逐一檢查本益比、TTM、說明、收藏、更新及資訊按鈕邊界互不相交；驗證來源展開不覆蓋圖表／分頁，無資料不留高度。

畫面：[桌機](screenshots/pr-a/a3-desktop.png)、[390px](screenshots/pr-a/a3-iphone390.png)、[375px](screenshots/pr-a/a3-iphone375.png)、[展開來源](screenshots/pr-a/a3-expanded-desktop.png)、[自選桌機](screenshots/pr-a/a3-board-desktop.png)、[自選 375px](screenshots/pr-a/a3-board-iphone375.png)。

## A4 舊資料匯入僅在「我的」頁尾

空裝置不渲染卡片或「沒有可匯入」提示。有舊資料時只在「我的」頁尾提供原有匯入選項；個股、自選等頁面均不掛載卡片。原有明確同意、衝突保留、帳號隔離與雲端同步流程不修改。

23 項相關單元測試、45 項桌機／390px／375px Chromium 與 WebKit 回歸通過，保留原有重複匯入、雲端專有畫線、訪客／未授權不讀取舊資料的斷言。畫面：[桌機](screenshots/pr-a/a4-desktop.png)、[390px](screenshots/pr-a/a4-iphone390.png)、[375px](screenshots/pr-a/a4-iphone375.png)。
