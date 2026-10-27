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

## iPhone sheet 後續修正

目前前端的直接啟動、可讀備援頁、官方 scheme 棄用資訊與返回聚焦，見
[LINE_LOGIN_LAUNCH.md](LINE_LOGIN_LAUNCH.md)。以下的反向碼與伺服器保護維持不變。

## 使用方式

1. 手機網頁或主畫面 App 點「使用 LINE 登入」。原來的 Atlas 視窗保留，先嘗試直接
   開啟 LINE；受阻時可用有提示文字的備援頁繼續。原視窗顯示配對碼。不需要 App 與 Safari 共用 Cookie 或 localStorage。
2. 在 LINE 完成驗證，核對與原視窗相同的配對碼，再點「確認登入原來的 Atlas」。
   只有本人剛剛發起登入時才確認；單純開啟別人轉傳的網址不會送出身分。
3. LINE 頁面顯示伺服器產生的六位確認碼。從主畫面／App 切換器回到**剛剛發起登入的原 Atlas**，
   或切回原 Safari 分頁，手動輸入該確認碼，再確認 LINE 帳號。確認碼只輸入在自己的 Atlas，
   請勿轉傳給任何人；原視窗不會自動取得這個碼。iOS 不允許網站強制切回指定的已安裝 Web App。
4. 兩邊需維持同一網路（IPv4 /24 或 IPv6 /64）。請勿在登入中切換 Wi-Fi／行動網路；
   VPN、Private Relay、不同 IP 協定或瀏覽器路由也可能造成拒絕，應在相同路由重新發起。
5. 原有伺服器重新驗證 ID token 與儲存授權，才允許裝置／雲端保存。未授權帳號
   仍可登入但只暫存；不會自行新增使用者儲存權限。

取消、超過兩分鐘、驗證失敗或原 App 被系統完整關閉時，重新開始一次登入。
若 LINE 未開啟，可點「開啟 LINE 登入視窗」，在提示頁點「在 LINE 中繼續」。不依賴 iOS 提供新視窗控制物件。
完成端只在原 App 的 begin 尚未提交（404）時重試同一交接，最多 10 次／7.5 秒；
不重啟 OAuth，不改寫已使用的交接。沒有失敗後自動重導或無限重試。
桌面瀏覽器仍保留同一儲存空間的 SDK 登入；LINE 內開啟仍先直接使用 LIFF 身分，
手機上需要重新登入時也可啟動交接。

## 交接邊界

* 接收端產生 UUID 及各 256 bits 的 read/write secret。read secret 只在原視窗 RAM
  與 HTTPS POST 存在，從不放入 LINE URL、localStorage、sessionStorage 或 IndexedDB。
* LINE 視窗只帶 write secret；完成端必須獨立通過既有 LINE ID-token 驗證。
  完成端須在最上層頁面明確核對配對碼、點確認後才傳送；iframe 不允許批准。
  配對碼只是視覺核對，不代替 proof 或 LINE 驗證。接收端須持另一份 read secret
  **加上手動輸入反向確認碼**才能原子領取一次，再由使用者確認帳號。只按 LINE 的批准
  尚不足以交出身分：持有 read secret 的發起者也必須知道 LINE 畫面顯示的反向碼。
* 交接保存於未暴露到 Data API 的 `atlas_private` schema。表啟用 RLS，表／RPC
  對 PUBLIC、anon、authenticated 均無權限；RPC 為 SECURITY INVOKER，只有現有
  service_role 可呼叫。一般使用者不能直接存取交接表。
* proof 只存 SHA-256；ID token 以 AES-GCM 加密，附加資料綁定 intent UUID。
  密鑰由既有伺服器 service-role secret 與專用 domain 派生，沒有新增瀏覽器金鑰。
  來源視窗不會在完成回應收到接收端密碼或明文 token。
* 兩分鐘後所有操作拒絕過期交接；領取／取消即刪除。下一次建立交接會清理到期列。
  最多 512 列、每列 ciphertext 16 KB，每個加鹽網路 bucket 最多五筆在途。
  沒有另建排程：閒置時過期密文可能保留至下一次 begin，但無法再領取。
* 這是必要的短期驗證傳輸，不保存個人操作／授權列。token 回原 App 後只放 RAM；
  `access.get` 仍使用真實 token 重新取得伺服器 grant，登出與到期立即失效。

## 反向確認與網路綁定（2026-10-27）

**原本的視覺配對碼與 read/write secrets 都保留。** LINE 頁面明確顯示：
「只有你剛剛在自己的裝置按了登入，才按確認」。批准後，既有 LINE 伺服器驗證先通過，
並且 SQL 在 row lock 下比對 begin／complete 的網路 hash，才保存密文與反向碼 hash。

* 六位碼由 Edge 端 `crypto.getRandomValues` 以 rejection sampling 均勻產生，包含前導零。
  只有成功的 `complete` HTTPS 回應會將明碼交給 LINE 頁面；它只留在該頁 RAM／畫面。
  資料列只存 `SHA-256(server secret + domain + intent UUID + code)`；不存明碼。
  碼不進 URL、瀏覽器儲存、ID-token 密文、begin／poll／cancel 回應或日誌。
* 原視窗的無碼 poll 只得到 pending／code_required，沒有身分，也不扣次數。
  每次使用者送出六位錯碼才由 SQL 原子累計；五次即刪除交接（並行猜測同樣受限）。
  正確碼搭配 read secret 才能一次領取。取消只刪除，不回傳 ciphertext。
* 網路只從 `CF-Connecting-IP` 取值：單一合法 IPv4 正規化為 /24，IPv6 為 /64，
  IPv4-mapped IPv6 正規化為 IPv4。以現有 server secret 與獨立 domain 加鹽 SHA-256，
  表中不存 IP／prefix 明碼。網路 hash 也作為 begin 容量 bucket。
* 缺少／格式錯誤／多值 CF 標頭時 begin 與 complete 都 fail closed；**不採用**
  `X-Forwarded-For`、`X-Real-IP` 或 body 來源。不同網路的 complete 回覆 403：
  「請在同一個網路下完成登入」，保留待完成列至取消或原兩分鐘到期，不寫身分。
* Cloudflare cross-zone Worker 使用共用 `2a06:98c0:3600::103`，不能代表使用者網路，
  因此拒絕該值。自架、直接繞過 Cloudflare 的 origin、額外反向代理或 custom domain
  不在這次信任範圍；若要支援，必須先獨立確認 ingress 的覆寫與不可繞過契約。

### 可信標頭的證據與範圍

使用目前既有的 hosted URL `https://bxhqpfeberqbtxymghyt.supabase.co/functions/v1/user-settings`。
查閱 [Supabase Edge 架構](https://supabase.com/docs/guides/functions/architecture)、
[Supabase CF IP 用法](https://supabase.com/docs/guides/database/debugging-performance#production-use-with-pre-request-protection)
及 [Cloudflare 標頭規則](https://developers.cloudflare.com/fundamentals/reference/http-headers/#cf-connecting-ip)。
Cloudflare 提供從其入口到 origin 的 CF 來源標頭；XFF 可能保留 caller 加入的值。
僅有社群回答不足以證明所有 Supabase 部署形態，這裡的證據限定上述正式 hosted 路徑。

2026-10-27 約 03:20 UTC，對既有函式做三次 GET（函式拒絕 GET，沒有任何資料寫入），
以獨立 User-Agent 標記，再只讀 `function_edge_logs` 的標頭**存在／相等布林值**：

| 請求 | HTTP | 函式入口 CF IP 存在 | CF IP 等於偽造值 | XFF 包含偽造值 |
| --- | --- | --- | --- | --- |
| 不提供 IP 標頭 | 405 | 是 | 否 | 否 |
| 偽造 CF-Connecting-IP=192.0.2.99 | 403 / Cloudflare 1000 | 未進入函式 | — | — |
| 偽造 XFF 與 X-Real-IP=192.0.2.99 | 405 | 是 | 否 | 是 |

這確認此 hosted 入口拒絕 client 偽造 CF 標頭，而不可信 XFF 的確能存留。沒有部署診斷函式、
沒有回傳／記錄真實 IP、沒有讀取 LINE token 或使用者資料。單次觀察不是未來所有路由的保證；
入口設定變更時須重驗，缺少可信標頭時不降級。網路相同也不等於本人（共用 NAT、VPN、同一 Wi-Fi
仍可能相同）；反向碼與 secrets 必須同時保留。仍應避免把碼透露給詐騙者，這不是抵抗所有社交工程的保證。

## 上線順序（Owner，PR 不代為執行）

1. 在 Supabase SQL Editor 套用 [line_login_handoff.sql](../supabase/manual/line_login_handoff.sql)。
   SQL 可重跑；新增 network_hash／code_hash／failed_attempts 欄位允許舊列為 null。
   舊在途列不會被補造網路或碼，遇到 complete／poll 會刪除並要求重新登入；移除舊六參數 RPC
   避免繞過保護。SQL 到函式的切換期間，舊版交接可能失敗，重新發起即可。
   不動既有個人資料、grants 或帳號。這是 manual SQL，沒有建立或套用
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
  disposable cluster 測試過期、取消、讀寫 proof、匿名拒絕、容量、並行單次領取、錯碼上限、
  並行錯碼、網路不符／缺少，以及舊 schema 升級／SQL 重跑。瀏覽器同樣測試合法同裝置流程、
  錯碼後更正、五次錯碼刪除與不同網路的中文拒絕訊息；執行真正 shared handler 與 crypto，
  ingress IP 在離線 fixture 模擬，SQL 規則另由真正 PostgreSQL 驗證。
* guest 零個人資料保存、既有 grant／account isolation、畫線重新登入、SDK timeout
  與手動 retry 回歸仍保留。實際數量與 exact-head CI 結果記於 PR。

離線簽章用本機測試 key，不是本人 LINE 授權；WebKit 自動測試不等於實體 iPhone。
正式 LINE 帳號同意與 iOS 系統切換仍待 owner 上線後驗收。沒有獨立外部 review signoff。

參考：[LINE LIFF 初始化規則](https://developers.line.biz/en/reference/liff/#initializing-liff-app)、
[Supabase functions / grants](https://supabase.com/docs/guides/database/functions)、
[Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security)。
