# PR-D：交易日曆

本分支從建立時最新 main `0cb7789772` 開始，D1 一個 commit。PR-A／B／C 分開審查；沒有合併、部署或手動觸發正式排程。

## 更新與來源

`scripts/build_market_calendar.py` 只用 exchange-calendars **4.13.2** 的 XTAI／XNYS，產生 `docs/report/market-calendar.json`。涵蓋產生日前 30 天至後 366 天，含交易日的 UTC 開收盤秒數、交易所時區、平日休市日與提前收盤清單。日曆約 39 KB，前端不讀全市場行情來推算交易日。臨時休市／交易所修訂仍依上游日曆版本更新，不自行編造日期。

每日 schedule.yml 新增獨立的小型 calendar job，沿用 generated_branch.py 的 restore／owner delta／非強制推送重試，只發布 calendar owner；既有 daily job、通知與永久紀錄流程不改。calendar job 的權限只有 contents: write，並有獨立 concurrency，沒有新通知。產出加入 gitignore，避免意外進 main。

market-extras.yml 在抓取 IPO／盤前盤後之前，用紐約當地日期檢查是否為交易日。休市時不 restore、不抓公開來源、不 publish，保留上一份顯示資料；UTC 已跨午夜的週五盤後仍屬紐約週五，可以更新。這個檢查直接讀本機 exchange-calendars，不依賴遠端 JSON 是否剛發布。

## 前端

LiveQuoteFeed 仍是唯一批次報價與輪詢來源。共用 MarketCalendarStore 每個 UTC 日最多讀一次小檔，同時請求合併；沒有新 interval、帳戶儲存或 API。

- 有日曆：休市日開頁讀一次最後收盤價，後續不週期更新；失敗的首次報價也不在休市日重試。
- 提前收盤：例如 2026-11-27 紐約 13:00 精確停止，不套用一般收盤寬限。
- 一般收盤：保留原有 15 分鐘 final-print 寬限；供報價狀態使用的零寬限呼叫也相容。
- 缺檔、HTTP／網路／解析失敗、超出涵蓋日期：退回原星期與當地時段規則，不讓圖表壞掉。請求最長 2.5 秒，下一個 UTC 日再載入。

TW 使用同一個 XTAI 日曆供上市／上櫃；美股使用 XNYS。分 K 圖既有的 interval 更新入口也先等同一份日曆，再判斷時段，沒有新增輪詢。沒有變更價格、K 線、均線、畫線、盤前盤後價格或 GCP 推播邏輯。

## 驗證與操作

Python 測 TW 國慶補假／農曆年、週末、US 感恩節、13:00 提前收盤、DST、未來一年覆蓋、原子寫入與 workflow owner／gate。既有 generated publisher、market-extras、data-status 測試仍執行。

Vitest 測格式／時區／日期驗證、同日合併、跨日重載、失敗退路、休市不更新與提前收盤。瀏覽器測桌機、390px Chromium／iPhone WebKit、375px Chromium／iPhone WebKit，包含原 live-quotes、closed-market、nav-inventory；原檢查沒有跳過或放寬。實際最終數字列於 PR 說明。

正式發布由維護者合併後的正常排程執行。沒有 SQL、函式部署、金鑰設定或補檔步驟；首次 JSON 發布以前前端使用原規則。未執行正式 schedule 或 market-extras workflow。

PR-A／C 尚未合併，因此本分支以 main 的 feed 為基底；依 A→B→C→D 整合時必須保留 A 的報價狀態與 C 的最短訂閱間隔／timeout，再加入本次日曆判斷，不能用本分支整份覆蓋那些功能。

## 本地結果

clean npm ci、lint、typecheck、138 個 Vitest 檔／1,786 個測試、一般與 Pages 建置通過。補上分 K 的日曆入口後再次執行完整 lint／typecheck／Vitest，仍為 138 檔／1,786 個測試全數通過。

Python：新日曆 7、既有 generated publisher 33、market-extras 5、data-status 12，全數通過。generated inventory 首次驗證指出新產出的 gitignore 尚未登錄，已加上獨立產出路徑，保留原檢查並重跑通過。

瀏覽器：桌機／390px 45、375px 30，另補分 K 的五種配置 15，共 90 通過。新增分 K 測試初次在圖表掛載後、計時器 effect 就緒前快轉而失敗；補上等待原日曆請求並讓回應完成，再重跑全部 15 通過，請求次數／13:00／退路斷言均保留。

沒有介面重新排版。以下是假的行情 fixture 在提前收盤後保留圖表的畫面，沒有帳戶或持股資料：

- [桌機](screenshots/pr-d/early-close-desktop-chromium.png)
- [390px Chromium](screenshots/pr-d/early-close-mobile-chromium.png)／[iPhone WebKit](screenshots/pr-d/early-close-iphone-webkit.png)
- [375px Chromium](screenshots/pr-d/early-close-mobile-chromium-375.png)／[iPhone WebKit](screenshots/pr-d/early-close-iphone-webkit-375.png)
