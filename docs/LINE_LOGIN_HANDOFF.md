# LINE 網頁／主畫面 App 登入交接

本修正解決「Atlas → LINE → 未登入網頁 → 再次登入」的循環。程式與 SQL 在 PR
供檢查，尚未更改正式資料庫、部署函式或網站；合併本身不代表所有前置步驟已完成。

## 原因與實際重現

LINE SDK 的 PKCE 驗證資料存在發起登入的瀏覽器儲存空間。iOS 主畫面 App、Safari
與 LINE 開啟的視窗可能不共用該空間。使用真正的官方 SDK 2.31.2、測試用 ECDSA
簽章與離線 LINE HTTP 回應重播：同一 browser context 能交換 code、取得 ID token；
全新 context 帶著相同 callback 卻不會交換 code，`isLoggedIn()` 為 false。
前一版 SDK double 直接回報帳號，沒有重現這個驗證資料遺失的情境。

公開 LIFF 設定的 Endpoint URL 是 `/my-stock-screener/`，舊根目錄會在初始化前
立即 redirect 至 `/atlas/`。現在兩個入口使用同一份 Atlas build：先處理 primary
SDK 初始化，再進行 SDK secondary navigation／應用程式網址整理。primary／secondary
的交接識別都會先於 router 處理。LINE 後台、channel、openid scope 不變。

## 使用方式

1. 手機網頁或主畫面 App 點「使用 LINE 登入」。原來的 Atlas 視窗保留，另一個
   視窗開啟 LINE 的 LIFF 入口。不需要 App 與 Safari 共用 Cookie 或 localStorage。
2. 在 LINE 完成驗證；返回的網頁會顯示驗證完成與返回原視窗的提示。
3. 從主畫面／App 切換器回到 Atlas，或切回原來的 Safari 分頁，確認顯示的 LINE
   帳號後點「確認登入此帳號」。iOS 不允許網站強制切回指定的已安裝 Web App。
4. 原有伺服器重新驗證 ID token 與儲存授權，才允許裝置／雲端保存。未授權帳號
   仍可登入但只暫存；不會自行新增使用者儲存權限。

取消、超過兩分鐘、驗證失敗或原 App 被系統完整關閉時，重新開始一次登入。
若沒有開啟新視窗，可點「開啟 LINE 登入視窗」。不依賴 iOS 提供新視窗控制物件。
完成端只在原 App 的 begin 尚未提交（404）時重試同一交接，最多 10 次／7.5 秒；
不重啟 OAuth，不改寫已使用的交接。沒有失敗後自動重導或無限重試。
桌面瀏覽器仍保留同一儲存空間的 SDK 登入；LINE 內開啟仍先直接使用 LIFF 身分，
手機上需要重新登入時也可啟動交接。

## 交接邊界

* 接收端產生 UUID 及各 256 bits 的 read/write secret。read secret 只在原視窗 RAM
  與 HTTPS POST 存在，從不放入 LINE URL、localStorage、sessionStorage 或 IndexedDB。
* LINE 視窗只帶 write secret；完成端必須獨立通過既有 LINE ID-token 驗證。
  接收端須持另一份 read secret 才能原子領取一次，並由使用者確認帳號。
* 交接保存於未暴露到 Data API 的 `atlas_private` schema。表啟用 RLS，表／RPC
  對 PUBLIC、anon、authenticated 均無權限；RPC 為 SECURITY INVOKER，只有現有
  service_role 可呼叫。一般使用者不能直接存取交接表。
* proof 只存 SHA-256；ID token 以 AES-GCM 加密，附加資料綁定 intent UUID。
  密鑰由既有伺服器 service-role secret 與專用 domain 派生，沒有新增瀏覽器金鑰。
  來源視窗不會在完成回應收到接收端密碼或明文 token。
* 兩分鐘後所有操作拒絕過期交接；領取／取消即刪除。下一次建立交接會清理到期列。
  最多 512 列、每列 ciphertext 16 KB，每個加鹽 caller bucket 最多五筆在途。
  沒有另建排程：閒置時過期密文可能保留至下一次 begin，但無法再領取。
* 這是必要的短期驗證傳輸，不保存個人操作／授權列。token 回原 App 後只放 RAM；
  `access.get` 仍使用真實 token 重新取得伺服器 grant，登出與到期立即失效。

## 上線順序（Owner，PR 不代為執行）

1. 在 Supabase SQL Editor 套用 [line_login_handoff.sql](../supabase/manual/line_login_handoff.sql)。
   SQL 可重跑，不動既有個人資料、grants 或帳號。這是 manual SQL，沒有建立或套用
   Supabase migration history。維持 `atlas_private` 不列入 exposed schemas。
2. 部署更新的 `user-settings` 與它的 `_shared/lineLoginHandoff.ts`。沿用既有
   `SUPABASE_SERVICE_ROLE_KEY`、`SUPABASE_URL`、`LINE_LOGIN_CHANNEL_ID`，不需要新 secret。
   既有 `.github/workflows/supabase-functions.yml` 會在符合條件的 main push 部署；
   本 PR 沒有 dispatch、手動部署或更改 verify_jwt 設定。
3. 發布 Atlas build。Atlas job 現在將同一份 `index.html` 放在根目錄與 `/atlas/`，
   generated atlas owner 一起發布這兩個入口，避免 primary endpoint 初始化前跳轉。
   原 daily owner 的 legacy 根目錄權限保留，其他 owner 與同路徑衝突保護不變。
4. 用本人手機確認 LINE 同意畫面、回原 App、帳號確認、重新取回既有畫線與登出。
   SQL 或函式未就緒時會顯示登入服務錯誤，不能用新前端假裝完成登入。

## 驗證

* 實際 pinned SDK 在離線 provider fixture 做 ECDSA ID-token 驗證、PKCE code 交換、
  全新瀏覽器分區失敗重現、primary endpoint 順序、secondary 身分恢復與跨分區交接。
  真正 SDK 的測試入口使用 HTTPS，讓 WebKit 的 Secure expiry cookie 與正式站一致；
  只由本機靜態 build 回應測試網域，不轉送瀏覽器 Cookie／標頭／body，不關閉 TLS 驗證。
* 桌面 Chromium、手機 Chromium、iPhone WebKit 都使用兩個不共享瀏覽器儲存的
  context 測試：原 App 只有確認後才能取得 storage grant，登出後立即回暫存模式。
* Node unit tests 執行真正 server handler、加密與驗證邊界；PostgreSQL 15+ 的
  disposable cluster 測試過期、取消、讀寫 proof、匿名拒絕、容量及並行單次領取。
* guest 零個人資料保存、既有 grant／account isolation、畫線重新登入、SDK timeout
  與手動 retry 回歸仍保留。實際數量與 exact-head CI 結果記於 PR。

離線簽章用本機測試 key，不是本人 LINE 授權；WebKit 自動測試不等於實體 iPhone。
正式 LINE 帳號同意與 iOS 系統切換仍待 owner 上線後驗收。沒有獨立外部 review signoff。

參考：[LINE LIFF 初始化規則](https://developers.line.biz/en/reference/liff/#initializing-liff-app)、
[Supabase functions / grants](https://supabase.com/docs/guides/database/functions)、
[Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security)。
