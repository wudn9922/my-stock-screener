# FY2021–FY2025 Yahoo 年度缺值回補

取代已關閉、未合併的 #151。基準是包含 #164 SEC 重編比較期的 main
`dc3cceac55bc9b2dfac13cd48e626bea1c219edd`。不建立新期間、不重設來源列標籤。

## 判定與保存

- 只考慮既有年度列的 `fy=2021..2025`；只填 `null`。季度、其他年度、既有非空值、
  EPS（基本／稀釋）和稀釋股數均不改。
- 同期日期沿用 builder 的五日容差，必須一對一；沒有重疊值或日期不唯一就跳過。
- **所有**重疊欄位都比對（包括不會回補的 EPS／股數）。相對於現存值差異大於 2%，
  整期不回補；0 對非 0 也拒絕，負數不轉正。restated／mixed-basis 沒有豁免。
  即使重疊值相符，已明確標記重編／混合基準的期間仍略過（`basis-unverified`）：
  相同數值無法確認缺值欄位所屬的 SEC accession／申報基準。
- `restated` 必須有可驗證的較晚 SEC comparative revision（相同欄位／期末／值、
  新舊 accession 與 filing date）；`unit-mismatch` 要同號比率在非零 10 次方的 2% 內。
  無法證實的差異列為 `unexplained`，不猜測重編。每筆差異保留兩邊數值與證據。
- `annual.fieldSrc` 是與 `end` 平行的逐欄來源 map。填入欄位記 `yahoo`；
  `annual.yahooAnnualBackfill` 保留核准填值，供日常更新維持歷史值。
  頁面來源文字列出年度及欄位，表格與點選圖表的來源標籤也顯示 Yahoo 補值。
- 品質僅呼叫 `build_financials.quality()` 計算；與**全部**舊旗標取聯集，
  舊 revision 原物件、順序及未知代碼都保留。
- 日常 Yahoo 更新對已回補列採非空保護及整期基準否決；EPS／股數不再從該列補入。
  SEC／FinMind 更新仍缺欄位時保留填值及 Yahoo 來源；官方提供該欄時以官方值為準，
  更新逐欄來源並移除該欄的舊 Yahoo 保留值。FinMind 的刻意 null 仍依既有權威規則處理。

## 可重跑指令

來源擷取與寫入分開。固定快照包含所有 Yahoo 重疊欄位，並保留每個查詢的 URL、
參數、擷取時間與原始 payload SHA-256；快照是完整 Yahoo 回應的**解析結果**，不是原始回應存檔。
擷取涵蓋兩個時間窗，任何請求失敗都記入 errors，離線套用拒絕不完整快照。
幣別沿用正式 parser；不同幣別的數值不轉換後混填。

```sh
# 可選：明確重新擷取；這會產生新快照，來源可能隨時間改變。
python atlas/scripts/backfill_yahoo_annual.py --fetch --snapshot /tmp/yahoo-new.json.gz

# 在上述基準的獨立 checkout，使用已提交快照離線預覽／套用。
python atlas/scripts/backfill_yahoo_annual.py --audit /tmp/yahoo-preview.json
python atlas/scripts/backfill_yahoo_annual.py --apply

# 對原始 Git tree 重播規則並驗證實際寫入。
python atlas/scripts/backfill_yahoo_annual.py \
  --verify-against dc3cceac55bc9b2dfac13cd48e626bea1c219edd \
  --proof /tmp/yahoo-proof.json
```

先對整批預檢，再寫入；失敗回復原檔。同一份快照再次套用不改檔案或原始收據。
`growth.json` 用同一時鐘對套用前後各重算一次，只更新受回補影響的列；頁面各比率、FCF、
ROE、成長率則由現值即時計算。獨立驗證檢查所有財報檔案、每一筆 null→value、
完整 metadata、全部舊旗標／revision，以及 growth 重建結果。

Q4 答案集、classification、acceptance ledger 與 SEC 重編 manifest 另做逐位元組比對。
全量非空保護也涵蓋 #164 的重編現值。CI 有獨立 `yahoo-annual-write-proof` job，
當本 PR 變更套用收據時，以 PR head 與固定基準驗證，不依賴 generated branch 的快照版本。

逐筆填值、略過期別與分市場／欄位計數見 `docs/report/yahoo-annual-backfill.json`；
Git tree 證明見 `docs/report/yahoo-annual-backfill-proof.json`。
