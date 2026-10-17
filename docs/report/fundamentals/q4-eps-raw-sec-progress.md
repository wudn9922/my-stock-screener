# SEC 原始 Q4 EPS 稽核進度

產生日期：2026-10-17。這是部分進度報告，**不是全市場完成報告**。所有 SEC 掃描只寫入本機快取／checkpoint；本次沒有修改任何財報 JSON 或 production DB。

## PR 與工作基準

GitHub 唯讀 API 確認 PR #89 仍為 open、非草稿、未合併；head 為 `codex/q4-eps-backfill` / `1ad9445014022421b3c3963c6d5c6a64dbb8373e`，base 為 `main` / `c0f84f34477adcf832f3809293c6175320159957`。GitHub 回報 `mergeable=false`。目前工作樹 HEAD 與 PR head 相同；本進度報告記錄的是疊在 PR #89 上的後續變更，PR #89 本身仍未合併。

PR #89 的基準資料報告仍是 [q4-eps-evidence.md](../financials/q4-eps-evidence.md)：基準稀釋 Q4 EPS 缺值且有 Q4 淨利者為 7,364 期、2,173 檔；PR #89 未解或隔離 cohort 為 7,547 期；既有未分級稀釋值為 33,136 筆。這三個分母保持分開。

## 掃描母體與實際進度

以目前財報快照重新計算，SEC 掃描器需搜尋或核驗 3,907 檔美股資料中的 43,614 個 Q4 期別；其中 7,948 期至少有一個 EPS 欄位為 null，另 35,666 期已有數值但缺少符合要求的原始 filing trace。7,948 不是基準的 7,364 筆稀釋 EPS 缺漏數，因其涵蓋 Basic／Diluted 欄位及目前快照。

兩次全市場預設續跑都在首家公司附件工作量達到 budget 後 checkpoint；另對 VEEV 做了 120 秒優先續跑。Submissions 歷史可用 accession 數、候選 accession 數、已快取 accession 數是不同統計，不能互相當成已審核文件數。

| 股票 | Q4 期別 | SEC 歷史 accession 數 | 期別候選 accession 數 | 已快取 accession 記錄 | 原始掃描狀態 |
| --- | ---: | ---: | ---: | ---: | --- |
| A | 14 | 2,365 | 1,232 | 70 | paused；14/14 歷史 block 完整，0/14 本次 force-refresh |
| AMD | 15 | 3,326 | 1,178 | 49 | paused；15/15 歷史 block 完整，0/15 本次 force-refresh |
| VEEV | 14 | 2,004 | 965 | 14 | paused；14/14 歷史 block 完整且已 refresh；最新 Q4 的 7 個候選 accession 已列入 |

目前共快取 218 份 VEEV 文件、1,571 份 A 文件及 918 份 AMD 文件；部分 filing 另含圖片、ZIP、CSS 或其他非 HTML 附件，這些不算可解析 EPS 證據。checkpoint 的累積計數為 13,719 次 SecClient request、12,457 個 URL cache key；這是跨續跑的快取統計，不代表 12,457 份成功審核的來源。當前 cache 沒有記錄 HTTP 403；受限網路時產生的 URLError 不視為 HTTP 403，程式仍保留首個真實 403 即停止的行為。

## 已完成的離線審核

對 A FY2025、AMD FY2022、AMD FY2025 及 VEEV FY2026 四個期別做定向、唯讀的結構化稽核：0 筆 staged Grade A、0 筆財報寫入。對兩家公司全部快取附件執行的廣泛離線 audit 在 180 秒到時仍未完成，沒有輸出可代表全市場的 audit 表。

| 期別 | 觀察 | 裁決 |
| --- | --- | --- |
| A FY2025 | 來源 CIK 由目前 ticker map 推得；資料列沒有足以確認該財年歷史 ticker/CIK lineage 的證據 | manual review；不升級 |
| AMD FY2022 | `epsBasic`、`epsDiluted` 都是 null；41 個申報候選，該期也沒有可核對的存檔 CIK lineage | manual review；不回填 |
| AMD FY2025 | SEC 8-K / EX-99.1 表格有 GAAP diluted EPS $0.92，並與現有 Grade A CompanyFacts 值一致；原始表格只用 `$`，且檔案沒有完整可驗證的 split-event history | 僅作來源交叉比對；這次原始 filing audit 不另升級、不改值 |
| VEEV FY2026 | SEC 8-K / EX-99.1 明列 Q4 GAAP diluted EPS $1.45；目前財報 JSON 的 ungraded `epsDiluted` 是 $1.48 | 記為官方來源差異待裁決；稽核仍標記 ticker/CIK lineage、USD 單位及 split-event coverage 未完整驗證，所以不更正、不升級、不寫入 |

VEEV 來源：CIK `0001393052`、accession `0001393052-26-000007`、8-K Item 2.02 / 9.01、申報日 2026-03-04、EX-99.1，[SEC 原始文件](https://www.sec.gov/Archives/edgar/data/1393052/000139305226000007/veev-20260131q426xex991.htm)。SecClient 於 2026-10-17 擷取的完整 response body 為 407,499 bytes，SHA-256 `8c6161ce12efafe725d3a1bb47e00bd96ee3951fab38d55bd7152ac27955b899`。原始表格是「Diluted net income per share on a GAAP basis」，期間截至 2026-01-31；測試 fixture 保存該 response 及來源 metadata。此差異尚未列為已確認的錯值，也沒有作為正式 TTM 輸入。

## 資料與品質結果

- 本次新找到並正式寫入的 Diluted Grade A：0；本次新寫入的 Basic Grade A：0；現有值升級為 A：0。
- 基準 7,364 筆稀釋缺漏仍未由本次工作補回；PR #89 現有 7,547 筆 unresolved／quarantined cohort 沒有變化。
- 本次正式值更正：0；33,136 筆既有未分級 Diluted EPS 沒有變化。VEEV 差異保留待核，不能算已修正錯值。
- 正式 Q4 A/B 覆蓋率沿用 PR #89 報告：Basic 2,463/43,614（5.65%），Diluted 2,723/43,614（6.24%）；未達全市場正式選股及回測條件。

## 近十年季度 Diluted EPS 快照檢查

以目前 4,967 檔財報 JSON 的季度欄位，按每檔最後 40 個連續 FY/FQ 期別檢查：2,344 檔有至少 40 個連續季度列；其中 1,522 檔的 40 個 Diluted EPS 均非空且季度來源標記為 SEC。再要求這 40 季中的十個 Q4 都有 Grade A/B 的 `secQ4EpsFacts.epsDiluted`，而且 fact value 與季度值相同，符合者為 3 檔：ARL、TCI、WLDN。

這是現存快照的結構／來源標記檢查，並非逐季重新核對原始 filing。Q1–Q3 尚未逐筆保存或複核原始 accession 與摘錄；因此按本任務要求的「完整 raw-source audit」標準，本次新完成近十年全季度原始來源稽核的公司數為 0。上述 3 檔不可解讀為本次重新核實後可直接供正式回測。

## 驗證及審查

- Python 全套測試：`/tmp/q4-test-venv/bin/python -m unittest discover -s tests -p 'test_*.py'`，1,154 tests、全數通過。該暫存 venv 只補上測試依賴，沒有改 repo dependencies。
- TypeScript：`npx tsc -b` 通過；lint 通過；Vitest 72 files / 894 tests 通過。
- `python -u scripts/build_growth.py`：rows=6,591、written=false；因財報 JSON 未變，`docs/report/growth.json` 未重寫。
- 另有不同 Codex GPT-6 Sol Max read-only code review；Gemini 不可用。Reviewer 和主工作階段共用 workspace/cache，且看到部分工作脈絡，因此不是完全盲審。
- 本環境不能切換至指定的 Sol 6.1 XHigh 或 Luna Max 執行模型；由當前 Codex 工作階段完成實作與驗收，未使用 Astra。

完整全市場 filing 擷取、33,136 筆未分級值逐筆核驗、7,364 筆基準逐類 A/B/D 結果、TTM 按公司差異，以及跨產業／規模／公司事件分層的全市場品質抽查，均未完成。不得將本進度報告或局部掃描描述為 COMPLETE。

## 交付狀態

此進度報告與程式、測試及來源 fixture 已提交至 draft stacked PR [#99](https://github.com/wudn9922/my-stock-screener/pull/99)，base 為 PR #89 的分支 `codex/q4-eps-backfill`。PR #99 仍未完成全市場掃描、逐筆既有值核驗或正式資料回補，故維持 draft。
