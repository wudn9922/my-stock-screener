# MEXC 唯讀合約部位

本功能把 USDT 合約帳戶與持倉作為每次 connect／sync 的獨立回應與前端 RAM 資料。合約不進入現貨 holdings document、報價表、資產配置或持股總值；不修改 SQL、金鑰加密或唯讀權限驗證，不增加交易端點、通知或背景排程。本次只交付 PR，不合併或部署。

## 官方版本與預期清單的差異

2026-11-07 核對現行 MEXC Futures REST V1 文件，更新紀錄最新項為 2026-10-23。採用下列官方資料：

- [Integration Guide](https://www.mexc.com/api-docs/futures/integration-guide)：GET 業務參數按字典序，以 URLSearchParams 編碼後用 `&` 串接，無參數則空字串；簽名為 HMAC-SHA256(secret, accessKey + 毫秒時間字串 + 參數字串)，放在 `ApiKey`、`Request-Time`、`Signature` headers。公開請求不帶 Key 或簽名。
- [Change Log](https://www.mexc.com/api-docs/futures/update-log)：2026-01-19 把 Futures REST 網域改為 `https://api.mexc.com`。依任務「有出入以官方文件為準」採此固定網域；不接受外部 URL 或網域參數。這與任務預期的舊 `contract.mexc.com` 有差異。
- [Get All Account Assets](https://www.mexc.com/api-docs/futures/account-and-trading-endpoints/get-all-account-assets)：私有 `GET /api/v1/private/account/assets`，只取唯一的 USDT 列。
- [Get Open Positions](https://www.mexc.com/api-docs/futures/account-and-trading-endpoints/get-open-positions)：私有 `GET /api/v1/private/position/open_positions`；文件列 View Order Details 權限。`unRealizedPnl` 為 2026-06-18 更新的欄位，不使用歷史 `realised` 或 `profitRatio` 代替未實現盈虧。
- [Get Contract Info](https://www.mexc.com/api-docs/futures/market-endpoints/get-contract-info)：現行為公開 `GET /api/v1/contract/detail/country`，與任務預期的舊 `/detail` 有差異，讀取 `contractSize`。選填參數 `symbol`（字串，合約名稱，如 `BTC_USDT`）；省略時回全部合約。
- [Get Ticker](https://www.mexc.com/api-docs/futures/market-endpoints/get-ticker-contract-market-data)：公開 `GET /api/v1/contract/ticker`，以 `fairPrice` 作標記價格，不使用成交價 lastPrice。選填參數 `symbol`（字串，合約名稱）；省略時回全部合約。另有 `GET /api/v1/contract/fair_price/{symbol}`（官方列 20 次／2 秒），不採用，避免多一種回應格式。
- [Error Code](https://www.mexc.com/api-docs/futures/error-code)：私有端點 HTTP 403 或代碼 511／701／703 顯示 no-permission；其他來源錯誤顯示 unavailable。

未使用真實 Key 或私人帳戶。公開 GET 相容性檢查成功，當時 detail/country 為 1,382,872 bytes／727 筆，ticker 為 624,740 bytes／1,198 筆，均小於既有每個回應 2 MiB 上限。這只能確認公開端點相容性，不代表已驗證正式帳戶權限。

## 2026-11-07 修正：只讀持有合約（正式環境一律 unavailable）

正式記錄顯示私有 assets、open_positions 均成功，接著 `{"step":"futures-detail","status":200,"reason":"response-size-or-empty"}`：不帶參數的 `detail/country` 已超過每回應 2 MiB 上限（`ticker` 全清單同樣有風險），整個合約區塊因此降級 unavailable，即使使用者確有持倉。

修正後只查實際持有的合約：

1. 取 open_positions 中有效且未平倉的代號去重。代號已通過 `^[A-Z0-9]{1,20}_USDT$`，才放入固定 origin、固定路徑的唯一 query：`/api/v1/contract/detail/country?symbol=XXX_USDT` 與 `/api/v1/contract/ticker?symbol=XXX_USDT`。
2. 不超過 **20** 個代號時逐代號讀取，兩端點合計最多 40 個請求，**同時最多 4 個**。每個回應沿用 2 MiB 上限。單一合約回應可為包裝物件或陣列；都必須正好含該代號，否則 unavailable。任一請求失敗即停止排程並中止其餘進行中的請求，只記一筆診斷。
3. 超過 20 個代號時，逐代號請求在共用 9 秒期限內不可行（最多 400 個請求，也會撞到公開端點頻率限制），改回各讀一次全清單；**只有這兩個公開全清單**上限放寬為 **8 MiB**（2026-11 前實測 1.4 MB／0.6 MB，保留約 6 倍成長空間），私有端點與逐代號讀取仍為 2 MiB。超過 8 MiB 仍降級 unavailable，不截斷。
4. 其他不變：固定 `https://api.mexc.com`、GET、`redirect: error`、no-store、no-referrer、宣告與串流雙重大小檢查、共用 9 秒 AbortController、嚴格解析與精度。診斷仍只記 step／status／固定 reason／允許的 code 與 msg，不記代號、金額、query 或 Key。

**2026-11-07 核對時的限制**：當時工作環境的網路政策封鎖 `www.mexc.com`、`api.mexc.com` 與 MEXC 文件鏡像，端點與 `symbol` 參數由搜尋引擎索引的上述兩個官方頁面確認。2026-11-08 的解析修訂已直接讀取官方 open_positions 與 ticker 文件，但沒有呼叫公開行情或私人帳戶；程式仍同時接受公開端點的物件或陣列回應。

## 2026-11-08 修正：持倉欄位缺漏與逐筆隔離

正式記錄出現 `futures-positions`／HTTP 200／`invalid-response`，現貨及合約資產已成功。此次使用假回應覆蓋缺漏欄位的情況；不讀取真實持倉。現行官方 open_positions 文件列有 `unRealizedPnl`，但實際回應不能以它為必填條件；`liquidatePrice` 的官方描述為逐倉強平價，不能把全倉的非正哨兵當成有效價格。

- `unRealizedPnl` 有有效值時原樣保留，包含零與18位小數。缺少、null或空字串時，優先用 `holdAvgPriceFullyScale`，缺省用 `holdAvgPrice`；配合公開 ticker 的 `fairPrice`、`holdVol × contractSize` 計算。多單為 `(fairPrice − entryPrice) × holdVol × contractSize`，空單方向相反。沒有用已實現損益或報酬比例代替。
- 金融運算使用18位固定尺度的BigInt，輸入數值不經Number。合約幣數四捨五入至18位；推算未實現盈虧直接使用未先行捨入的原始乘積，最後四捨五入至8位，負數在恰好一半時遠離零。共用比值工具也能處理1/3等循環小數；不再因乘積超出18位小數就拒絕整份資料。
- 強平價缺少、null、空字串、零或負數均回null，畫面顯示「—」。其他單筆格式錯誤（方向／模式／狀態、均價、張數、保證金等）只跳過該筆，計入 `skippedPositions`。每筆都通過原回應契約後才合併；全部跳過時顯示「目前沒有可顯示的合約持倉」。原本閉倉、零張數與不支援代號規則保留。
- positions容器不是陣列、超過200筆，或既有上游／公開行情整份回應失敗時，仍回unavailable；沒有用估計的標記價補缺。現貨流程、skippedAssetNames、20檔以下逐檔讀取、4個並行及2 MiB上限不變；超過20檔既有的兩個8 MiB公開全清單備援也不變。
- 逐筆 `invalid-response` 只額外記固定 `field` 及該筆 `positionKeys`。最多讀前40個欄位名，已知官方鍵名保留，未知鍵名替換為 `[other]` 並去重；不記任何持倉欄位值、幣名、金額、數量或Key。整份positions結構錯誤只記 `field: data`，不記原始內容。HTTP status及原有技術錯誤碼／白名單訊息沿用既有規則。

此修訂不需SQL或新環境變數。維護者審查後更新user-settings（`mexcFutures.ts`及`mexc.ts`）與Atlas提示文案；本次只推PR，不部署。沒有改金鑰加密、雲端授權、限流、背景同步或現貨快照，合約仍只在RAM中顯示。

## 執行順序與隔離

1. 沿用現貨 apiKeyInfo 的唯讀白名單驗證，沒有 CONTRACT_ACCOUNT_READ 時直接回 `{status: "no-permission"}`，不呼叫合約私有或公開端點。
2. 現貨的 key info、account balances 與 ticker 都成功並驗證後，才開始可選的合約讀取。合約使用固定四個 GET 路徑（公開路徑只帶已驗證的 `symbol`）、`redirect: error`、no-store、no-referrer、宣告與串流雙重 2 MiB 限制（僅 20 個以上代號時的兩個公開全清單為 8 MiB）。
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
| unrealized | 優先unRealizedPnl；缺省依fairPrice、持倉均價與張數×contractSize推算，允許負數 |
| liquidationPrice | liquidatePrice；缺漏、null、零或負數表示未知，回 null |
| marginMode | openType 1 逐倉、2 全倉 |

上游金融數字常是 JSON number。解析時先驗證 JSON 與成功 envelope，再保留原始數字 token 轉成十進位字串，避免經過 JavaScript Number 而丟失精度。科學記號精確展開，輸入與可顯示數值沿用原持股規則的15位整數／18位小數上限；只有輸出的未實現盈虧可為負數，負強平哨兵先歸null。張數乘contractSize使用BigInt固定小數並四捨五入，範圍無法顯示或幣數小到捨入為零時只跳過該筆，不捏造正幣數。合約帳戶摘要仍取assets來源，不以可能略過部分部位的清單反推。

嚴格要求 `success === true` 且 `code === 0`。部位原始清單最多200筆；超量回unavailable，不截斷。代號不符合規則或單筆財務欄位無效的項目跳過並計數；已平倉state=3或零張數不顯示。公開回應缺少有效contractSize／fairPrice等既有來源檢查保留，來源無法成立時不顯示估計數值，現貨仍完成。

## 顯示與安全記錄

合約區塊位於現貨操作下方，標題標示「僅供查看，未計入持股總值」。多綠空紅，未實現盈虧保留正負號，報酬率為該筆 unrealized / margin，保證金為零時顯示「—」。所有金額與幣數保留字串精度，長數字可換行；未知強平價顯示「—」。

手動與既有可見頁自動同步都更新該帳戶的 RAM 資料。重新讀現貨 status 或確認成本時保留；成功同步拿到 unavailable／no-permission 時取代舊數字，中斷連線、身份切換與整頁刷新不保留。沿用既有成本確認與五分鐘輪詢條件，不新增同步頻率。

錯誤沿用 `diagnostic()`，步驟僅為futures-assets／futures-positions／futures-detail，後者包含公開ticker。只記step、HTTP status、固定reason、有效數字MEXC code與既有白名單訊息；逐筆解析失敗另有固定field及最多40個安全欄位名。未知鍵名遮蔽、未知訊息省略。沒有合約代號、金額、數量、Key、簽名、URL、原始錯誤或回應內容。成功與原有跳過代號不輸出私人資料記錄。

## 驗證方法

使用假 Key、固定時間與關聯式 storage harness 驗證實際 user-settings handler，不存取正式資料庫或私人 MEXC 帳戶。測試涵蓋簽名字串／headers、精度、各欄位映射、合約錯誤不影響現貨、超時、權限、200 筆限制與診斷內容。

瀏覽器測試透過相同 handler 驗證手動／自動同步、所有 status、長數字手機排版，以及合約讀取前後整份現貨總計（包含收合的市場小計）文字不變。額外以超大權益與負未實現盈虧確認不混入持股總值；新區塊在 375px Chromium／WebKit 亦檢查無水平溢出。這是 Playwright 模擬，不是實體 iPhone 驗證。
