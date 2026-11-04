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

## A5 以水平線交給現有 Discord 監控

在「價格提醒」建立價格條件後，按該提醒的「推播到 Discord」。按鈕使用 AppStore 的既有畫線提交／歷史記錄，於載入完成的股票與週期建立可見水平線（預設 2px，沿用自訂水平線樣式）；既有 DrawingSync 將它保存到同一份 `chart_drawings`。現有 GCP 依既有突破／跌破規則監控，這個 PR 不新增任何發送程式或通知排程。

必須具有 cloud 授權且初次畫線同步成功。訪客、none、device 或同步失敗時，按鈕停用並說明原因；點擊時再檢查目前授權、股票及週期。相同可見價格與週期的水平線會重用，不重複建立；既有鎖定與其他週期的畫線不被修改。

瀏覽器提醒的單向條件、停用與刪除僅影響瀏覽器提醒。Discord 監控依水平線執行兩個方向；要停止監控，須刪除或隱藏該線。面板直接標示兩種執行範圍，細節折疊收起。建立後仍可能遇到原有雲端同步錯誤，沿用既有錯誤／重試狀態；不把建立水平線宣稱為已發送通知。

5 項新增單元測試含既有 `atlas.save` 客戶端／DrawingSync 整合、canonical anchors、undo 與去重；21 項提醒／同步相關測試及完整 1,797 項單元測試通過。四種授權狀態 × 五種裝置／尺寸，共 20 項瀏覽器測試通過，檢查水平線同步 payload、重按不新增線、無跨網頁推播請求。全部為假服務與假資料，未寫入正式 Supabase 或發送 Discord 訊息。

畫面：[桌機](screenshots/pr-a/a5-desktop.png)、[390px](screenshots/pr-a/a5-iphone390.png)、[375px](screenshots/pr-a/a5-iphone375.png)。不需 SQL、函式或 GCP 部署。

## 最後回歸

- `npm ci`、lint、typecheck、完整 142 檔／1,797 項 Vitest、一般版與 Pages 建置通過。
- 受影響的站台回歸 141 項全部通過；原有資料來源、離線快取、價格提醒與畫線完整旅程 16 項全部通過（另含 iPad WebKit）。各項的 375px Chromium／WebKit 結果列於上方。
- 最後保留來源列原有延遲／快取等 metadata 文字後，再通過 lint、typecheck、23 項相關單元測試與兩種建置。未新增 skip 或調高 timeout。
- Workflow YAML、`git diff --check` 與檔案範圍檢查通過；受保護的登入／帳號原始碼、Supabase 函式／SQL、GCP 與永久行情／財報檔變更數為零。
- 以上為本機自我驗證；GitHub CI 與維護者的 Claude 審查另行確認，未宣稱實機 iPhone 驗收。
