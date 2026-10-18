# SEC Q4 Diluted EPS 免費來源 POC

此 POC 用 SEC 免費官方資料檢查 10 檔小型美股、20 個 FY/Q4 期別，目標是判斷原始申報、Financial Statement Data Sets、Notes Data Sets 是否含有 CompanyFacts 之外可直接採用的 GAAP diluted Q4 EPS。結果不會寫入正式 financials 或 production DB。

## 核心結果

- 樣本在 SEC 查詢前，使用 main 的 `docs/report/q4-eps-missing-classification.json`、`docs/report/q4-eps-acceptance-ledger.json` 和 `atlas/scripts/classify_q4_eps_missing.py`，以 seed `20261018` 凍結。source commit：`fccf858d16e773df861e4424880d7f8264d699ff`。
- Main 分類檔有 7,755 個缺漏期別。本次選 8 檔缺漏股票、16 個缺漏期別，另選 PZG/PTN 兩檔 main Grade A 對照、4 個期別。
- Main 分類更新後的樣本在缺漏期別找到 **5/16** 筆 Verified Direct Q4 Diluted EPS：ASTC FY2014 `-0.04`、FGC FY2016 `+0.27`、FGC FY2022 `-0.03`、WRAP FY2022 `-0.09`、ULBI FY2021 `-0.07`。ULBI FY2014 有 `+0.05` per-share 候選，但 SEC 8-K 未確認 basic 或 diluted，未採用。
- 舊版的 6/16 屬於不同的暫存分類與樣本；OFG FY2017/FY2020 在 main 現列為 `derivation_rejected`，不在重凍結樣本。新版加入 PCSA 與 ULBI 取代 OFG/DUKR。舊 6/16 是小型分層樣本，目前版本為 5/16；兩者都不能外推至全市場 7,755 個缺漏期別。
- 8-K / EX-99.1 是本次缺漏列最有效來源，新增 5 個唯一直接值。FSDS、Notes 與 10-K 的直接候選主要用於 PZG/PTN 控制期別；沒有為缺漏列增加已驗證直接值。Notes 有 PZG FY2016 Q4 +0.32 候選，但 main Grade A fact 是 -0.32，符號衝突仍未解。
- PZG/PTN 四個 main Grade A 對照中，三個 SEC as-filed 值與 main 一致，一個（PZG FY2016）未解。PTN 的 `splitFactor=0.0008`：as-filed EPS `+0.06/+0.23` 對應目前資料基準 `+75/+287.5`。
- NMTC FY2017/FY2019 和 TDW FY2018/FY2019 不在 main missing 清單，也沒有 row-level Grade A fact。Ledger 對這四列沒有 blocked、overridden 或 deferred 條目；`verifiedA=5009` 是 aggregate，沒有 per-row accepted-A 清單，不能從總數推斷這些列是 Grade A。
- 期間映射另發現 16 個已確認 local Q4 end 與 SEC 年報 report end 不同，以及 3 個需補充映射的 row。詳情見 `feasibility-report.md`。此 PR 不修改 `docs/report/financials/*.json`。

判定為 **PARTIAL**。剩餘 8 個缺漏期別只有年度/部分季度線索；1 個 diluted/basic 候選未確認；PCSA FY2012 歷史 CIK/申報映射不完整；BNC FY2024 凍結期末與 SEC 年報期末不符。這些情況不能一概解讀成 SEC 沒有披露或 parser 失敗。

## 樣本與 SEC 搜尋

樣本清單、ticker/CIK、缺漏原因、期別、seed 與 hash 在 `sample.json`。重凍結沿用同一 seed 與排序/產業分散規則，先固定樣本，再執行 SEC 來源查詢。6 個原缺漏樣本公司與 12 個 FY/Q4 期別保留；來源資料可依 accession 重用。新加入 PCSA、ULBI、PZG、PTN；移除 DUKR、OFG、NMTC、TDW。

搜尋只處理樣本對應的 SEC 申報窗口：

1. SEC Financial Statement Data Sets（16 個 filing-calendar-quarter ZIP）。
2. SEC Financial Statement and Notes Data Sets（16 個 ZIP）。
3. 樣本公司的 SEC 10-K / 10-K/A 原始文件。
4. 樣本期間附近的 8-K / EX-99.1；圖片附件 OCR 以 URL 與 SHA-256 對應原始文件。

Zip 的 calendar quarter 是申報日期季度，不代表公司的財政季度。Facts 必須再以 CIK、accession、duration/quarter、period end、unit、dimension、GAAP/diluted 語境核對。Annual EPS、basic-only EPS、adjusted EPS 或 FY 減 Q1–Q3 的結果不會作為正式 Q4 值。

PCSA FY2012 無法由目前 CIK submissions 對應申報，列為 E（不完整搜尋），不是 D（完成搜尋但沒有直接披露）。PCSA FY2022 的年報僅找到年度 EPS；8-K 文字及完成的影像 OCR 未找到 Q4 diluted EPS。

## 網路與資料限制

- 只使用 SEC 官方免費端點，User-Agent 由 POC 的 `SecClient` 設定，速率上限 4 requests/second，支援 `Retry-After` 與退避。沒有呼叫 Massive API、沒有下載全市場財報資料、沒有寫入 production data。
- 前一版 POC snapshot 記錄 406 次 request 和 11,190,612,664 bytes；該 bytes 總數含一個 Notes ZIP 的快取 payload，不能視為精確的實際網路傳輸量。從前一版 artifact 到重凍結資料，按各來源可比計數器記錄 162 次新增 request、3,837,738,545 wire bytes。Source A/B 的累積欄位與最近一次 invocation 的 `incremental` 欄位另分開保存；不能把來源累積數再次相加成總量。
- 凍結樣本引用 16 個唯一季度 ZIP，FSDS payload 合計 1,534,782,067 bytes、Notes payload 合計 7,472,961,173 bytes。這些是包含重用回應的 ZIP 檔案大小，不是本次新增下載量，也沒有 commit 進 Git。Source A/B 使用 accession 重用；Source C/D 只取得樣本申報與附近 8-K 文件。
- 各來源的 request、wire bytes、cache hit、HTTP status 與 parse status 分別列在 `results.json` / `source-ledger.jsonl`，避免將舊樣本累積計數和重凍結查詢混為一談。PCSA FY2012 在所有來源表均列為 1 個未完成目標，因目前 CIK submissions 無對應 accession；不是「沒有資料」或網路失敗。
- SEC 收集約在樣本凍結後 21 分鐘完成；報告重建為離線，不另發 SEC request。各來源 request、bytes、cache hit、HTTP status 和 parse status 在 `results.json` / `source-ledger.jsonl` 保存。
- 未把 SEC 原始 ZIP commit。報告與 ledger 保留來源 URL、accession、申報日期、status、大小、hash、parser/OCR 狀態及 adjudication。沒有 API key。

## 重現

查詢資料前重新凍結樣本（需使用目前 main 分類檔，且不要覆蓋已凍結樣本，除非在獨立 POC 測試中明確指定）：

```bash
python scripts/sec_q4_eps_poc.py freeze-sample \
  --seed 20261018 \
  --source-commit fccf858d16e773df861e4424880d7f8264d699ff \
  --replace
```

目前已保存原始回應與解析結果。只重建報告時不發出 SEC request：

```bash
python scripts/render_sec_q4_eps_poc_report.py
```

執行 POC 測試：

```bash
python -m unittest discover -s tests -p 'test_sec_q4_eps_poc.py' -v
```

## 交付檔案

- `sample.json`：固定樣本、main 分類與 ledger audit、seed 和 hash。
- `results.json`：20 個期別的來源/分類結果、positive-control audit、source success rate、network metrics 與 fiscal mismatch rows。
- `source-ledger.jsonl`：SEC URL/accession 索引、HTTP、bytes/hash、ZIP parser 與 OCR 狀態、判定。
- `feasibility-report.md`：逐 ticker 結果、Verified Direct 明細、來源成功率、未解原因、period mismatches 和結論。
- `official-docs.json`、`filing-map.json`、`source-a.json`、`source-b.json`、`source-c.json`、`source-d.json`、`source-d-ocr.json`、`positive-control-pzg-2017-source.json`：SEC 官方文件與回應 metadata；沒有原始大型 ZIP。

## Review

最終狀態：**PARTIAL**，無剩餘阻擋事項。Sol 6.1 XHigh 唯讀複核已確認來源分母、增量統計、TDW 2017 過渡期證據、樣本與 ledger 核對及 POC-only diff 範圍。
