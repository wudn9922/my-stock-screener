# Atlas 免費主畫面 App

Atlas 沿用同一個 React 網站與 GitHub Pages 發布，不需要 Apple 開發者年費、
TestFlight、商店審核、付費打包或每七天續簽。這是需要網路的可安裝 Web App。

## iPhone／iPad

1. 在 **Safari** 開啟 https://wudn9922.github.io/my-stock-screener/atlas/ 。
2. 點「分享」→「加入主畫面」。較新的系統若出現「以 Web App 打開」，將它開啟。
3. 確認名稱為 Atlas，點「新增」，以後從主畫面圖示開啟。

若從 LINE 開啟，先在頁面右上角「顯示設定」查看「安裝 Atlas」，複製連結並在
Safari 開啟，再完成系統安裝。網站無法代替使用者操作 iOS 的分享選單。

## Android

在 Chrome 開啟相同網址。「顯示設定」→「安裝 Atlas」可在瀏覽器提供安裝能力時
叫出原生視窗；也可用 Chrome「⋮」→「安裝應用程式」／「加入主畫面」。
取消或只接受安裝要求不會被當成已完成安裝；安裝後的獨立視窗會顯示已加入狀態。

## 登入與畫線

- 主畫面 App 可點「使用 LINE 登入」。只有主動登入或處理登入回傳時才會在一般
  瀏覽器載入 LINE SDK；同意登入會啟用 LINE 必要的驗證儲存。
- 每次重新開啟可能先是訪客。再次點登入可使用 SDK 仍有效的工作階段，並重新向
  伺服器取得儲存授權。網址參數、安裝狀態、解碼後的使用者編號不會授予權限。
- 訪客／無儲存授權仍只在記憶體操作。裝置與雲端授權維持原規則；登出不會刪掉
  該帳號已保存的畫線。登入過期或撤銷權限會立即停止個人資料保存。
- Safari 與主畫面 App 可能使用不同儲存空間。裝置保存的舊資料請先匯出；雲端
  資料需用同一個具有雲端授權的帳號重新同步。不保證自動搬移瀏覽器資料。
- 真正的 LINE 同意畫面／iOS 返回 App 行為需要在使用者自己的裝置確認。既有
  LIFF endpoint 必須涵蓋上述 Atlas 路徑、具有 openid scope，且 LINE channel 可供
  該帳號使用；本變更不修改 LINE 後台或 Supabase 的部署／資料庫設定。

## GitHub 更新

原始碼合併並完成既有部署流程後，新開啟的 App 會載入發布的網站。於「顯示設定」
的「版本更新」檢查新版，點「更新並重新開啟」套用；保留所在頁面網址，不自動中斷
畫線。更新前確認資料已保存，訪客暫存會在重新開啟時清除。

`app-version.json` 與 HTML 的 `atlas-build` 由同一次建置產生，不含個人資料。
無法讀取、離線或錯誤版本資料時，顯示無法確認，不冒稱已更新。安裝身分與範圍
保持原有 `./`，不需因每次網站發布而重新安裝。

此 screener 版本維持原有服務工作執行緒退役與無背景快取政策，避免在未授權時
保存操作。主畫面安裝不依賴服務工作執行緒；不承諾離線啟動、離線新行情或背景監控。

## 驗證

自動測試涵蓋 manifest／圖示、iPhone／iPad／Android 引導、取消／完成安裝、
獨立視窗偵測、安全區與橫向捲動、LINE 主動登入／取消／過期／登出、帳號隔離、
重新登入取回保存畫線、登入回傳處理，以及版本更新失敗與使用者主動重新開啟。
WebKit 模擬不等於實際完成 iOS 系統安裝或真實 LINE 帳號驗證。

參考：

- https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable
- https://support.apple.com/en-eg/guide/iphone/iphea86e5236/ios
- https://developers.line.biz/en/docs/liff/developing-liff-apps/#to-use-line-login-in-an-external-browser
- https://developers.line.biz/en/reference/liff/#login
