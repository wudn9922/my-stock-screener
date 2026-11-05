# PR-C：封存優先，Yahoo 補最新

原 C1～C5 的分項 commit 保留；本輪用 merge 整合 main `b6ba9afd60`（包含已合併 #219、#224），沒有 rebase／force-push。保留報價狀態、盤前盤後直接顯示、自選更新時間與價格警示。讀取器直接讀 PR-B 的個股 index；尚未發布 index 的代號（HTTP 404）繼續用原資料來源，不下載全市場 manifest。沒有合併 PR、部署、執行正式排程或寫入永久資料。

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

封存請求固定指向 `raw.githubusercontent.com/wudn9922/my-stock-screener/kline-archive/`；只接受對應 symbol／bucket／路徑及有效年度明細。個股 index 的年度 SHA 驗證、年度／日期排序、OHLC、quarantine 與未知成交量均有檢查。年檔存入原有 account-scoped 行情 IndexedDB；未授權持久化時沿用記憶體，不修改登入或儲存授權程式。台股張／美股股仍由原顯示函式處理。

PR-B 另封存的全球指數保留 global/，本次 tw/us 讀取不把其他幣別誤標為美股；世界資產仍走既有路由。五年覆蓋由 [PR-B 操作說明](https://github.com/wudn9922/my-stock-screener/blob/2e5c02ab1943f5e334efe74bf981a034f0b9df05/docs/KLINE_ARCHIVE_EXPANSION.md) 的維護者補檔決定，本 PR 沒有執行正式補檔。

日 K 的日期轉換保留既有畫線時間：股票與 Yahoo 指數使用市場開盤時間（含美國夏令時間），唯一原有官方序列 ^TWOII 保留 UTC 日期時間。已核對部署的 series/index.json 只有 ^TWOII，且增加美股冬／夏令、上市／上櫃、官方指數的舊畫線定位測試。正規化版本有獨立快取 key。

## 測試

新增單元涵蓋個股 index、SHA、未知量、排序／壞日期、IDB 跨實例與跨日更新、同頁合併請求、缺年檔、兩種 timestamp 的同交易日、分割失配與完整 Yahoo 替換、週／月失敗保留、背景事件、P/E 較早歷史、形成中 K 棒持續更新、可見列清理、五分鐘間隔／短訂閱優先／捲回快取，以及真正懸住請求的 timeout。

新瀏覽器案例：日／週／月 502、逾時、無效資料；分 K 回日 K；47 檔清單零五年請求、126 點與日漲跌不變；缺檔與網路退路；IPO 只讀實際年度；側邊台美混合批次與分鐘更新；小圖可見訂閱與五分鐘更新。桌機、390px Chromium／iPhone WebKit、375px Chromium／iPhone WebKit 都測。

既有 nav-inventory、workspace-refinements、chart-watchlist-followups、market-coverage-charts、market-chart-controls、watchlist-board、live-quotes、pe-river、data-integrity、closed-market 及原繪圖實驗室 provider／cache／提醒／離線旅程均保留門檻。CI 保留原全部命令，另加新案例與 375px job。實際結果與計數列在 PR 說明；沒有宣稱實機 iPhone 或獨立審查通過。

## 額外舊離線相容性

未改前端的 PR-B 基準與本 PR 用獨立新瀏覽器重現：shell 只快取 11 個直接資源，沒有 ChartPage；離線重開兩者皆失敗，缺少圖表 JS/CSS。現在建置時列出公開 chunk 供原非選股站點 worker 安裝，原離線斷網／關伺服器測試仍照舊執行。選股站點既有記憶體／worker 退役策略不變。沒有快取帳戶 API 或交接資訊，也沒有修改登入／帳號、Supabase、GCP 或永久行情。

## 截圖

全部是假的行情與帳號 fixture，共 25 張。每項有桌機、390px Chromium／WebKit、375px Chromium／WebKit，放在 [screenshots/pr-c](screenshots/pr-c)。

| 案例 | 桌機 | iPhone 375px |
| --- | --- | --- |
| 斷線保留封存（C1/C2） | [圖](screenshots/pr-c/c2-desktop-chromium.png) | [圖](screenshots/pr-c/c2-iphone-webkit-375.png) |
| 六個月自選（C3） | [圖](screenshots/pr-c/c3-desktop-chromium.png) | [圖](screenshots/pr-c/c3-iphone-webkit-375.png) |
| 批次側邊清單（C4） | [圖](screenshots/pr-c/c4-desktop-chromium.png) | [圖](screenshots/pr-c/c4-iphone-webkit-375.png) |
| #224 補齊日 K | [圖](screenshots/pr-c/c3-fill-desktop-chromium.png) | [圖](screenshots/pr-c/c3-fill-iphone-webkit-375.png) |
| 五分鐘小圖（C5） | [圖](screenshots/pr-c/c5-desktop-chromium.png) | [圖](screenshots/pr-c/c5-iphone-webkit-375.png) |

## 審查修訂：個股索引與 #219 整合

已以 merge 合併 main b6ba9afd60，保留 #219 全部功能，沒有 rebase 或 force-push。讀取歷史以前僅請求 `index/{bucket}/{代號}.json`；不請求 manifest。PR-B 的 index 檔名是磁碟上的 URL 編碼字串，HTTP 要再編碼 `%`，例如 `^TWII` 為 `index/tw/%255ETWII.json`；CSV 則仍為 `tw/%5ETWII/2026.csv`。

index 與 404 都使用原有行情 IndexedDB／記憶體授權機制快取，每個 UTC 日最多更新一次；不另建帳戶儲存。不持久化授權時只保留記憶體。公開文件與原行情的淘汰額度分開，避免年度 K 線逐出當天已讀的 index。跨日會重新取得 index，舊的正向快取不會掩蓋次日的 404。身份、bucket、CSV path、年度日期範圍、SHA／rows／bytes 都驗證後才讀年度檔；index 不存在就回原 Yahoo 路由。

先由維護者讓 PR-B 的正常 archive publisher 產出新 index；本 PR 不會從瀏覽器產生或寫入封存資料。尚無 index 時功能沿用原來源。完整 CI 與新手機截圖於本輪修訂重跑。

#224 的 meta.atlasIntradayFill 形狀已核對，測試使用 ^TWII 2026-11-04／11-05 原為全 null、補齊後有值的完整回應。日 K 與封存以交易所交易日期比對；同日 timestamp 從原日 K 換成首筆 5 分 K（或下一批不同時間戳）也只保留一根。已有封存日期的值保留，新的形成中日期持續更新。單元涵蓋封存截至 11/03 與 11/04；五種瀏覽器配置另驗證兩次批次更新、不抓五年與圖表保留。

## 本輪驗證結果

合併與索引修改後，lint、typecheck、一般／Pages 建置通過；最終完整 Vitest 149 檔／1,852 個測試全數通過。包含 index／404 跨實例同日重用與跨日重載、年度快取淘汰不逐出 index、訪客瀏覽 500 檔仍不重抓當日 index。

前一輪桌機／390px 99 個、375px Chromium／WebKit 50 個瀏覽器案例全數通過。再納入 #224 後，桌機／390px 重跑 87 個受影響案例，375px Chromium／WebKit 重跑 50 個；保留 #219、舊自選／市場／導覽與本次所有斷言，25 張截圖已更新。完整 CI 將驗證推送的相同程式，包括舊離線旅程、PostgreSQL 授權與 breakout 整合；最終結果記於 PR。

## CI 真實 Pages 導航修正

首輪完整 CI 在 iPhone WebKit 捕捉到頁面已離開後，舊 archive lookup 才啟動 `series/index.json` fallback，產生 access-control pageerror。ArchiveFirstProvider 現在於頁面離開後停止後續 provider 工作；BFCache 還原使用新 lifetime，舊 pending lookup 不會復活。沒有過濾錯誤或修改 smoke 斷言。

三個 late fallback 測試先重現失敗，再加入修正與 BFCache 恢復測試；完整 149 檔／1,852 單元、lint／typecheck 與精確 Pages 建置通過。原研究 smoke 在桌機與 WebKit 以兩份真實 generated snapshot（包含最新 487f3dfbf5）驗證通過，errors 為空且無橫向溢出。完整 CI 於本修正 head 重新跑全部下游檢查。

## 最新 main #224 合併

再以正常 merge 納入 b6ba9afd60。MiniChart 保留 #224 的盤中原地重繪，不重建圖表或重置縮放；市場卡片使用每分鐘批次訂閱，連續看圖保留可見範圍訂閱與五分鐘間隔，沒有另開輪詢。新增 main 的缺漏日期插回與市場卡片更新案例一併通過；同交易日期補齊與封存測試繼續保留。

最新合併後，未修改的 research-pages-smoke 也以精確 Pages 路徑與真實 generated snapshot 487f3dfbf5 通過桌機／iPhone WebKit，兩者 errors=[]、horizontalOverflow=0。
