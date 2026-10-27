# iPhone 主畫面 App：直接開啟 LINE 與可關閉的備援頁

這是已合併 PR #184 的前端修正（#185 啟動方式，後續修正 iOS 改為頂層 scheme navigation）；不修改交接 SQL、Edge Function、LINE console 或帳號授權。
原本 `window.open(LIFF_URL)` 會先建立空白視窗，iOS 將它顯示為 PWA 上方的瀏覽器 sheet；
即使登入完成，該 sheet 仍可能遮住原來保存 read secret 的頁面。

## 啟動方式與官方文件

LINE 目前支援的 LIFF URL 為 `https://liff.line.me/{LIFF_ID}/path?query`。
`line://app/{LIFF_ID}` 是官方記載的舊格式，**已棄用，但尚未訂出停用日期**。
LINE 特別指出，自訂 scheme 可能被非 LINE App 接管；不能把它當成驗證 LINE App 身分的保證。
參考：[開啟 LIFF](https://developers.line.biz/en/docs/liff/opening-liff-app/)、
[LINE URL scheme 與棄用原因](https://developers.line.biz/en/docs/line-login/using-line-url-scheme/)。

### iOS／iPadOS：頂層 scheme navigation（取代 #185 的隱藏 iframe）

實機（iPhone 主畫面 PWA）驗證 #185 的隱藏 `<iframe src="line://...">` 不會開啟 LINE：
iOS WebKit 會默默忽略子 frame 的自訂 scheme navigation。現在 iPhone／iPad（含 MacIntel + 多點觸控 UA）
改為在登入按鈕的同一個同步 user-gesture handler 內（`line.login.begin` POST 送出後、await 之前），
建立一個暫時的 `<a href="line://app/{LIFF_ID}?atlasLineHandoff=...&atlasLineWrite=..." rel="noreferrer">`
並 `click()`，即頂層 navigation，隨即移除該元素。依據：LINE 文件記載 `line://app/{LIFF_ID}` 為舊格式
（已棄用、未訂停用日期），且 LINE URL scheme 支援 iOS 與 Android 版 LINE；自訂 scheme 由 App 接手時，
WebKit 不會卸載目前文件，所以原頁 RAM 中的 read secret 保留。程式只允許 `line://app/` 走這條路，
原視窗絕不 navigation 到 http(s)、不 reload、不呼叫 window.open。

### Android：維持隱藏 iframe

LINE 文件沒有記載 LIFF 的 `intent://...;package=jp.naver.line.android;S.browser_fallback_url=...` 形式，
無法確認其正確性；而 Chrome 對未處理的頂層自訂 scheme 可能顯示 `ERR_UNKNOWN_URL_SCHEME`、
`browser_fallback_url` 也會以頂層 navigation 取代原頁，兩者都會失去 read secret。因此 Android 保留 #185 的
短期隱藏子 frame（失敗只影響該 frame），取消、完成或到期即移除；同樣適用下述「未開啟提示」。

### 未開啟提示（約 1.5 秒）

送出 scheme 後監聽 `visibilitychange`（hidden）、`blur`、`pagehide`；任一發生即視為已交給 LINE。
若 1.5 秒後頁面仍 visible 且 `document.hasFocus()`，就判定 LINE 沒有開啟，把既有「開啟 LINE 登入視窗」
連結改成醒目按鈕並顯示「LINE 沒有自動開啟？請點這裡」。逾時後**不會**自動開新視窗（逾時已不在 gesture 內，
會被 popup 阻擋）、不重送 scheme。iOS 對「是否開啟 App」沒有可靠 API，這只是啟發式：
例如系統跳出確認對話框造成 blur 時不會顯示提示，但手動連結始終可見。

這是對仍支援舊 scheme 的行動環境所做的相容嘗試，**不是 LINE 新推薦的開啟方式**。
平台可阻擋或移除 scheme；使用者可隨時選「開啟 LINE 登入視窗」，改用官方 HTTPS 路徑。
桌面 SDK 登入與桌面安裝版的 window.open 備援頁維持原來行為。

## 備援 sheet

所有程式開啟的備援視窗與手動連結都先到同源 `line-login-launch.html`，而非 about:blank 或
直接到 LIFF。它有獨立的 HTML 與最小模組，不等待 React、LINE SDK 或網路授權結果，初始即顯示：

> 已在 LINE 繼續登入，完成後請按左上角「完成／X」回到原本的 Atlas 輸入確認碼

若 LINE 尚未開啟，可點該頁的「在 LINE 中繼續」原生 HTTPS 連結。沒有自動重導或重建另一個視窗。
此時若系統將連結交給 LINE，提示頁保留；返回時依 visibilitychange／focus 嘗試 window.close()。
平台不允許關閉時，提示與「關閉此視窗」按鈕保留。HTTPS 實際外部 App 派送仍由 OS／瀏覽器決定；
若選擇留在瀏覽器進行 LINE 的網頁授權，跨來源 provider 頁面屬於 LINE 控制。

helper 只接受格式正確的 intent/write proof，LIFF ID 固定來自同一次 build 的公開設定，
不採用 query 提供的 LIFF ID 或 redirect URL。載入後從自身網址移除 query，標頭 referrer policy
為 no-referrer；不啟動 SDK、不領取交接、不呼叫個人 API，也不使用 localStorage／sessionStorage／IndexedDB。
HTML 與模組是 Vite 的第二個 entry，正常與 Pages build 都會打包並發布，不需更改根目錄 LIFF endpoint。

## 返回原 App

原視窗只在交接 pending 時回應 focus／visibilitychange。確認碼欄位已出現時會捲入視野並嘗試 focus；
若先收到返回事件、稍後才收到伺服器 readiness，欄位 render 後也會執行。錯碼後重新啟用欄位同樣適用。
不在背景搶焦點，不自動送出碼，也不自動確認帳號。iOS 可能要求再次點欄位才顯示軟鍵盤。

## 安全邊界與驗證限制

只有原有 intent UUID 與 write secret 進入 App URI／備援連結；不包含 read secret、六位碼或 ID token。
read secret 繼續只留原頁 RAM 與原有 HTTPS POST。反向碼、五次錯誤上限、同網路綁定、兩分鐘 TTL、
一次性領取、獨立 LINE token 驗證及最終 access.get grant 全部維持 #184。scheme 被接管只取得這份
write proof，不能僅靠它領取身分；但不能因此宣稱自訂 scheme 沒有攔截風險，仍需核對畫面與帳號。
若不接受舊 scheme 的風險，可使用官方 HTTPS 備援流程。

單元測試驗證 iPhone／iPad 頂層 scheme 與 Android frame 都在 begin 回應前同步發出、沒有 window.open、
proof 範圍、frame 清理，以及 1.5 秒提示只在頁面仍 visible＋focused 時出現（blur／pagehide／hidden／
失焦／取消後都不出現）。瀏覽器測試以 capture-phase click 攔截並取消 `line:` 連結（Playwright 無法開啟真正
LINE），斷言它在可信的 click gesture 內發生、只有 intent＋write proof、沒有 iframe 或 window.open、主 frame
沒有任何 navigation、沒有 pagehide／beforeunload；另驗證提示出現、不自動開窗，以及桌面路徑不變。桌面／手機 Chromium／iPhone WebKit 使用真正 LINE SDK 與獨立 browser context，
驗證原頁不卸載、反向碼／網路拒絕仍生效、返回聚焦、備援關閉被阻擋時可讀、無 JS 時有文字、
以及固定目的地與零儲存。外部 native protocol 與 OS App 切換在 headless 中無法實測；
這些測試不是 LINE App 已成功打開或實體 Safari 軟鍵盤已彈出的證明。Owner 將在 iPhone 驗收。

本 PR 不合併、不部署。套用前端時須已具備 #184 的 SQL／函式；本次不需要新增 SQL 或 secrets。
