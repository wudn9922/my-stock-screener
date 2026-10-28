# Atlas MEXC 只讀同步（PR B）

PR A #199 已合併後，從 main `61eb973919c8f1fd045213c726d9eed293a9f346` 開始此分支。
此功能讓使用者**在網站輸入自己的 MEXC API Key 與 Secret Key**，伺服器讀取現貨餘額及公開 USDT 行情；不下單、不取消訂單、不交易、不轉帳、不提幣，也不發任何通知。

## 網站操作

1. 先取得目前 Atlas 帳號的 **cloud** 儲存授權。device／none 仍可用 PR A 上傳或貼上，但不會顯示 API 金鑰輸入，也不發出 MEXC 請求。
2. 「自選 → 持股 → 新增帳戶 → MEXC → 使用 MEXC API 連線」。既有 MEXC 帳戶也能直接連線，不需要先匯入空檔案。
3. 到 MEXC 的 [API 管理](https://www.mexc.com/user/openapi) 建立金鑰，**只勾讀取，不勾交易、轉帳或提幣**。在 Atlas 填入 API Key／Secret Key，勾選確認，再按「連接並讀取 MEXC」。不要把金鑰貼到聊天、issue、PR 或 GitHub。
4. 金鑰欄位送出時立即清空。應用程式不存 localStorage、sessionStorage、IndexedDB 或 URL；不自動重送金鑰。連線成功後只顯示 API Key 末四碼，失敗需重新輸入。
5. 新幣種第一次填入含買進費用的**目前平均成本（USDT）**。網站不假裝 MEXC 的餘額 API 能提供成本，也不把未知成本填成零。確認前只顯示待核對餘額，此帳戶暫不列入損益總計。
6. 後續餘額不變／減少時保留均價，自動套用有效餘額；新增或增加時保留上次確認的持股，要求補成本／核對均價後才套用新快照。相同餘額下先賣再買、轉入轉出或成本變動無法從餘額判斷，需自行核對。更換 API 金鑰應屬於同一 MEXC 帳戶；要換帳戶請新增 Atlas 帳戶，避免沿用不同帳戶均價。
7. 可按「同步餘額」，或在頁面可見且無待確認成本時每五分鐘同步；隱藏頁面、離開持股頁或編輯金鑰／成本時不啟動背景同步。已發出的讀取可能完成，但不會持續排程。沒有人在看頁面時不跑伺服器 cron。
8. 「中斷連線」會刪除伺服器金鑰與同步快照，保留已確認的持股與均價。它不會替你刪除 MEXC 平台上的金鑰；如不再使用，可自行至 MEXC 撤銷。

每次快照為當時現貨 **free＋locked**，不重複加 available；不含合約、理財、歷史交易或已實現損益。USDT 自身餘額以 USDT 計價，單位價格 1 是同幣別換算，並非宣稱 USDT＝USD；成本仍由使用者確認。

## 官方協定與限制

- [MEXC Introduction](https://www.mexc.com/api-docs/spot-v3/introduction)：固定 API 主機、HMAC-SHA256、毫秒 timestamp、recvWindow、API key header 及限流。
- [Query APIKey Info](https://www.mexc.com/api-docs/spot-v3/spot-account-trade/query-api-key-info)：查金鑰狀態與權限。伺服器要求有效且未過期，包含 SPOT_ACCOUNT_READ；只接受已知的讀取權限，任何寫入／未知權限都拒絕。帳戶 canTrade／canWithdraw 不是金鑰權限，不能用它們代替檢查。
- [Account Information](https://www.mexc.com/api-docs/spot-v3/spot-account-trade/account-information)：GET /api/v3/account 取得現貨餘額。
- [Symbol Price Ticker](https://www.mexc.com/api-docs/spot-v3/market-data-endpoints/symbol-price-ticker)：GET /api/v3/ticker/price 取得全部公開價格後，只保留此帳戶持有商品的 USDT 報價。缺少交易對顯示「無報價」，不猜測兌換路徑。

所有 MEXC 請求都由伺服器送往固定 `https://api.mexc.com`，僅允許上述三個 GET 路徑，不接受前端指定網址、不跟隨轉址，整組外部讀取有九秒逾時及兩 MB 回應大小上限。簽章只使用小寫十六進位 HMAC，API Secret 永遠不放在網址或送給 MEXC。

官方權限查詢要求 accessKey 參數；**公開 API Key 僅在伺服器 → MEXC 的必要查詢中出現**，並以 TLS 傳送，沒有進瀏覽器 URL、應用程式 log 或錯誤回應。MEXC 自己已持有該識別碼；Secret 只用於伺服器簽章。網站 → Atlas 的金鑰輸入為固定 Edge URL 的 POST，一次明確送出，不透過 MEXC CORS 或瀏覽器直接查詢。

行情時間標示為同步取回時間，ticker/price 沒有提供成交時間與同來源前日收盤，因此**MEXC 的今日損益顯示未知**，不拿滾動 24 小時漲跌冒充今日損益，也不混用 Binance 前日價格。未連線的 PR A 手動持股仍沿用原行情。帳戶／市場小計保留 USDT；沿用 PR A 的 USD/TWD 規則，沒有真正 USDT/TWD 時不假設等值，台幣總計標示未完整。

MEXC 官方建議 IP 白名單；白名單必須是伺服器出口，手機 IP 不等於 Supabase Edge 出口，且 Edge 不保證固定出口 IP。此功能不自動變更白名單、不要求付費出口服務。未綁 IP 的金鑰依官方說明有 90 天期限，到期請續期或更換。

## 金鑰保存與授權

- API Key 與 Secret 一起以 AES-256-GCM 加密；每次保存有隨機 12-byte IV、128-bit tag，AAD 綁定版本＋已驗證 LINE owner＋帳戶 UUID。移動密文到另一帳戶／owner 或修改密文都無法解密。
- 加密主金鑰只從 Supabase Edge 環境變數 `HOLDINGS_ENCRYPTION_KEY` 讀取，必須是 32 bytes 的標準 base64；沒有 service_role／預設字串降級，不出現在前端 VITE 環境或程式包。
- `atlas_private.atlas_mexc_connections` 只存密文、末四碼、經過驗證的餘額／公開報價快照與版本；不保存原始 MEXC 回應、備註或 UID。RLS 開啟，anon／authenticated／PUBLIC 無表與 RPC 權限，只有 service_role。
- `holdings.mexc.status/connect/sync/apply/disconnect` 每次先經既有 requireCloudStorage，owner 一律用已驗證身分，不信任 payload owner_id／userId。外部 I/O 完成後再檢查一次授權，撤權後不提交。
- 前端在 initializeAccess 等待之前就綁定原帳號脈絡的 AbortSignal；切換帳號／模式時，舊金鑰與持股請求不得改送至新身分。這個保護也套用原持股 cloud 讀寫，不更改登入流程或正常儲存行為。
- 所有回應 no-store；錯誤採固定白名單文案，原始 network error、簽章 URL、金鑰、上游私密回應或資料庫細節不送 logger／瀏覽器。瀏覽器金鑰輸入為遮蔽欄位，關閉自動完成。

## 並行、限流與保護

同 owner 的匯入／同步／刪除沿用 PR A advisory lock。每 owner 的外部讀取最少間隔 30 秒，錯誤金鑰也消耗額度；遇到 MEXC 418／429，保存 Retry-After（上限一小時），不做快速重試。狀態讀取與成本確認不呼叫 MEXC。

claim／finish 以 30 秒有效的隨機一次性 request_id 配對。中斷連線使進行中的 request 失效，晚到的同步不能重建已刪除金鑰。帳戶 revision 與 snapshot UUID 防止舊成本覆蓋其他裝置的新資料；手動修改後需重新同步／確認。對應過期時可捨棄尚未儲存的成本再重讀，並不清除已保存的成本。

每帳戶最多 1,000 非零餘額，持股仍受 PR A 每人 30 帳戶／2,000 持股上限。格式錯誤、負數、精度超過 18 位或數值超過現有欄位範圍時，整份同步失敗並保留前一份；有效的空現貨餘額才可自動套用零持股。新／增加餘額的確認以伺服器保存的快照為準，前端只能傳成本，不能偽造數量。

## 維護者手動啟用順序（本 PR 不執行）

1. 確認 PR A 的 `supabase/manual/atlas_holdings.sql` 與持股 user-settings 已完成。檢閱此 PR 的 `supabase/manual/atlas_mexc_holdings.sql`，由維護者在可丟棄環境驗證，**手動套用**後再進正式環境。可重複套用；只新增 MEXC private 表／RPC，沒有修改 K 線、財務、價格或 archive。
2. 在自己的安全環境產生隨機 32-byte base64 主金鑰（例如 `openssl rand -base64 32`），存入 Supabase **Edge Function Secrets** 的 `HOLDINGS_ENCRYPTION_KEY`。不要放進 GitHub、前端 `.env`、VITE 變數或 PR。
3. 維護者部署 `user-settings`，包含 `mexc.ts`、`mexcContract.ts` 與既有 `holdings.ts`。維持原身分驗證與授權設定，不自動新增 cloud grant。
4. 維護者驗證 service_role-only／RLS、跨 owner 隔離、限流、revision／斷線競態；確認密文欄位沒有明文、各 public response 沒有金鑰。再依原流程發佈前端，使用者才在網站輸入自己的只讀 MEXC 金鑰。

未設定主金鑰／資料表時，網站明確顯示未啟用，禁止輸入連線資料，原有持股匯入仍可使用。中斷連線不依賴主金鑰，遺失或更換主金鑰後仍可刪除舊連線；改主金鑰後既有密文不能自動解開，需重新連線，已確認持股／均價保留。

## 驗證界線

單元與瀏覽器測試只用自建假 MEXC 回應；沒有對 api.mexc.com 發出真實請求、沒有使用真實使用者金鑰。MEXC 官方說明目前沒有 sandbox，因此這不是正式環境連線認證。

新 SQL 僅以 pglast 8.7 **靜態解析** DDL 與 PL/pgSQL 語法，沒有執行 SQL，也沒有部署。relational fake 測試會執行真正 user-settings handler／AES／HMAC 與假上游，但不能證明真實 PostgreSQL 的 RLS／鎖效力；仍須完成上述維護者驗證。headless WebKit 不等於實體 iPhone。實際 lint／型別／單元／建置／瀏覽器數量以 PR 為準。
