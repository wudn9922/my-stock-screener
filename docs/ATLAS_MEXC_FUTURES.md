# MEXC 唯讀合約部位

本功能把 USDT 合約帳戶與持倉作為每次 connect／sync 的獨立回應與前端 RAM 資料。合約不進入現貨 holdings document、報價表、資產配置或持股總值；不修改 SQL、金鑰加密或唯讀權限驗證，不增加交易端點、通知或背景排程。本次只交付 PR，不合併或部署。

## 官方版本與預期清單的差異

2026-11-07 核對現行 MEXC Futures REST V1 文件，更新紀錄最新項為 2026-10-23。採用下列官方資料：

- [Integration Guide](https://www.mexc.com/api-docs/futures/integration-guide)：GET 業務參數按字典序，以 URLSearchParams 編碼後用 `&` 串接，無參數則空字串；簽名為 HMAC-SHA256(secret, accessKey + 毫秒時間字串 + 參數字串)，放在 `ApiKey`、`Request-Time`、`Signature` headers。公開請求不帶 Key 或簽名。
- [Change Log](https://www.mexc.com/api-docs/futures/update-log)：2026-01-19 把 Futures REST 網域改為 `https://api.mexc.com`。依任務「有出入以官方文件為準」採此固定網域；不接受外部 URL 或網域參數。這與任務預期的舊 `contract.mexc.com` 有差異。
- [Get All Account Assets](https://www.mexc.com/api-docs/futures/account-and-trading-endpoints/get-all-account-assets)：私有 `GET /api/v1/private/account/assets`，只取唯一的 USDT 列。
- [Get Open Positions](https://www.mexc.com/api-docs/futures/account-and-trading-endpoints/get-open-positions)：私有 `GET /api/v1/private/position/open_positions`；文件列 View Order Details 權限。`unRealizedPnl` 為 2026-06-18 更新的欄位，不使用歷史 `realised` 或 `profitRatio` 代替未實現盈虧。
- [Get Contract Info](https://www.mexc.com/api-docs/futures/market-endpoints/get-contract-info)：現行為公開 `GET /api/v1/contract/detail/country`，與任務預期的舊 `/detail` 有差異，讀取 `contractSize`。
- [Get Ticker](https://www.mexc.com/api-docs/futures/market-endpoints/get-ticker-contract-market-data)：公開 `GET /api/v1/contract/ticker`，以 `fairPrice` 作標記價格，不使用成交價 lastPrice。一次讀全部所需價格，避免每個部位各發 fair_price 請求。
- [Error Code](https://www.mexc.com/api-docs/futures/error-code)：私有端點 HTTP 403 或代碼 511／701／703 顯示 no-permission；其他來源錯誤顯示 unavailable。

未使用真實 Key 或私人帳戶。公開 GET 相容性檢查成功，當時 detail/country 為 1,382,872 bytes／727 筆，ticker 為 624,740 bytes／1,198 筆，均小於既有每個回應 2 MiB 上限。這只能確認公開端點相容性，不代表已驗證正式帳戶權限。

## 執行順序與隔離

1. 沿用現貨 apiKeyInfo 的唯讀白名單驗證，沒有 CONTRACT_ACCOUNT_READ 時直接回 `{status: "no-permission"}`，不呼叫合約私有或公開端點。
2. 現貨的 key info、account balances 與 ticker 都成功並驗證後，才開始可選的合約讀取。合約使用固定四個 GET 路徑、`redirect: error`、no-store、no-referrer、宣告與串流雙重 2 MiB 限制。
3. 全部上游讀取共用原有 9 秒 AbortController；不另加第二段 9 秒。即使某個 transport 不配合取消，也會在截止時結束等待並回合約 unavailable。沒有重試或放寬逾時。
4. 合約讀取與解析失敗只產生合約 status；接著仍執行原本的最終授權與 revision／finish 流程。合約查詢留在這些最終檢查之前，避免讀取期間被中斷連線或改掉成本時回傳過時快照。授權／資料庫衝突仍沿用原本錯誤，不能被合約降級處理吞掉。
5. finish RPC 只收到原本的現貨餘額、現貨報價及必要密文資訊。futures 不進 RPC、資料庫、holdings document 或 MEXC status 回應。

## 回應與精度

成功讀取時另附 `futures: {status: "ok", account, positions, skippedPositions}`。其他狀態只含 status，不帶上次的財務數字冒充最新值。

| 回應欄位 | 官方來源／處理 |
| --- | --- |
| account.equity／available／positionMargin／unrealized | equity／availableBalance／positionMargin／unrealized |
| position.symbol | 原始大寫代號，須符合 `^[A-Z0-9]{1,20}_USDT$` |
| side | positionType 1 多、2 空 |
| contracts／size | holdVol 張數；幣數＝holdVol × contractSize |
| entryPrice | holdAvgPriceFullyScale，缺省時 holdAvgPrice |
| markPrice | 該代號 ticker.fairPrice |
| leverage／margin | leverage／im |
| unrealized | unRealizedPnl，允許負數 |
| liquidationPrice | liquidatePrice；缺漏、null 或零表示未知，回 null |
| marginMode | openType 1 逐倉、2 全倉 |

上游金融數字常是 JSON number。解析時先驗證 JSON 與成功 envelope，再保留原始數字 token 轉成十進位字串，避免經過 JavaScript Number 而丟失精度。科學記號精確展開，數值使用原持股規則的 15 位整數／18 位小數上限；只允許未實現盈虧為負數。張數乘 contractSize 使用 BigInt 固定小數，超出精度或範圍即降級 unavailable，不截斷或捏造幣數。

嚴格要求 `success === true` 且 `code === 0`。部位原始清單最多 200 筆；超量回 unavailable，不截斷。代號不符合規則的項目跳過，只回 skippedPositions 計數；已平倉 state=3 或零張數不顯示。缺少有效 contractSize／fairPrice、方向／模式不合法或其他財務欄位不合法時，整個合約區塊回 unavailable，現貨仍完成。

## 顯示與安全記錄

合約區塊位於現貨操作下方，標題標示「僅供查看，未計入持股總值」。多綠空紅，未實現盈虧保留正負號，報酬率為該筆 unrealized / margin，保證金為零時顯示「—」。所有金額與幣數保留字串精度，長數字可換行；未知強平價顯示「—」。

手動與既有可見頁自動同步都更新該帳戶的 RAM 資料。重新讀現貨 status 或確認成本時保留；成功同步拿到 unavailable／no-permission 時取代舊數字，中斷連線、身份切換與整頁刷新不保留。沿用既有成本確認與五分鐘輪詢條件，不新增同步頻率。

錯誤沿用 `diagnostic()`，步驟僅為 futures-assets／futures-positions／futures-detail，後者包含公開 ticker。只記 step、HTTP status、固定 reason、有效數字 MEXC code 與既有白名單內的訊息；未知訊息省略。沒有合約代號、金額、數量、Key、簽名、URL、原始錯誤或回應內容。成功與跳過代號不輸出私人資料記錄。

## 驗證方法

使用假 Key、固定時間與關聯式 storage harness 驗證實際 user-settings handler，不存取正式資料庫或私人 MEXC 帳戶。測試涵蓋簽名字串／headers、精度、各欄位映射、合約錯誤不影響現貨、超時、權限、200 筆限制與診斷內容。

瀏覽器測試透過相同 handler 驗證手動／自動同步、所有 status、長數字手機排版，以及合約讀取前後整份現貨總計（包含收合的市場小計）文字不變。額外以超大權益與負未實現盈虧確認不混入持股總值；新區塊在 375px Chromium／WebKit 亦檢查無水平溢出。這是 Playwright 模擬，不是實體 iPhone 驗證。
