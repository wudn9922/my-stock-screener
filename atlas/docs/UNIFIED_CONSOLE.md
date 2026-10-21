# Atlas 與參數控制台整合

## 操作

- 新入口 `atlas/?page=console`，Atlas 導覽的「控制台」。每日 LINE 報告的參數連結也改指向這裡；舊控制台專案與 LIFF endpoint 沒有被修改。
- 「均線參數」沿用原有 user-settings 的 LINE 驗證、股票／自訂分組管理、大盤管理員權限。
- 在選股頁套用既有技術／財務條件，再按「存成自選清單」，可保存多組命名策略；控制台另可建立手動固定股票清單。
- 每次開啟比較上次開啟的「今天新入選、今天出榜」與連續入選天數。「今天」指畫面明列的資料日；同日重開不增加天數，來源失敗或日期倒退不判定出榜。
- 本機清單與歷史使用獨立且驗證過的 localStorage v1 文件，不遷移既有畫線／指標 IndexedDB。提供獨立 JSON 匯出／匯入；大清單每次顯示 100 檔。
- 每組清單可選突破監控與均線。勾選只保存意圖；同步且部署新版服務後，沿用既有突破引擎、門檻與通知管道。
- 從 LINE 登入後，建立清單、儲存名稱、修改監控開關與均線會自動同步到 Supabase；同一清單的寫入與刪除會依序執行。非 LINE 瀏覽器仍保存在本機，雲端失敗提供「重試同步」。刪除已連結清單時，先刪雲端再刪本機；若失敗或發生版本衝突，保留本機並顯示原因。
- 清單每檔股票旁的「設定個股均線」可永久保存固定參數。固定參數以 stocks.group_id='ungrouped' 記錄，不加入分組、不建立額外監控；出榜或刪除清單後仍保留。股票在分組或已啟用監控的清單中時，固定參數優先於分組／清單均線；重新入榜或加入分組會再次使用。
- 均線參數頁可依代號／組別名稱搜尋、篩選組別（含無分組），以約五筆高度的區域捲動。編輯使用大型均線輸入框及確認／取消按鈕；刪除使用獨立確認框。固定參數仍可從「無分組」編輯／刪除；新增同股票到分組時沿用其固定參數。
- 美股財報預設季度、20 季（近五年），年度以 p=a 明確保存。依實際資料顯示期數，不補造季度／EPS；美股河流圖保留原年度 EPS 估值口徑。
- 同業比較在財報下方預設收合，展開才載入；依 industry 自動匹配，可自行增減，最多四個對象。每列揭露期末日、幣別、缺值／金融業不適用原因。
- 題材輪動追蹤比較兩段 21 交易日複利相對報酬，顯示排名變化及領先／轉強／轉弱／落後。至少需 43 個對齊資料點，並非資金流量。
- 每個頁面最下方有收合的資料健康總覽；展開才檢查格式、日期、來源與缺漏，超過四個日曆日提示舊資料。個股財報載入結果也會列入。
- 篩選視窗改為最上層 portal，底部套用列不可壓縮，依 visualViewport 調整。React route 直接傳入條件，套用後更新結果、URL 與重新載入狀態。

## Supabase 正式部署待辦（此次未執行）

1. 執行 supabase/manual/atlas_watchlists.sql。新表以 (line_user_id,id) 分隔使用者，啟用 RLS，anon／authenticated 不可直接存取；service-role key 僅供服務端。
2. 部署新版 supabase/functions/user-settings/（包含 watchlists.ts）。沿用 LINE ID token 驗證後處理 watchlists.list/save/delete，所有操作限定 token 對應的使用者。
3. 驗證現有 LINE channel／LIFF 可從 Atlas 取得 token。一般瀏覽器仍可使用本機清單；原均線參數可從控制台的 LINE 入口開啟。
4. 設定 GitHub repository variable ATLAS_WATCHLIST_SYNC_ENABLED=1，確認 SUPABASE_URL、SUPABASE_SERVICE_ROLE_KEY、SUPABASE_USER_ID secrets。Atlas workflow 的 refresh:watchlists 使用同一套篩選器記錄每日入選者，不發送通知、不推進上次開啟基準。
5. 部署新版既有 Python 突破服務，設定其 ATLAS_WATCHLIST_SYNC_ENABLED=1 環境變數。只讀指定使用者已同步、啟用的清單；來源未就緒／無效／過舊時保留警示狀態。
6. 真帳號驗收：兩裝置同步、版本衝突、到期 token、帳號隔離、關閉監控及實際通知。這些需要部署與登入，不能由 mock 測試宣稱完成。

### 固定個股參數的部署順序（本次未執行正式部署）

1. 先更新使用 main.py 的每日報告工作與 Python 突破監控／查詢服務。新版本明確排除 ungrouped 的監控資格，只對已在分組／啟用監控清單中的股票套用同帳號固定參數。舊版突破程式會推斷未知組別為監控群組，因此不可先開放建立固定參數。
2. 先套用 `supabase/manual/stocks_fixed_parameters.sql`：正式環境另有 (line_user_id,ticker) 唯一限制，會讓 ungrouped 記錄與同股票的分組記錄互相阻擋；改為只限制非 ungrouped 記錄的部分唯一索引。再部署新版 user-settings。沿用 stocks 表與既有 (line_user_id,ticker,group_id) 唯一索引，不需要新表。
3. 確認所有消費者都已更新後，才在 Edge Function 設定 ATLAS_FIXED_PARAMETERS_ENABLED=1。未啟用時保存固定參數回 503，介面保留輸入並顯示原因；原有分組參數不受影響。
4. 真帳號驗收「保存 → 出榜／刪清單 → 仍保留 → 再入榜／加分組 → 使用固定參數」，並確認無分組記錄不單獨產生報告或提醒。關閉開關只阻止新增／修改固定參數，已部署消費者仍會正確排除其獨立監控資格。

已連結雲端的清單在開啟時讀取每日紀錄，再更新開啟基準；寫入／刪除以 updated_at 條件更新，衝突回 409，保留本機資料。同一清單的設定與開啟紀錄共用寫入佇列；回應只前進雲端版本，不覆蓋等待期間新增的本機修改。新表或函式未部署時顯示錯誤，不宣稱已同步。

本機模式只計已觀察日期，不能回推未開啟期間的連續天數。每日收集啟用後才有跨日紀錄；收集器從 SPY／加權指數 CSV 取得上一交易日，處理假日連續性；來源日曆缺少時保守計算相鄰平日，不猜缺少的觀察。

## 驗證方式

### 參數控制台後續修改的本機結果（2026-10-20）

- clean npm ci、typecheck、lint、normal build 與 Pages build 通過。
- Vitest：77 files／918 tests 通過；實際 user-settings handler 與寫入佇列共 13 tests 於最後修正後另重跑通過。
- Python 完整 suite：1,347 tests 通過，涵蓋固定參數的帳號隔離、無分組不監控、重新入榜套用，以及實際每日報告 loader。
- 桌面／手機 Chromium：控制台與原整合 24 tests 通過；圖表／指標／畫線／提醒／備份 8 tests 通過。兩種尺寸的編輯框與清單截圖已檢查。
- 第一次補測發現開啟紀錄失敗訊息會蓋過設定失敗訊息，已統一為自動同步未完成並明示本機保留，重跑通過。
- 本機仍無 WebKit binary；新的 PR workflow 包含 parameter-console.spec.ts，待 GitHub runner 驗證。正式 Supabase／LINE 帳號驗收與服務啟用未執行。

npm test、npm run lint、npm run build、npm run build:pages；Playwright unified-console／financials／screener-filters／pe-river／themes。整合測試包含 390×280 視窗中的套用按鈕、多清單、開啟差異、同日冪等、監控開關、原控制台 API 與未部署錯誤。

tests/test_atlas_watchlists.py 使用假 Supabase 回應，不發送通知。PR verification workflow 只讀 repository，不部署、不合併、不帶 production secrets；GitHub runner 另執行 iPhone WebKit。

### 本機結果（2026-10-20）

- npm ci、typecheck、lint、normal build、Pages build 通過。
- Vitest：76 files／907 tests 通過。
- Python 完整 suite：1,344 tests 通過；監控整合的 6 tests 另於最後修正後重跑通過。
- 桌面／手機 Chromium：研究頁與整合 95 passed、1 expected skip（桌面無手機 tab bar）；原圖表／提醒／備份回歸 8 passed。整合 16 cases 另重跑通過。
- 每日收集命令在未啟用模式正常略過，沒有連線資料庫或發送通知。
- 本機無 Playwright WebKit binary，下載被環境網路政策拒絕（Domain forbidden）；WebKit 交由新增 PR workflow 驗證。未宣稱實體 Safari 或獨立 reviewer 驗收。
