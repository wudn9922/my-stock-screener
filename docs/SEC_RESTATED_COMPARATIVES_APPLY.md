# Task B：套用已審閱的 SEC 重編比較期

本 PR 接續 Task A，以最新 main `53d705a64911652671208fde8a7c83ca30f875a4` 為基準，套用 #153 已合併的 dry run。套用收據記錄的 Task A 基準 `693d585c7` 與此 main 的財報／growth／Q4 資料相同。沒有合併或部署本 PR。

## 寫入結果

| 項目 | 結果 |
| --- | ---: |
| 有財報數值變更的股票 | 580 |
| 年度期間 | 1,172 |
| 另有數值變更的季度期間 | 36 |
| 數值變更 | 2,151 |
| 設為 null 的值／Q4 EPS | 0 / 0 |
| 額外數值變更／移除品質旗標 | 0 / 0 |
| 重算後有變動的 growth 列 | 219，全為本次股票 |
| 比對的永久財報檔案 | 6,947 |
| 未變動的 Q4 答案集、分類、ledger 檔案 | 9 |

Q4 EPS 只套用已審閱的 KVHI FY2013 Q4 基本、稀釋兩值：**−0.02 → 0.36**，同一重編 accession `0001007587-16-000027`、filed `2016-03-14`。沒有重跑 Q4 acceptance、分類或跨申報基準推導。`end`、`fy`、`fq`、`epsQuality`、拆股、ADR 及既有品質旗標不變。

580 檔有 `restated`；其中 518 檔有 `mixed-basis`，573 檔有 `quarterly-as-reported`。既有旗標與 revision 歷史完整保留、順序不變。每個實際變動的欄位，都有 `quality.revisions` 的 `kind: "restated"` 舊／新值、accession、filed 及 seen；沒有為缺少的重編欄位補造數字。

逐筆內容與證明：

- [原 dry run](report/sec-restated-comparatives.json)：原檔未改，SHA-256 為 `13471c443f0b1d64431c065c5289412893c30b74206f562775f6b829f2a99997`。
- [套用紀錄](report/sec-restated-comparatives-apply.json)：2,151 個實際變更、逐檔前後雜湊、570 份不同 CIK 的 SEC 來源 URL／取得時間／雜湊及 219 列 growth 舊／新陣列。
- [獨立寫入驗證](report/sec-restated-comparatives-verification.json)：從 Git 基準重新取出所有財報與受保護檔案，逐欄比對 `(symbol, period, end, field, old, new)`；另列 9 個受保護檔案的實際 SHA-256。

## 原始 SEC 期末日

`secEnd` 是與 `end` 平行的 metadata 欄，不參與期別合併、排序或 fiscal labels。新取得的 US GAAP／IFRS SEC 列、套用的重編列都保留原始 SEC 日期；季度保存自己的日期，不使用年度 basis 的期末日。

另外只為 NVDA 補上日期 metadata，所以實際寫入 **581 份**財報：580 份數值修正，以及 1 份只有 metadata 的 NVDA。NVDA FY2026 保持 `end=2026-01-31`，另存 `secEnd=2026-01-25`；EPS、營收、其他值和期別對齊都未改。此次共有 1,331 個非空 `secEnd`。無法確認的日期不自行推測。

財報表格在兩個日期不同時顯示 `SEC YYYY-MM-DD`。品質面板可查看混合基準、季度保留說明與原值修訂紀錄。

## 套用與驗證流程

`apply_sec_restatements.py` 是離線、一次性套用入口。它固定檢查已審閱報告的 SHA-256、所有目標財報的原始 SHA-256，以及 SEC 快取的 CIK 與來源雜湊。先準備、驗證整批候選，再寫入；來源若產生任何超出報告的數值差異就拒絕整批。寫入失敗會回復已寫入檔案。重複套用會因原始雜湊不符而拒絕，應改用驗證指令。

來源取得是獨立的 `fetch_sec_restatement_sources.py`，只寫忽略追蹤的快取，不寫財報：使用既有 identified User-Agent、每秒最多 4 次、12,000 次上限與遇首個 403 即停止的規則。本次取得 570 份不同 CIK 來源，無 403；套用階段的 SEC 請求為 **0**。

```bash
# 在原始財報仍與 dry run 相同的 checkout，先取得來源。
python atlas/scripts/fetch_sec_restatement_sources.py --cache-dir .cache/task-b-sec

# 只檢查，不寫永久紀錄。
python atlas/scripts/apply_sec_restatements.py --cache-dir .cache/task-b-sec --workers 4

# 通過整批檢查後才寫入，包含 growth 重算。
python atlas/scripts/apply_sec_restatements.py --apply --cache-dir .cache/task-b-sec --workers 4

# 已套用的本分支可直接重跑這個離線驗證，不需原始 SEC 快取。
python atlas/scripts/apply_sec_restatements.py \
  --verify-against 53d705a64911652671208fde8a7c83ca30f875a4
```

`growth.json` 用同一計算時點與官方來源選擇規則，比較套用前／後結果，只更新此次輸入造成差異的股票；其他市場、其他股票與觀測日老化不會因本次操作改寫。219 列中 `revY` 變動 218 列、`epsY` 7 列、`epsQ` 1 列、`revQ` 1 列、`tY` 2 列（同列可有多欄）。獨立驗證也從 Git 基準重建上述差異，與實際 growth 完整比對。

財報頁的淨利率、ROE、TTM、FCF、YoY 等由 `financialMetrics.ts` 讀原始財報即時計算，沒有另一份靜態數字快取；測試包含 AD 新營收的淨利率，以及 KVHI 同基準 Q4 EPS。Yahoo valuation 與 SEC frames 是獨立來源，沒有改寫為本次 companyfacts 的數字；雲端個人自選股沒有外部寫入。

## 驗證範圍

完整 Python suite、Atlas clean `npm ci`、全部單元測試、typecheck、lint、production build、Pages build；新增瀏覽器回歸驗證 NVDA 日期與 AD 品質／修訂紀錄。實際數量與 CI 狀態列於本 PR 與 [測試收據](report/task-b-verification.json)。

CI 在本 PR 變更套用紀錄時，也會從 PR 基準重新驗證全量財報、growth 與 Q4 受保護資料。獨立驗證讀 Git 基準的檔案清單，因此刪除受保護檔案也會失敗。由主要實作者自我審查，沒有宣稱獨立簽核；WebKit 是 Playwright 模擬，不是實體 iPhone 驗證。
