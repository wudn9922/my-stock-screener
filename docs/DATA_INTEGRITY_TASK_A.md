# Task A：K 線、台灣金融業淨收益與逐股資料日

本次從 `main` 的 `a69e903e8` 建立分支，依 [2026-10-22 核對報告](report/data-reality-audit-2026-10-22.md) 修正 Task A。所有修正僅交付 PR，沒有合併或正式部署。

## 實際資料修正

### K 線

| 股票／日期 | 修正 | 來源 |
| --- | --- | --- |
| 3555.TWO，2026-10-22 | 換成 TPEx 完整日報價，Low 146.5 → 145；官方成交股數 172,291 | TPEx 當日報表 |
| 4133.TW，2026-10-22 | 換成 TWSE 完整日報價，Low 約 20.65 → 20.60；官方成交股數 75,057 | TWSE 當日報表 |
| 7825.TWO，2026-08-28 | 原始 CSV 列原樣保留，列入隔離；沒有自行夾限或猜測替代價格 | Yahoo 重抓仍不合理，沒有可獨立驗證的正確替代值 |

逐欄舊值／新值、實際來源 URL、抓取時間及隔離原因保存在 [kline-corrections.json](report/kline-corrections.json)。兩筆官方替換也包含官方成交股數；其餘歷史成交量沒有改成另一來源口徑。

全量重新掃描 **2,526 份 CSV、595,593 根 K 線**，**未隔離的 OHLC 違規為 0**，只有指定的 7825 原始列保留為隔離證據。詳見 [掃描結果](report/kline-integrity-verification.json)。兩份改動的股票 CSV 都只替換指定日期的一行，其他原始行與精度保留。

Brent 已重新抓取 Yahoo 原始回應：10/20 與 10/21 的成交量仍同為 50,349；該回應另有尚在交易時段的 10/22 日 K。程式排除 10/22，保留已完成的 10/21。成交量相同不能單獨證明半根 K。原始時間戳、交易時段與 SHA-256 摘錄在 [驗證紀錄](report/task-a-verification.json)。

### 台灣金融業財報

用目前官方產業名單與既有 `financial-sector-format` 旗標的聯集選出 **86 檔**，逐股重新抓取 FinMind 三張報表，共 **258 份來源輸入**，範圍從 2015 年開始；來源未提供的既有期間也保留。

相對分支起點，共 **7 檔、48 個財報數值**修正，全部為營收／淨收益：**年度 22 個、季度 26 個**。最終變更沒有刪除財報檔案、期間，或把數值清空。逐筆紀錄、來源 URL／取得時間／輸入雜湊及品質旗標前後結果保存在 [tw-financial-sector-corrections.json](report/tw-financial-sector-corrections.json)。

| 股票 | 改動欄位數 |
| --- | ---: |
| 2881.TW | 2 |
| 2883.TW | 4 |
| 5864.TWO | 10 |
| 6015.TWO | 5 |
| 6020.TWO | 15 |
| 6021.TWO | 4 |
| 6026.TWO | 8 |

2883 的 2025 Q2 為 **−357,222,000 TWD**；2025 年度四季加總為 **64,304,206,000 TWD**。銀行／金控的「淨收益」、證券業的「收益」及淨利息收入組合均可為負；一般「營業收入」仍採原有非負有效性規則。明確產生的來源值（包含負值及刻意置為 null 的無效來源）不會被舊值回填。

重新計算年度加總、品質旗標與成長檔後，`growth.json` 有 **4 組季度營收成長序列**變動，完整陣列舊值／新值同樣在修正紀錄中。未改動 EPS 或淨利數值。往後 `write_stock()` 的逐欄來源修正也追加於各檔 `sourceCorrections`，重跑不重複記錄。

### 全市場逐股資料日

`record_to_row()`、欄位清單及前端解析均保留個股 `asOf`。市場 `asOf` 仍是各列日期的眾數，RS 百分位也依同一資料日分組計算。

本次重建保留原來全部 **7,317 檔**股票：

| 市場 | 市場資料日 | 各列日期分布 |
| --- | --- | --- |
| TW，1,979 檔 | 2026-10-22 | 1,946 檔 10/22；31 檔 10/21；2 檔 10/20 |
| US，5,338 檔 | 2026-10-21 | 5,338 檔 10/21 |

台股以可用的永久 CSV 重算；缺少本地 CSV 的 8047 使用重新取得的 Yahoo 日 K。美股以已發布且收盤吻合的帶日期研究紀錄核對，NANO 另以 Yahoo 原始日 K 確認。個股資料日是來源日 K 的觀測日期，零成交列不表示當天必有實際成交。歷史基準使用 Yahoo 指數，10/22 另使用官方加權指數收盤序列，沒有為此補造 OHLC。詳見 [日期重建紀錄](report/universe-date-rebuild.json)。

選股列日期與市場不同時直接顯示日期；價格、量比、RS 等數值排序先按日期分組；漲跌、成交量、量比與 RS 條件只比較市場資料日。介面也明示這個範圍，避免把不同交易日混在一起排名。舊格式可讀，明確未知的日期保持未知。

## 寫入、讀取與保存規則

- `kline_integrity.py` 統一檢查有限正價格及 `Low <= min(Open,Close) <= max(Open,Close) <= High`。最新已完成列有問題時最多重抓一次；台股仍不合理時再取日期／代號驗證過的官方報價，其他無法確認的輸入拒收並記錄。
- 每日掃描、全市場快取、世界 K 線及回補路徑均接上驗證。全市場解析器不再用 `max/min` 把錯誤高低價夾成看似合理的價格。
- 股票／官方序列讀取、市場寬度與突破統計、研究指標、Yahoo 正規化、靜態來源與離線快取都排除隔離列。無法拆解的原生週／月 K 若涵蓋隔離日期，保守排除整個聚合列。
- 永久 CSV 寫入合併原有日期，保留隔離原列與未改動行。世界 CSV 發布拒絕刪除舊日期，發布端再次驗證 OHLC。財報不再依顯示視窗裁掉舊期間或因下市刪檔。
- 隔離／修正紀錄留在 `main` 的永久 records；研究 artifact 帶回並合併修正事件，並行發布保留事件聯集。產物恢復後的 PR CI 會疊回這個 PR 修正的資料。

## 驗證與重跑

實際測試結果、資料筆數與保留檢查集中在 [task-a-verification.json](report/task-a-verification.json)。本機執行範圍包括：

- 完整 Python suite（含重抓、官方備援、隔離、永久保存、金融業負值與逐列日期 round trip）。
- Atlas `npm ci`、`npm test`、typecheck、lint、production build、Pages build。
- **102 項**網站瀏覽器回歸：日期 UI、全市場選股、篩選器、財報與可畫線研究圖，涵蓋桌面 Chromium、手機 Chromium、iPhone WebKit。
- **6 項**畫線／週期隔離瀏覽器回歸，同樣涵蓋上述三個專案。

WebKit 在本環境使用解壓於 `/tmp` 的系統庫執行，未變更系統安裝；這是 Playwright 裝置模擬，不是實體 iPhone 驗證。由同一工程實作者自我審查，沒有宣稱獨立審查簽核。遠端 CI 狀態以此 PR 的 checks 為準，不預先寫成成功。

```bash
python scripts/audit_kline_integrity.py
python scripts/rebuild_tw_financial_sector.py --fetch
python scripts/rebuild_tw_financial_sector.py --preview
python scripts/rebuild_tw_financial_sector.py --apply
# 來源快取位於 .cache/task-a-financials；每筆來源 URL 與雜湊在修正紀錄。
cp docs/report/kline-corrections.json /tmp/test-kline-corrections.json
KLINE_CORRECTIONS_LOG=/tmp/test-kline-corrections.json python -m unittest discover -s tests
cd atlas
npm ci
npm test
npm run typecheck
npm run lint
npm run build
VITE_PUBLIC_BASE=/my-stock-screener/atlas/ npm run build:pages
```
