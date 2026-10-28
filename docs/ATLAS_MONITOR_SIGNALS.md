# 自選的 Discord 突破／跌破訊號

「自選 → 清單」的行情來源，在「LINE 熱門」旁新增「突破／跌破」。
點組別的「詳細」即可連續向下看股票卡片：成功發送時間、方向、觸發價、
觸發均線、量比，以及含成交量與觸發均線的日 K。圖表可縮放，個股按鈕仍開啟既有工作台。

一檔觸發多條均線時合併成一張卡片；突破／跌破的檔數分別去重，同一檔可能同時出現在兩個方向。
詳細頁的方向選擇只篩選卡片，卡片保留該檔的其他均線訊號，避免誤以為股票僅有單一方向。
每批 20 檔，接近尾端時自動載入下一批，也保留手動載入按鈕。離開可視範圍的圖表
會釋放 canvas，卡片高度不變；手機垂直滑動與滑鼠滾輪保留原生頁面捲動，縮放用按鈕或雙指手勢。
既有 MiniChart 的滾輪縮放預設不變，只有訊號清單關閉滾輪縮放以免快速往下看時被圖表攔截。

## 資料來源與限制

- 讀現有 `breakout_alert_state` 的 `last_alert_*`，不是全市場回測的 `breakouts.json`，
  也不重新推算當下是否突破。`breakout_engine.py` 只有 Discord 成功回傳後才寫這些欄位。
  建立基準、沒有突破、發送失敗而沒有成功時間的列不顯示。
- 只看最近 30 天，沒有「今日無資料就清空」的行為；週末、休市保留視窗內的舊訊號。
  每個組別／股票／均線只保留最後一次成功紀錄，**不是完整 Discord 訊息歷史**。
  若監控既有孤兒清理刪掉狀態列，網站也沒有那列的歷史；此 PR 不改監控資料保存方式。
- 包含固定、自訂、Atlas 自選組別及大盤均線提醒；排除 `atlas_` 畫線警示狀態。
  上限 5,000 筆原始狀態，超過時明示截斷。伺服器每頁 500 筆，不受單次 REST 筆數上限影響。
- 觸發價是當時訊號價格，日 K 是既有行情 provider 的延遲資料，兩者時間不同。
  無行情仍顯示訊號，不造 K 線。不把訊號均線寫成個股預設指標。
- 台股純數字代號透過真實目錄判斷上市／上櫃，不猜 `.TW`。

## 唯讀入口與個資邊界

新增獨立 `monitor-signals` Edge Function，**不改 user-settings、chart-drawings 或登入檔**。
它沿用現有 LINE／記住裝置身分解析與 `requireCloudStorage`，只開放已獲 cloud 授權的帳號。
none／device 在前端顯示說明且不呼叫個人雲端資料；伺服器也一律拒絕，不能靠修改前端繞過。

只有 `POST { action: "list", idToken }`。使用 LINE／裝置驗證得到的 owner 查詢，
不信任 body 的 userId、group 或儲存模式。多頁讀取結束再次檢查授權，回傳欄位白名單，
不含 owner、原始 metadata、金鑰或錯誤細節。沒有寫入、刪除、一般 SQL／URL 代理能力。
共用裝置 token 的既有 `use` 身分驗證流程維持原樣；監控／行情資料只有 SELECT。

個人組別與訊號只留 RAM，不進網址、localStorage、IndexedDB、analytics 或公開 generated 檔。
請求綁定呼叫前的帳號脈絡，在初始化等待、網路或回傳解析期間切換帳號，都取消並丟棄舊結果。
回應與瀏覽器請求使用 `no-store`；網站重新開啟、切回頁面（最短一分鐘）或手動整理會讀最新資料。
服務未部署或失敗時保留原自選功能，明示錯誤與重試。

沿用 LINE 自有驗證方式，`verify_jwt = false` 只跳過 Supabase JWT 閘門，**不是匿名資料入口**。
LINE token 在固定官方 verify URL 的 POST body 驗證；裝置 token 沿用既有 RPC。
service_role 留在伺服器。CORS 限正式 GitHub Pages origin 與開發／測試 origin，不能取代身分驗證。
新 SDK 固定 `2.110.8` 並附 lockfile，不新增前端 npm 套件。

## 維護者手動啟用

此 PR 不執行 SQL、不部署、不合併、不發送任何 Discord／LINE 訊息。

1. 確認既有帳號授權／裝置工作階段與監控狀態表已啟用。沒有新增表或 SQL。
2. 檢閱並由維護者部署 `monitor-signals`，沿用現有 `SUPABASE_URL`、
   `SUPABASE_SERVICE_ROLE_KEY`、`LINE_LOGIN_CHANNEL_ID`、`ADMIN_LINE_USER_ID`。
   新 function 的 entrypoint／import map 在 `supabase/config.toml`，不更動既有 function entrypoint。
3. 先驗證兩個測試帳號：cloud 只讀自己的狀態，none／device 被拒絕；非法 token 不讀狀態表。
4. 再依既有 Pages 流程發佈前端。預設入口是同專案 `/functions/v1/monitor-signals`，
   其他環境可在建置時設定 `VITE_MONITOR_SIGNALS_API_URL`，其中不得含任何金鑰。

部署前的自動化測試只用合成訊號、假資料庫與假 LINE 回應，沒有讀正式個人訊號。
實體 iPhone 的滑動手感仍由使用者驗收，headless WebKit 不代表真機效能。

## 選股頁資料健康修正

原本 `.screener-page` 固定 `height: 100%`，但清單最少 300px，加上群組、篩選與來源文字後，
內容可能超出頁面的排版高度，後面的「資料健康」會和清單重疊。
改用頁面最小高度與自然內容高度，清單保留 300～680px、依視窗高度調整的獨立虛擬捲動區。
資料健康仍在原位置，完整排在選股內容之後，沒有改頂端列或底部導覽列。

在 390×520 的合成資料重現中，舊樣式清單底部 700.3px、資料健康頂端 486px，
重疊約 214px；新樣式資料健康頂端 734.3px，與同一清單底部保留 34px 間距。

瀏覽器回歸檢查清單捲動、兩區幾何不重疊、資料健康可展開／重查、最後一列可捲入 main，
並涵蓋一般畫面及 390×520 的矮手機視窗。

## 參考

- [Supabase Edge Function 驗證](https://supabase.com/docs/guides/functions/auth)
- [Supabase changelog](https://supabase.com/changelog)（實作時檢閱；沒有資料庫 schema 變更）
- `breakout_alert/breakout_engine.py` 的成功發送後 `last_alert_*` 寫入
- [ADR-021](../atlas/docs/architecture/ADR-021-monitor-signals-read-only.md)
