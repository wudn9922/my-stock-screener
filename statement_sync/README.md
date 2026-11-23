# Email 對帳單同步

券商寄到 Gmail 的 PDF 對帳單 → Apps Script（每天一次）→ Cloud Run `statement-sync` 解析 → Supabase 保存成交並套用到 Atlas 持股帳戶。

- **目前支援**：國泰證券「客戶日買賣報告書」的海外股票（美股）成交；永豐金證券「當日成交回報」的台股現股成交與庫存明細。凱基、月對帳單、融資融券要等收到樣本才加。
- **讀不懂就不寫**：每筆成交都要通過報告本身的算式（股數 × 價格 ≈ 成交金額；成交金額 ± 手續費 ± 稅 = 應收付；小計相符）。不符、出現未支援的區段或市場，整份不寫入，網站顯示「無法辨識／尚未支援」，信不加標籤，之後會再試。
- **只算一次**：成交以「報告日:成交序號」為鍵，重寄同一封信不會重複計算。持股寫入和「已套用」標記在同一個資料庫交易內完成。
- **套用規則**：買進加股數，成本含手續費與稅；賣出減股數，平均成本不變；賣出超過帳上股數就暫停套用並在網站提示。配股、分割、期初持股用「修正持股」調整，之後的成交會加在修正後的持股上。
- **永豐庫存核對**：永豐的回報附完整庫存（集保＋融資）。套用當天成交後，帳戶股數會設成庫存的數字：已有成本的股票總成本不變（配股會拉低均價），沒有成本資料的股票均價先記 0，網站會提示用「修正持股」補上；不在庫存裡的股票會移除。比最新已套用庫存更舊的回報只標記已處理，不會倒退。台股代號用網站的上市櫃目錄換成 `.TW`／`.TWO`。
- **保存內容**：只存解析後的成交、處理結果與遮蔽後的帳號（例如 D3\*\*\*00）。PDF、密碼、信件內容都不保存；服務日誌只記結果代碼與筆數。

## 一次性設定（維護者）

1. **Supabase**：[`supabase/manual/atlas_statement_sync.sql`](../supabase/manual/atlas_statement_sync.sql) 由「Deploy Supabase Edge Functions」workflow 在部署 `user-settings` 前自動套用（可重複執行），不必手動跑。
2. **Secret Manager**（同一個 GCP 專案）：
   - `statement-shared-secret`：至少 32 字元的隨機字串（Cloud Shell：`openssl rand -hex 32`）。
   - `statement-pdf-passwords`：PDF 開啟密碼，一行一個（不同券商密碼不同時各一行）。
   - 讓 Cloud Run 執行身分（沿用 `SINOPAC_RUNTIME_SA`，或另設 `STATEMENT_RUNTIME_SA`）可以讀這兩個 secret，以及既有的 `sinopac-supabase`。
3. **GitHub repository variables**：設定 `STATEMENT_SYNC_ENABLED=true`，再手動執行一次「Deploy statement sync」workflow。輸出的網址就是服務位址。其餘沿用既有的 `GCP_*`、`CLOUD_RUN_BREAKOUT_IMAGE`、`SINOPAC_SUPABASE_URL`、`ADMIN_LINE_USER_ID`。
   第一次部署後，擁有者在 Cloud Shell 執行一次 `gcloud run services update statement-sync --region us-central1 --no-invoker-iam-check`，讓 Apps Script 不需 Google 身分驗證即可呼叫（服務仍以 `X-Statement-Secret` 驗證）。部署帳號沒有修改存取權限的權限，之後的部署會保留這個設定。
4. **Gmail Apps Script**：到 <https://script.google.com> 建新專案，貼上 [`apps_script/Code.gs`](apps_script/Code.gs)。在「專案設定 → 指令碼屬性」加入 `STATEMENT_URL`（上一步的網址）與 `STATEMENT_SECRET`（同 `statement-shared-secret`），執行一次 `syncStatements` 並授權，再執行一次 `installDailyTrigger`。券商寄件網域由服務的 `/senders` 提供，之後新增券商不必再改這份程式。
5. **Atlas**：持股 → 新增帳戶 → 新增方式「Email 對帳單」→ 國泰證券（美股）或永豐證券（台股）→ 建立並連結。已收到的成交會立刻套用。一家券商只能連結一個帳戶；連結失敗時剛建立的空帳戶會自動移除。刪除已連結的帳戶會同時取消連結。

## 處理結果代碼

| 代碼 | 意思 | 信件會再送嗎 |
|---|---|---|
| ok | 已保存並套用 | 否 |
| no-account | 已保存，尚未連結帳戶；連結時套用 | 否 |
| oversell | 已保存，賣出超過帳上股數，暫停套用 | 否（修正持股後按「立即套用」） |
| password | PDF 密碼不對 | 是 |
| unrecognized / unsupported | 版面或內容尚未支援 | 是（更新解析器後自動補上） |
| ignored | 不是對帳單 | 否 |

## 資料異常時怎麼處理

- **修正持股**。遇到配股、分割、要補期初持股，或看到 `oversell`（賣出股數超過帳上持股）時，在 Atlas「持股」選擇該帳戶，按「修正持股」，調整股數與成本。缺少股票時按「新增修正列」。若因 `oversell` 暫停，請補上該檔在那筆賣出前的持股。按「用這份資料取代此帳戶持股」後，之後套用的成交會以修正後的持股為基礎加減。
- **立即套用**。修正 `oversell` 後，或看到「持股剛被修改」時，在 Atlas「持股」選擇已連結的帳戶，到「Email 對帳單同步」按「立即套用」。系統會以目前持股按日期重試尚未套用的成交，成功時顯示套用筆數或「沒有待套用的成交」。若仍有賣出股數超過持股，這批成交會繼續等待套用。
- **取消連結**。想停止自動更新該帳戶時，在 Atlas「持股」選擇該帳戶，到「Email 對帳單同步」按「取消連結」並確認。原帳戶和它的持股會保留，之後收到的成交仍會保存。要恢復同步，從「持股 → 新增帳戶」選「Email 對帳單」，選國泰證券（美股）並按「建立並連結」。目前只能透過新增帳戶恢復同步。新帳戶從空持股開始，連結時會立即嘗試套用已保存但尚未套用的成交，過去已套用的成交不會再加進來。請先核對套用結果，再用「修正持股」把目前的持股補進新帳戶，讓之後的成交以正確的持股為基礎加減。若不再需要原帳戶，在「持股」選擇原帳戶，按「刪除帳戶」並確認，避免同一批持股在兩個帳戶都被計入。
- **`password`**。在已連結帳戶的「Email 對帳單同步」看到「PDF 密碼不正確，未讀取」時，請確認 PDF 的正確開啟密碼，由維護者更新 Secret Manager 的 `statement-pdf-passwords`，並讓 Cloud Run 服務載入新密碼。Atlas 沒有輸入 PDF 密碼的按鈕。Gmail Apps Script 會在後續排程自動重送仍在搜尋範圍內的信件（最近 60 天），解析成功後才會保存成交並嘗試套用。
- **`unrecognized`／`unsupported`**。在同一區看到「內容無法完整辨識，未更新持股」或「尚未支援此券商或版面」時，請把錯誤提示告知維護者，由維護者確認報告格式並更新解析器。Atlas 沒有重跑 PDF 解析的按鈕。Gmail Apps Script 會自動重送仍在搜尋範圍內的信件（最近 60 天），能完整解析後才會保存成交並嘗試套用。
- **某筆成交解析錯誤**。目前無法在 Atlas 更正已保存的單筆成交。「修正持股」只會更新帳戶庫存，原成交紀錄仍會保留。
