# Atlas 持股匯入與損益（PR A）

入口在「自選」既有頁面上方的「清單｜持股」。預設仍開清單，不改底部導覽、帳號區塊、登入流程或行情來源。此文件說明 PR A；PR B 在 #199 合併後另加入 MEXC 金鑰／自動同步，設定方式見 [MEXC 同步文件](ATLAS_MEXC_SYNC.md)，仍需維護者手動啟用。

## 使用方式

1. 新增帳戶：國泰、永豐金、凱基、口袋、MEXC 或 Firstrade。可自訂名稱；市場與成本幣別依帳戶類型固定為 TW/TWD、US/USD、CRYPTO/USDT。
2. 上傳 CSV／XLSX，或從券商複製含欄名的庫存表格貼上。原始檔、貼上文字、備註及不需要的欄位只留在瀏覽器記憶體，不上傳、不保存檔名。
3. 自動辨識代號、數量、均價／總成本；未知欄名可指定對應，並選股／張。總成本除以實際股數得到每股均價。台股透過 universe 與官方目錄辨識 TW/TWO，不預設上市。目錄查無或有歧義時須修正／略過。
4. 預覽逐列修正或略過。重複代號不得直接保存；空資料須勾選明確清空確認。確認後**整份取代該帳戶**，不重複加總，其他帳戶不變。手動編輯沿用此預覽修正流程。
5. 同帳戶且欄名指紋相同時套用上次對應。只保存欄索引、單位及 SHA-256 指紋，沒有原始欄名／內容。

CSV 支援引號、跳行、UTF-8、Big5、帶 BOM 的 UTF-16、全形數字；貼上支援 Tab、兩個以上空白與逗號。千分位逗號可以是引號儲存格或 Tab 分隔；未加引號且有多種合理切分的 CSV 會標示錯誤，避免猜錯數量／均價。檔案上限 8 MB、解壓宣告大小 32 MB、20,000 列、128 欄；XLS／巨集檔不接受，請另存 XLSX 或貼上。解析器固定 `read-excel-file@9.3.13`（MIT），數值計算固定 `decimal.js@10.6.0`（MIT）；新依賴與其相依套件在開發時未列於 npm audit 已知漏洞。專案既有相依套件的告警不屬於這次新增套件。

## 匯出格式證據與測試資料

- [凱基官方 0560 證券庫存查詢](https://event.kgi.com.tw/userguide/htshelp/service/0560.htm) 確認有 Excel 匯出，並明示股／張與普通、融資、融券等欄位。其[官方舊版畫面](https://event.kgi.com.tw/userguide/htshelp/images/GTS/0560.jpg)只有庫存及交易數量，**沒有均價**，不能宣稱任意凱基版本一律能自動算成本。`kgi-inventory.xlsx` 用此畫面的可見欄名、完全虛構的帳號／數字建立；將多層標題展平，不是使用者檔案。測試確認可上傳、指定股／張、成本缺漏保持紅字，補正後才保存。實際使用宜複製含買進均價的庫存／未實現損益表；不要把融資、融券、未成交委託或昨日餘額當成普通現股目前股數。不同版本可用欄位對應與預覽核對。
- [Firstrade 官方匯出說明](https://help.firstrade.info/en/articles/9251827-how-do-i-export-to-a-csv-file)：Accounts → Tax Center → Download Account Information → Excel CSV Files，下載的是指定期間的歷史交易，並非持股快照。官方文字沒有公開完整 CSV 標頭；測試採用公開匯出範例可核對的 `Symbol,Quantity,Price,Action,Description,TradeDate,SettledDate,Interest,Amount,Commission,Fee,CUSIP,RecordType` 欄序，所有資料均為自建假交易。[欄位範例來源](https://forum.portfolio-performance.info/t/pdf-import-von-firstrade-securities-inc/28276)。

Firstrade 依 TradeDate 排序（同日沿用原檔列順序，若缺少成交時間，須核對當日先後及成本），BUY 增加成本、SELL 按平均成本扣除部位，全部賣出後不保留零股數。買進有負數 Amount 時採淨付出成本，不重複加 Commission/Fee；未提供 Amount 才以股數 × 價格＋費用計算。賣出股數超過已知庫存、無效日期、不支援的空單／選擇權會阻擋該部位並列出問題。股利／分割／移轉等非一般買賣列顯示於略過清單，必須核對確認；不推估期初庫存，也不自動假定拆股／再投資無影響。若匯出期間不完整，應改用實際庫存表或在預覽修正。

## 報價、損益與限制

- 台美股使用原有 MarketDataProvider 的真實日 K，忽略 simulated；以最新收盤價及前一交易日收盤計算市值、帳上未實現損益與「今日損益」。顯示的是報價資料日的損益，不是自動判定持股增減後的已實現報酬。價格來源與日期逐檔顯示，不是即時券商報價。
- 加密貨幣使用既有 world-assets 的明確 USDT 現貨快照與相符日 K，目前可取得 BTC/USDT；其他幣沒有來源即顯示「無報價」。BTC 點擊開既有原物料圖，台美股開既有個股圖；未支援的加密商品不建立假的個股圖。
- 數量與均價以十進位字串儲存（最多 15 位整數、18 位小數），以 80 位精度十進位計算；平均成本法的循環小數保存時四捨五入至 18 位。台股數量顯示張、修正欄位使用股；美股顯示股、加密顯示幣。
- 每帳戶、每市場按幣別小計。總計依既有 world-assets 的明確 USD/TWD 匯率換算並顯示日期；沒有 USDT/TWD 時，USDT 小計分列，台幣總計標示「可換算部分」。**不將 USDT 當作 USD，也不把無報價當作零元完整總計。**
- 台股賣出成本預設關：0.1425% × 折扣＋0.3% 證交稅，官方目錄標記 ETF 為 0.1%。此為使用者指定的簡化試算，不涵蓋最低手續費、日沖、稅率減免或特殊商品；未知 ETF 分類時按普通股試算，券商實收為準。已知缺少 ETF 目錄時不應將試算當成精確稅額。
- 買進均價應包含取得成本；匯入快照不再重複加買進費用。零成本部位不顯示無限大的損益百分比。

## 儲存與個資邊界

`auth/storage.ts` 是既有授權來源，沒有修改登入檔。none／訪客使用 userStorage 的帳號脈絡記憶體，明示「重新開啟就會消失」；device 使用既有帳號與模式隔離的 userStorage。cloud 只用 RAM 讀取快取＋Supabase，不在 localStorage／IndexedDB 存持股副本，沒有離線寫入佇列或失敗轉本機保存。授權／帳號改變會換 store、卸載匯入畫面，舊請求不得寫入新帳號。

只有驗證後的資料模型會以固定 URL 的 POST 上傳雲端；原始檔、原文、額外欄位與解析錯誤細節不傳送。持股數量、成本、帳戶名稱不進 URL、瀏覽器／函式日誌或 analytics；報價只傳公開商品代號，圖表導航也只有公開代號。持股分支攔截資料庫錯誤，不送到原有通用 error logger。所有持股回應 `Cache-Control: no-store`。不發通知。

### 雲端資料契約

新表 `atlas_holding_accounts`、`atlas_holdings`，RLS 啟用，public/anon/authenticated 全部撤銷，只有 service_role 可存取。`atlas_holdings_action` 是 SECURITY INVOKER、空 search_path、service_role-only 的 RPC。每次 user-settings 請求先通過既有 `requireCloudStorage`；owner 永遠來自驗證結果，不採用請求中的 userId／owner_id。列、帳戶、刪除及取代全部以 owner 篩選。

上限：每人 30 帳戶、2,000 持股，每帳戶 1,000 持股。每人交易級 advisory lock 使並行操作不能繞過上限；每帳戶 revision 做 compare-and-swap，舊裝置版本回 409，須重新載入。取代在單一交易中先驗證整份資料，再刪除該帳戶舊持股並寫入新資料；例外回滾。網路中斷可能無法得知是否已提交，UI 要求重新載入確認，不能保證請求逾時就未寫入。

## 維護者部署順序（本 PR 不執行）

1. 先在可丟棄環境／備份後由維護者檢閱並**手動套用** `supabase/manual/atlas_holdings.sql`。可重複套用；只新增持股表／RPC 及其授權，不修改行情、財務、K 線封存或現有帳號授權表。確認 anon/authenticated 無權直接讀寫表或執行 RPC。
2. 維護者再部署更新後的 `user-settings`（包含相鄰 `holdings.ts`）。沿用目前 LINE／device-session 驗證與儲存授權，不新增機密、不更改 storage grant。手動驗證 cloud 可存、none/device 回 403、不同帳號互不可讀、舊 revision 回 409，以及整份取代／筆數上限。
3. 最後依既有流程發佈前端。若先開前端而服務未就緒，雲端持股會明確報錯並禁止寫入，不影響原有自選／行情。device／訪客匯入不依賴新表。
4. 本 PR 不執行 SQL、不部署函式／前端、不合併、不變更任何使用者持股、不註冊通知、不接 MEXC API。PR B 另外處理只讀金鑰加密與同步。

## 驗證

unit 測試涵蓋解析、格式／數字上限、Firstrade 成本、原子取代、損益、匯率與費用、儲存脈絡及真實 user-settings handler 的授權／owner／上限／錯誤去識別。瀏覽器測試覆蓋 CSV/XLSX、貼上、對應、預覽修正／略過、清單切換、device 保存、訪客清空、cloud 只送正規化資料、缺少服務、缺報價／匯率、BTC 與手機版面。

按使用者限制未執行新 SQL；伺服器測試使用符合現有 storage-access-handler 模式的資料庫替身，**不是實際 PostgreSQL 的 RLS／鎖效力驗證**。維護者手動套用之前仍須完成上述資料庫驗證。瀏覽器 WebKit 不是實體 iPhone 或券商原始檔完整相容性認證。實際通過數量與命令以 PR 說明為準。
