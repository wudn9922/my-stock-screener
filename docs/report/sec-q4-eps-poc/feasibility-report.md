# SEC Q4 Diluted EPS 免費資料來源 POC

本報告使用 main 的分類檔重新凍結樣本，並重用未改期別的 SEC 回應。先列 10 檔股票與 20 個測試期別。

| Ticker | FY/Q4 | 原本缺漏原因 | SEC ZIP | SEC Notes | 10-K | 8-K | 最終結果 | EPS |
|---|---|---|---|---|---|---|---|---|
| BNC — BNB Standard Corp<br>CIK 0001482541 | FY2014 2014-12-31<br>FY2024 2025-01-31 | q4_net_income_missing | HTTP 200 / parsed<br>parsed 2015q2<br>HTTP 200 / parsed<br>parsed 2025q1 | HTTP 200 / parsed<br>parsed 2015q2<br>HTTP 200 / parsed<br>parsed 2025q1 | HTTP 200 / parsed<br>0001493152-15-001473<br>HTTP 200 / parsed<br>0001641172-25-000913 | HTTP 200 / parsed; OCR hash verified<br>9 windows / 16 docs<br>HTTP 200 / parsed; OCR hash verified<br>3 windows / 9 docs | FY2014: C — 年度 EPS 與部分季度資料可用；10-K/8-K 搜尋範圍未找到可直接採用的 Q4 diluted EPS。<br>FY2024: E — 凍結期末 2025-01-31 與 SEC FY2024 年報期末 2024-12-31 不同，無法把年報當作該凍結期別。 | 2014-12-31: —<br>2025-01-31: — |
| QXL — QUANTUM X LABS INC.<br>CIK 0000797542 | FY2014 2014-12-31<br>FY2019 2019-12-31 | q4_net_income_missing | HTTP 200 / parsed<br>parsed 2015q1<br>HTTP 200 / parsed<br>parsed 2020q1 | HTTP 200 / parsed<br>parsed 2015q1<br>HTTP 200 / parsed<br>parsed 2020q1 | HTTP 200 / parsed<br>0001295345-15-000048<br>HTTP 200 / parsed<br>0001493152-20-004372 | HTTP 200 / parsed; OCR hash verified<br>6 windows / 6 docs<br>HTTP 200 / parsed; OCR hash verified<br>3 windows / 3 docs | FY2014: C — 年報的 Fourth Quarter 表是股價 High/Low，不是 EPS；僅有年度/部分季度 EPS 線索。<br>FY2019: C — 年報的 Fourth Quarter 表是股價 High/Low，不是 EPS；僅有年度/部分季度 EPS 線索。 | 2014-12-31: —<br>2019-12-31: — |
| FGC — FG Communities Holdings Inc.<br>CIK 0001591890 | FY2016 2016-12-31<br>FY2022 2022-12-31 | annual_diluted_eps_missing | HTTP 200 / parsed<br>parsed 2017q1<br>HTTP 200 / parsed<br>parsed 2023q1 | HTTP 200 / parsed<br>parsed 2017q1<br>HTTP 200 / parsed<br>parsed 2023q1 | HTTP 200 / parsed<br>0001387131-17-001422<br>HTTP 200 / parsed<br>0001493152-23-008861 | HTTP 200 / parsed; OCR hash verified<br>4 windows / 7 docs<br>HTTP 200 / parsed; OCR hash verified<br>3 windows / 24 docs | FY2016: A — SEC 8-K EX-99.1 明確列示 GAAP diluted Q4 EPS。<br>FY2022: A — SEC 8-K EX-99.1 明確分開單季和年度欄，GAAP 表及敘述一致。 | 2016-12-31: 0.27<br>2022-12-31: -0.03 |
| WRAP — WRAP TECHNOLOGIES, INC.<br>CIK 0001702924 | FY2017 2017-12-31<br>FY2022 2022-12-31 | annual_diluted_eps_missing | HTTP 200 / parsed<br>parsed 2018q1<br>HTTP 200 / parsed<br>parsed 2023q1,2023q2 | HTTP 200 / parsed<br>parsed 2018q1<br>HTTP 200 / parsed<br>parsed 2023q1,2023q2 | HTTP 200 / parsed<br>0001654954-18-002199<br>HTTP 200 / parsed<br>0001437749-23-005218, 0001437749-23-011737 | HTTP 200 / parsed; OCR hash verified<br>1 windows / 2 docs<br>HTTP 200 / parsed; OCR hash verified<br>5 windows / 8 docs | FY2017: C — 年度/部分季度資料可用；10-K/8-K 搜尋範圍未找到可直接採用的 Q4 diluted EPS。<br>FY2022: A — SEC 8-K EX-99.1 明確列示 GAAP basic-and-diluted Q4 EPS。 | 2017-12-31: —<br>2022-12-31: -0.09 |
| PCSA — Processa Pharmaceuticals, Inc.<br>CIK 0001533743 | FY2012 2012-12-31<br>FY2022 2022-12-31 | q1_q3_incomplete | no target accession linked to this CIK/period<br>target accession unavailable <br>HTTP 200 / parsed<br>parsed 2023q1 | no target accession linked to this CIK/period<br>target accession unavailable <br>HTTP 200 / parsed<br>parsed 2023q1 | no accession linked to current CIK/period<br>無配對申報<br>HTTP 200 / parsed<br>0001493152-23-009730 | historical CIK/filing window unavailable<br>0 windows / 0 docs<br>HTTP 200 / parsed; OCR hash verified<br>6 windows / 89 docs | FY2012: E — 目前 CIK 的 SEC submissions 沒有可連結至 FY2012 的申報；歷史 CIK/公司承接映射不完整，不能判定沒有披露。<br>FY2022: C — 10-K 只找到年度 basic-and-diluted EPS；SEC 8-K 文字與已完成的圖片 OCR 均未找到單季 Q4 EPS。 | 2012-12-31: —<br>2022-12-31: — |
| ULBI — ULTRALIFE CORP<br>CIK 0000875657 | FY2014 2014-12-31<br>FY2021 2021-12-31 | q1_q3_incomplete | HTTP 200 / parsed<br>parsed 2015q1<br>HTTP 200 / parsed<br>parsed 2022q1,2022q2 | HTTP 200 / parsed<br>parsed 2015q1<br>HTTP 200 / parsed<br>parsed 2022q1,2022q2 | HTTP 200 / parsed<br>0000875657-15-000010<br>HTTP 200 / parsed<br>0001437749-22-005572, 0001437749-22-009863 | HTTP 200 / parsed; OCR hash verified<br>3 windows / 3 docs<br>HTTP 200 / parsed; OCR hash verified<br>4 windows / 6 docs | FY2014: B — 8-K 報告 Q4 每股 0.05 美元，但未說明是 basic 或 diluted，故不接受為 diluted EPS。<br>FY2021: A — SEC 8-K EX-99.1 表格明確列示 GAAP Q4 per diluted share。 | 2014-12-31: — (候選 +0.05，未通過驗證)<br>2021-12-31: -0.07 |
| ASTC — ASTROTECH Corp<br>CIK 0001001907 | FY2014 2014-06-30<br>FY2019 2019-06-30 | derivation_rejected | HTTP 200 / parsed<br>parsed 2014q3<br>HTTP 200 / parsed<br>parsed 2019q3 | HTTP 200 / parsed<br>parsed 2014q3<br>HTTP 200 / parsed<br>parsed 2019q3 | HTTP 200 / parsed<br>0001571049-14-004871<br>HTTP 200 / parsed<br>0001564590-19-035928 | HTTP 200 / parsed; OCR hash verified<br>3 windows / 8 docs<br>HTTP 200 / parsed; OCR hash verified<br>2 windows / 3 docs | FY2014: A — SEC 8-K EX-99.1 ‘Fourth Quarter Results’ 明確列示 GAAP diluted EPS。<br>FY2019: C — 有年度 EPS 和財報資料；已查來源範圍未找到直接 Q4 EPS。 | 2014-06-30: -0.04<br>2019-06-30: — |
| IGC — IGC Pharma, Inc.<br>CIK 0001326205 | FY2012 2012-03-31<br>FY2019 2019-03-31 | derivation_rejected | HTTP 200 / parsed<br>parsed 2012q3<br>HTTP 200 / parsed<br>parsed 2019q2 | HTTP 200 / parsed<br>parsed 2012q3<br>HTTP 200 / parsed<br>parsed 2019q2 | HTTP 200 / parsed<br>0001185185-12-001484<br>HTTP 200 / parsed<br>0001185185-19-000890 | HTTP 200 / parsed; OCR hash verified<br>4 windows / 8 docs<br>no 8-K matched the requested search window<br>0 windows / 0 docs | FY2012: C — 年報有年度 diluted EPS 與前一曆年季度 pro forma 表，並非該財年 Q4。<br>FY2019: C — 有年度與部分季度 EPS 線索；已查來源範圍未找到直接 Q4 EPS。 | 2012-03-31: —<br>2019-03-31: — |
| PZG — Paramount Gold Nevada Corp.<br>CIK 0001629210 | FY2016 2016-06-30<br>FY2019 2019-06-30 | main Grade A positive control | HTTP 200 / parsed<br>parsed 2016q3<br>HTTP 200 / parsed<br>parsed 2019q3 | HTTP 200 / parsed<br>parsed 2016q3<br>HTTP 200 / parsed<br>parsed 2019q3 | HTTP 200 / parsed<br>0001564590-16-025274<br>HTTP 200 / parsed<br>0001564590-19-034803 | HTTP 200 / parsed; OCR hash verified<br>6 windows / 12 docs<br>HTTP 200 / parsed; OCR hash verified<br>2 windows / 3 docs | FY2016: B — Notes 與年報季度表列示 +0.32，但 main Grade A fact 為 -0.32；原始表格符號基準有衝突，暫不採用。<br>FY2019: A — SEC 年報季度 EPS fact 為 -0.09，並與 main Grade A as-filed value 一致；原始表格以 net loss 語境列示 0.09。 | 2016-06-30: — (候選 +0.32，未通過驗證)<br>2019-06-30: -0.09 |
| PTN — PALATIN TECHNOLOGIES INC<br>CIK 0000911216 | FY2018 2018-06-30<br>FY2019 2019-06-30 | main Grade A positive control | HTTP 200 / parsed<br>parsed 2018q3,2019q3<br>HTTP 200 / parsed<br>parsed 2019q3 | HTTP 200 / parsed<br>parsed 2018q3,2019q3<br>HTTP 200 / parsed<br>parsed 2019q3 | HTTP 200 / parsed<br>0001654954-18-010150<br>HTTP 200 / parsed<br>0001654954-19-010679 | HTTP 200 / parsed; OCR hash verified<br>1 windows / 2 docs<br>HTTP 200 / parsed; OCR hash verified<br>1 windows / 2 docs | FY2018: A — SEC 年報季度 diluted EPS fact +0.06，與 main Grade A as-filed value 一致。<br>FY2019: A — SEC 年報季度 diluted EPS fact +0.23，與 main Grade A as-filed value 一致。 | 2018-06-30: 0.06<br>2019-06-30: 0.23 |

## 樣本凍結與主要結果

- Source commit：`fccf858d16e773df861e4424880d7f8264d699ff`；seed：`20261018`；分類來源：`docs/report/q4-eps-missing-classification.json`。樣本 SHA-256：`89dcb33135d2abbb0297617bed371233d20b3341a1419504ae6768e36d58caa2`。
- Main 分類總缺漏為 7755 筆；本次固定抽樣 8 檔缺漏股、16 個期別，另 2 檔 Grade A 對照、4 個期別。分層小樣本不可外推至全市場 7,755 筆。
- 重新凍結後，缺漏期別找到 **5/16** 筆直接 Q4 EPS。舊版 **6/16** 是不同樣本的結果：OFG FY2017/FY2020 在 main 分類為 `derivation_rejected` 且不再入樣，PCSA/ULBI 取代舊樣本；本報告不沿用舊分子。無論舊或新比例，都不能外推到 7,755 筆。
- 分類：A=5, B=1, C=8, E=2。C 是僅年度/部分季度證據；B 是候選未通過驗證；E 是日期/身份映射不完整。沒有以年度 EPS 減前三季 EPS 補值。
- 樣本變動：移除 DUKR、OFG、NMTC、TDW；新增 PCSA、ULBI、PZG、PTN。保留的六家公司中 12 個原期別未變；來源回應得以按 accession 重用。OFG FY2017/FY2020 與 DUKR FY2021 均依 main 更新分類。

## Verified Direct Q4 EPS

以下每筆均是 SEC 原始申報內直接披露的單季值；未用年度減前三季推算。

| 股票 / FY | SEC 原始來源 / Accession / 申報日 | Fiscal Quarter / End Date | EPS | 原始標籤與位置 | GAAP / Diluted 判定 | 拆股基準 / 資料庫一致性 |
|---|---|---|---:|---|---|---|
| ASTC FY2014 | [SEC 8-K EX-99.1](https://www.sec.gov/Archives/edgar/data/1001907/000157104914004870/t1401849_ex99-1.htm)<br>`0001571049-14-004870`; filed 2014-09-30 | FY2014 Q4 ended 2014-06-30 | -0.04 | 無 XBRL tag；原始 HTML/表格 — Exhibit 99.1, ‘Fourth Quarter Results’; sentence for the quarter ended June 30, 2014. | Release labels results GAAP and explicitly reports the Q4 loss per diluted share; no adjusted qualifier applies. | As-filed basis; no adjustment applied. Later reverse splits were in 2017 and 2022. No same-period local Q4 value; not comparable. |
| FGC FY2016 | [SEC 8-K EX-99.1](https://www.sec.gov/Archives/edgar/data/1591890/000138713117001425/ex99-1.htm)<br>`0001387131-17-001425`; filed 2017-03-16 | FY2016 Q4 ended 2016-12-31 | 0.27 | 無 XBRL tag；原始 HTML/表格 — Exhibit 99.1, unaudited consolidated income statement; Three Months Ended December 31, 2016, Diluted row. | Primary consolidated statement labels the row Diluted. The annual ‘before catastrophe events’ EPS is adjusted/non-GAAP and excluded. | As-filed FY2016 basis; no adjustment applied. Later reverse splits were 0.04 (2024) and 0.20 (2026). No same-period local Q4 value; not comparable. |
| FGC FY2022 | [SEC 8-K EX-99.1](https://www.sec.gov/Archives/edgar/data/1591890/000149315223008865/ex99-1.htm)<br>`0001493152-23-008865`; filed 2023-03-24 | FY2022 Q4 ended 2022-12-31 | -0.03 | 無 XBRL tag；原始 HTML/表格 — Exhibit 99.1, Q4 earnings narrative and consolidated statement; Three Months Ended December 31, 2022 column. | Narrative says fully diluted Q4 EPS; primary statement says Basic and diluted net earnings (loss) per common share. The separate annual column is not used. | As-filed FY2022 basis; no adjustment applied. Later reverse splits were 0.04 (2024) and 0.20 (2026). No same-period local Q4 value; not comparable. |
| WRAP FY2022 | [SEC 8-K EX-99.1](https://www.sec.gov/Archives/edgar/data/1702924/000143774923005259/ex_483215.htm)<br>`0001437749-23-005259`; filed 2023-03-03 | FY2022 Q4 ended 2022-12-31 | -0.09 | 無 XBRL tag；原始 HTML/表格 — Exhibit 99.1, consolidated statements of operations; first column is Three Months Ended December 31, 2022. | Primary statement row is Net loss per basic and diluted common share; Q4 column is distinct from the twelve-month columns. | As-filed FY2022 basis; no post-period split adjustment applied. No same-period local Q4 value; not comparable. |
| ULBI FY2021 | [SEC 8-K EX-99.1](https://www.sec.gov/Archives/edgar/data/875657/000143774922002307/ex_331663.htm)<br>`0001437749-22-002307`; filed 2022-02-03 | FY2021 Q4 ended 2021-12-31 | -0.07 | 無 XBRL tag；原始 HTML/表格 — Exhibit 99.1, unaudited Q4 table; Three-Month Period Ended December 31, 2021, Net (Loss) Income Attributable row, Per Diluted Share column. | The table reports the unadjusted net loss attributable to Ultralife and its per diluted share amount. The separate adjusted net income row is not used. | As-filed basis; no adjustment applied and no later split identified for this period. No same-period local Q4 EPS value; the local Q4 EPS is null. |

## 來源成功率

每個來源預定查 20 個樣本期別（16 個缺漏期別及 4 個 Grade A 對照）；PCSA FY2012 因目前 CIK 沒有可連結的申報 accession，故每個來源均有 1 個未完成目標、19 個完成檢查期別。此 E 不算成找不到資料或來源失敗。候選數含對照期別；唯一貢獻欄只計 16 個缺漏期別，不把對照當成新補值或重複計算。

| 來源 | 預定目標 | 完成檢查 | 未完成 | 找到候選 | Verified Direct | 不可驗證 | 來源失敗 | 單一來源新增唯一值 | 累積唯一期別 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| SEC Financial Statement Data Sets | 20 | 19 | 1 | 3 | 3 | 0 | 0 | 0 | 0 |
| SEC Financial Statement and Notes Data Sets | 20 | 19 | 1 | 4 | 3 | 1 | 0 | 0 | 0 |
| 原始 10-K / 10-K/A | 20 | 19 | 1 | 4 | 3 | 1 | 0 | 0 | 0 |
| 原始 8-K / EX-99.1 | 20 | 19 | 1 | 6 | 5 | 1 | 0 | 5 | 5 |

- Source A/B/C 的候選主要落在陽性對照；Notes 與原始 10-K 各提供 3 筆可與 main 對照值相符的 direct control facts。PZG FY2016 的 +0.32 與 main -0.32 衝突，列為不可驗證。這些 control 值不計為缺漏資料回補。
- Source D 在缺漏樣本找到 5 筆 Verified Direct，另 ULBI FY2014 只有未標示 basic/diluted 的 +0.05 候選；PCSA FY2012 身份/歷史申報窗口不完整，列 E。Source D 是本次對缺漏列最有效來源。

## Positive control / main ledger 稽核

Main 現有 classifier 與 acceptance ledger 已可用。PZG、PTN 四個選中期別均由 main `epsQuality=A` 和該列 SEC Q4 fact 核對；其中 3/4 個 SEC as-filed 值一致，PZG FY2016 保留 unresolved sign conflict。PTN splitFactor=0.0008，as-filed +0.06/+0.23 調整後為目前資料 +75/+287.5；不混用拆股前後基準。

NMTC 與 TDW 不在 main missing-classification 清單。main financial rows 中有 EPS 值，但沒有 row-level `epsQuality=A` / `secQ4EpsFacts` 可作為 Grade A 證據；ledger 對這四列沒有 blocked、overridden 或 deferred 記錄。ledger 的 `verifiedA=5009` 是總數，沒有 per-row accepted-A 清單，因此不能只由 aggregate 認定 NMTC/TDW 是 Grade A；它們不是本次陽性對照。

| 舊控制候選 | FY / 本地 Q4 end | 本地 EPS | 在 main missing list | row-level Grade A fact | ledger blocked / overridden / deferred |
|---|---|---:|---|---|---|
| NMTC | FY2017 / 2017-12-31 | -0.21 | 否 | 無 | 無 / 無 / 無；ledger 只有 aggregate verifiedA=5009 |
| NMTC | FY2019 / 2019-12-31 | -0.34 | 否 | 無 | 無 / 無 / 無；ledger 只有 aggregate verifiedA=5009 |
| TDW | FY2018 / 2018-03-31 | -1.67 | 否 | 無 | 無 / 無 / 無；ledger 只有 aggregate verifiedA=5009 |
| TDW | FY2019 / 2019-03-31 | -0.58 | 否 | 無 | 無 / 無 / 無；ledger 只有 aggregate verifiedA=5009 |

## Fiscal period mismatch（另案處理）

以下比對 main 本地 Q4 row end 與 SEC 同 FY 10-K report end。這是期間映射診斷，本 PR 不修改 financials。TDW 本地仍把 2017–2026 年 3 月季度標為 Q4；FY2017-03-31 對應舊 3 月財年，但公司隨後改為 12 月結帳，並有截至 2017-12-31 的九個月 transition period。可比完整曆年年報自 FY2018 起，故下表確認 end-date mismatch 自 FY2018，另將 FY2017 transition gap 列在下方。

| 股票 | FY / Local FQ | 本地 Q4 end | SEC 10-K report end | Accession / Filing Date | 狀態 |
|---|---|---|---|---|---|
| TDW | FY2018 / Q4 | 2018-03-31 | 2018-12-31 | `0001564590-19-005545` / 2019-02-28 | confirmed_mismatch |
| TDW | FY2019 / Q4 | 2019-03-31 | 2019-12-31 | `0001564590-20-008191` / 2020-03-02 | confirmed_mismatch |
| TDW | FY2020 / Q4 | 2020-03-31 | 2020-12-31 | `0001437749-21-005037` / 2021-03-04 | confirmed_mismatch |
| TDW | FY2021 / Q4 | 2021-03-31 | 2021-12-31 | `0001437749-22-005725` / 2022-03-09 | confirmed_mismatch |
| TDW | FY2022 / Q4 | 2022-03-31 | 2022-12-31 | `0001437749-23-004829` / 2023-02-28 | confirmed_mismatch |
| TDW | FY2023 / Q4 | 2023-03-31 | 2023-12-31 | `0001437749-24-006108` / 2024-02-29 | confirmed_mismatch |
| TDW | FY2024 / Q4 | 2024-03-31 | 2024-12-31 | `0001437749-25-005487` / 2025-02-27 | confirmed_mismatch |
| TDW | FY2025 / Q4 | 2025-03-31 | 2025-12-31 | `0001437749-26-006391` / 2026-03-02 | confirmed_mismatch |
| TDW | FY2026 / Q4 | 2026-03-31 | 無同 FY 年報列 | — | unmatched_no_same_fy_annual_row |
| NMTC | FY2018 / Q4 | 2018-12-31 | 無同 FY 年報列 | — | unmatched_no_same_fy_annual_row |
| NMTC | FY2019 / Q4 | 2019-12-31 | 2019-09-30 | `0001213900-19-026720` / 2019-12-20 | confirmed_mismatch |
| NMTC | FY2020 / Q4 | 2020-12-31 | 2020-09-30 | `0001213900-20-041661` / 2020-12-09 | confirmed_mismatch |
| NMTC | FY2021 / Q4 | 2021-12-31 | 2021-09-30 | `0001213900-21-065463` / 2021-12-15 | confirmed_mismatch |
| NMTC | FY2022 / Q4 | 2022-12-31 | 2022-09-30 | `0001213900-22-081897` / 2022-12-22 | confirmed_mismatch |
| NMTC | FY2023 / Q4 | 2023-12-31 | 2023-09-30 | `0001213900-23-096120` / 2023-12-15 | confirmed_mismatch |
| NMTC | FY2024 / Q4 | 2024-12-31 | 2024-09-30 | `0001213900-24-109524` / 2024-12-17 | confirmed_mismatch |
| NMTC | FY2025 / Q4 | 2025-12-31 | 2025-09-30 | `0001213900-25-122390` / 2025-12-17 | confirmed_mismatch |
| BNC | FY2024 / Q4 | 2025-01-31 | 2024-12-31 | `0001641172-25-000913` / 2025-03-27 | confirmed_mismatch |
| BNC | FY2025 / Q4 | 2026-01-31 | 2026-04-30 (latest retrieved 10-K; fiscal-year label unresolved) | `0001482541-26-000019` / 2026-06-23 | unmatched_local_row_requires_period_mapping |

TDW transition-period gap：本地 FY2017 Q4 為 2017-03-31；SEC 2019 10-K 的比較季度表明列 ‘Nine Month Transition Period Ended December 31, 2017’（[accession 0001564590-20-008191](https://www.sec.gov/Archives/edgar/data/98222/000156459020008191/tdw-10k_20191231.htm)）。本地沒有 2017-12-31 的 Q4 row，這項過渡期間缺口與上表的 16 個同 FY confirmed mismatch 分開記錄。

確認期末不一致 16 列；另 3 列需要對應或等待同 FY 年報。所有 listed rows 留待獨立 financial-period 修正 PR。

## 未解決原因

| 原因 | 期別 | 說明 |
|---|---|---|
| SEC 搜尋範圍未找到可直接採用的 Q4 EPS | 8 | 缺漏樣本中 8 個 C 期別；只代表本次明確 10-K/8-K 範圍未找到，並非宣稱 SEC 從未披露。 |
| 只有年度或部分季度 EPS | 8 | 同上 C 類；可支援資料線索但不構成 Q4 直接值。 |
| 原始申報沒有可用 Q4 EPS 表 | 8 | 八個 C 期別只有年度/部分季度資料，或 Q4 表是股價資訊；沒有可用的直接 Q4 diluted EPS 列。這是搜尋範圍內的結果，不等於 SEC 歷史上從未披露。 |
| 有候選但 basic/diluted 或符號無法排除 | 2 | ULBI FY2014 未標示 basic/diluted；PZG FY2016 Notes/表格 +0.32 與 main Grade A -0.32 衝突。 |
| 公司/歷史 CIK 對應不完整 | 1 | PCSA FY2012 沒有可連至目前 CIK 的 SEC filing accession；E，非 D。 |
| 期間日期不匹配 | 1 frozen target + 16 fiscal rows | BNC FY2024 的凍結期末與年報期末不同；另見上方獨立 mismatch 列表。 |
| 解析器 / OCR 技術失敗 | 0 final unresolved | 本次 10-K HTML 已解析，8-K 的影像附件均有 HTTP 200，OCR sidecar hash 對應且完成；兩個 initial diagnostics 已清除。 |
| 最終網路 / 權限失敗 | 0 | 最終 artifact 記錄的 SEC 回應為 HTTP 200；PCSA FY2012 是歷史 mapping 範圍不完整，不是 403/429 或網路失敗。 |

## 執行量與重現

- SEC 節流上限 4 requests/second。Source A/B 各有 1,534,782,067/7,472,961,173 bytes 的 16 個樣本 ZIP 引用資料；這是含重用回應的獨立 ZIP payload 大小，不代表本次下載量。大型原始 ZIP 僅暫存，未納入 Git。
- 從前一版 POC artifact 到重凍結資料，按可對照的來源計數器記錄 162 次新增 SEC request、3,837,738,545 wire bytes；來源細項見 `results.json`。Source A/B 的 request/bytes 是舊版與新版 artifact 累積計數差；其 last-invocation 欄位另列，不能與累積差再相加。前一版曾報 406 requests 及 11,190,612,664 bytes；其 bytes 含一個 Notes ZIP 的快取 payload，因此不作為精確線上傳輸基準，也未與本次增量合計。
- 官方端點文件在首次測試取得；本次報告重建不發 SEC request。Source A/B 的 ZIP 及 accession 可重用；Source C/D 只查樣本相關申報。SEC 回應遵守 4 requests/second 上限，使用 User-Agent、快取與退避；未呼叫 Massive API、未使用付費資料或爬蟲，也未輸出任何 API key。
- SEC 收集約在樣本凍結後 21 分鐘完成（不含後續 report/test）。各來源 request、bytes、cache hit、HTTP status 和 parse status 在 `results.json` / `source-ledger.jsonl` 保存。
- 重建報告：`python scripts/render_sec_q4_eps_poc_report.py`。POC 測試：`python -m unittest discover -s tests -p 'test_sec_q4_eps_poc.py' -v`。完整 Python 測試：`python -m unittest discover -s tests -v`；Atlas 測試：`cd atlas && npm test`。

## 可行性結論

1. **重新凍結的 16 個缺漏期別中，找到 5 筆** Verified Direct Q4 Diluted EPS。先前 6/16 的樣本已因 main 分類更新而改變；此分層小樣本不能外推到 7,755 筆。
2. **原始 SEC 8-K / EX-99.1 最有效**：新增 5 個唯一缺漏期別；資料集和 10-K 對現有控制較有利，但未增加缺漏列的 Verified Direct 值。
3. 瓶頸同時來自直接揭露不足和解析/分類能力：8-K 的五筆直接值證明部分數值存在於原始公告；但另外 8 個缺漏期別只見年度/部分季度線索，1 個有 basic/diluted 不明候選，另有 2 個 period/歷史 CIK mapping 不完整。不能把後者全部歸因於 SEC 未公布或 parser 不足。
4. **值得做 50–100 檔下一階段測試**，但先用 SEC 10-K report date 矯正 FY/Q4 對應、解決 PZG control 符號差異，並保留對來源窗口與公司歷史 CIK 的檢查。10 檔樣本是按缺漏理由分層後選出，沒有代表性抽樣設計，不可把 5/16 或舊 6/16 外推到 7,755 筆。

**Verdict: PARTIAL** — 五筆直接值完成來源驗證；一筆候選與一筆 control 仍有疑義；PCSA FY2012 與 BNC FY2024 期別搜尋不完整/不匹配，另有獨立 local fiscal-period 對應問題。

Sol XHigh 審核：PARTIAL，無阻擋事項。Sol 6.1 XHigh 唯讀審查核對了樣本凍結與分類、ledger 對照、來源分母與網路統計、TDW transition 期間證據，以及 POC-only diff 範圍。

## 證據檔案

- `sample.json`：main classification / ledger 輸入、固定 seed、抽樣條件、frozen hash。
- `results.json`：逐期 adjudication、來源狀態、control audit、期間 mismatch 與 network metrics。
- `source-ledger.jsonl`：來源 URL、accession、HTTP、bytes、hash、ZIP parsing、OCR hash 與 adjudication。
- `official-docs.json`、`filing-map.json`、`source-a.json`、`source-b.json`、`source-c.json`、`source-d.json`、`source-d-ocr.json`：SEC 原始來源索引/解析 metadata；大型 ZIP 未提交。
- `positive-control-pzg-2017-source.json`：PZG 後續 10-K 比較來源，未能消除 FY2016 的 EPS 符號衝突。
