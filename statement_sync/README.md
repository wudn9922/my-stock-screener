# Email 對帳單同步

券商寄到 Gmail 的 PDF 對帳單 → Apps Script（每天一次）→ Cloud Run `statement-sync` 解析 → Supabase 保存成交並套用到 Atlas 持股帳戶。

- **目前支援**：國泰證券「客戶日買賣報告書」的海外股票（美股）成交。永豐、凱基、月對帳單與台股版面要等收到樣本才加。
- **讀不懂就不寫**：每筆成交都要通過報告本身的算式（股數 × 價格 ≈ 成交金額；成交金額 ± 手續費 ± 稅 = 應收付；小計相符）。不符、出現未支援的區段或市場，整份不寫入，網站顯示「無法辨識／尚未支援」，信不加標籤，之後會再試。
- **只算一次**：成交以「報告日:成交序號」為鍵，重寄同一封信不會重複計算。持股寫入和「已套用」標記在同一個資料庫交易內完成。
- **套用規則**：買進加股數，成本含手續費與稅；賣出減股數，平均成本不變；賣出超過帳上股數就暫停套用並在網站提示。配股、分割、期初持股用「修正持股」調整，之後的成交會加在修正後的持股上。
- **保存內容**：只存解析後的成交、處理結果與遮蔽後的帳號（例如 D3\*\*\*00）。PDF、密碼、信件內容都不保存；服務日誌只記結果代碼與筆數。

## 一次性設定（維護者）

1. **Supabase**：在 SQL Editor 執行 [`supabase/manual/atlas_statement_sync.sql`](../supabase/manual/atlas_statement_sync.sql)，然後重新部署 `user-settings` edge function。
2. **Secret Manager**（同一個 GCP 專案）：
   - `statement-shared-secret`：至少 32 字元的隨機字串（Cloud Shell：`openssl rand -hex 32`）。
   - `statement-pdf-passwords`：PDF 開啟密碼，一行一個（不同券商密碼不同時各一行）。
   - 讓 Cloud Run 執行身分（沿用 `SINOPAC_RUNTIME_SA`，或另設 `STATEMENT_RUNTIME_SA`）可以讀這兩個 secret，以及既有的 `sinopac-supabase`。
3. **GitHub repository variables**：設定 `STATEMENT_SYNC_ENABLED=true`，再手動執行一次「Deploy statement sync」workflow。輸出的網址就是服務位址。其餘沿用既有的 `GCP_*`、`CLOUD_RUN_BREAKOUT_IMAGE`、`SINOPAC_SUPABASE_URL`、`ADMIN_LINE_USER_ID`。
4. **Gmail Apps Script**：到 <https://script.google.com> 建新專案，貼上 [`apps_script/Code.gs`](apps_script/Code.gs)。在「專案設定 → 指令碼屬性」加入 `STATEMENT_URL`（上一步的網址）與 `STATEMENT_SECRET`（同 `statement-shared-secret`），執行一次 `syncStatements` 並授權，再執行一次 `installDailyTrigger`。
5. **Atlas**：持股 → 新增帳戶 → 新增方式「Email 對帳單」→ 國泰證券 → 建立並連結。已收到的成交會立刻套用。

## 處理結果代碼

| 代碼 | 意思 | 信件會再送嗎 |
|---|---|---|
| ok | 已保存並套用 | 否 |
| no-account | 已保存，尚未連結帳戶；連結時套用 | 否 |
| oversell | 已保存，賣出超過帳上股數，暫停套用 | 否（修正持股後按「立即套用」） |
| password | PDF 密碼不對 | 是 |
| unrecognized / unsupported | 版面或內容尚未支援 | 是（更新解析器後自動補上） |
| ignored | 不是對帳單 | 否 |
