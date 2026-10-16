# 美股 Q4 EPS 證據與缺漏報告

產生日期：2026-10-16。範圍是分支基準 `3f9406d8b` 上的美股財報快照及其後的工作樹變更。PR #79 已合併；本報告描述的是後續補強，不能把它當成對 PR #79 基準缺漏的完整修復。

## 驗收摘要

- 基準共有 4,967 檔美股財報、43,614 個 Q4 期別，期間從 2009-06-27 到 2026-09-03。基準稀釋 EPS 缺值且 Q4 淨利存在的紀錄為 7,364 筆、2,173 檔股票。
- 本次相對基準新增 7 筆 SEC Grade A 與 14 筆重建 Grade B，全部是 Basic EPS，且只寫入原本為 null 的欄位。沒有新增 Diluted A/B 值。
- 這 7,364 筆 Diluted 缺漏沒有一筆被本次新增的 A/B 值補上；按四類基準分類，新增正式補值率皆為 0%。現況 audit 有 7,547 筆缺值或已隔離期別，較基準多 183 筆，原因是 183 個未驗證的舊稀釋股數估值改標 C 並排除於正式用途。
- 正式 A/B Q4 覆蓋率目前為 Basic 2,463/43,614（5.65%）、Diluted 2,723/43,614（6.24%）。任一 EPS 類別有 A/B 的 Q4 期別為 2,971/43,614（6.81%）。這遠低於足以把全美股十年 EPS 當成完整正式選股資料的程度，**未達全市場正式使用條件**。
- 直接 SEC facts 的現存值驗證 n=5,127，中位差 0.00%、P90 0.00%；20 筆既存值經 SEC 直接季報資料更正。SEC 交叉檢查 n=5,154，`Q4 淨利 ÷ Q3 股數` 的相對差中位數 1.50%、P90 11.30%、最大 24.98%，正負號不符 0 筆。
- Basic 實際日數回測整體 n=456 未通過門檻；只採用經加嚴及原始 filing 脈絡核對的子群 n=191，中位正規化誤差 0.58%、P90 2.42%、絕對誤差 P50 $0.00317/P90 $0.01170、符號錯誤 0，通過門檻。14 筆通過原始 SEC 證據檢查後才列為 B。

## 基準數量與分類核對

基準是 commit `3f9406d8b`，不是更新後的工作樹。統計單位是「美股財報檔中的 Q4 期別」，只計 `epsDiluted` 缺值、Q4 `netIncome` 非 null 的期別；同一公司不同財年各算一筆。audit 的四個頂層類別互斥且合計 7,364。

| 基準類別 | 期別數 | 股票檔數（類別內去重） | 本次新增 Diluted A/B | 目前仍缺 | 本次補值率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 缺年度 EPS | 2,045 | 609 | 0 | 2,045 | 0.00% |
| 缺 Q1–Q3 EPS | 1,467 | 1,165 | 0 | 1,467 | 0.00% |
| 年度與季度股數差超過 25% | 1,235 | 802 | 0 | 1,235 | 0.00% |
| 其他 | 2,617 | 1,146 | 0 | 2,617 | 0.00% |
| **總計** | **7,364** | **2,173** | **0** | **7,364** | **0.00%** |

提示中的六類數字（1,964、1,877、1,460、177、572、1,314）相加也等於 7,364，但 repo 沒有可追溯的定義或快照能重現這六類，故不能宣稱已驗證其統計單位或互斥性。repo audit 的「other」是 2,617，與提示的 2,707 相差 90；以基準資料及 repo 現有分類重算後使用 2,617。

| 基準 `other` 的單一失敗原因 | 期別數 | 股票檔數（非互斥） |
| --- | ---: | ---: |
| 方法 A 股數基準差超過 25% | 1,791 | 901 |
| 年度 EPS 與淨利／股數不一致 | 290 | 207 |
| 方法 A 推算 EPS 與 Q4 淨利符號不一致 | 110 | 99 |
| 方法 A 推算值未通過 Q4 淨利／股數檢查 | 95 | 89 |
| 年度／季度期別缺漏、重複或不唯一 | 26 | 24 |
| 缺股數且 EPS 無效，無法推算股數 | 299 | 153 |
| 缺股數且缺淨利，無法推算股數 | 2 | 2 |
| 年度或季度來源非 SEC | 4 | 4 |
| **合計** | **2,617** | — |

「other」子原因在每一期別上互斥；股票檔可能在不同期別落入不同原因，所以股票數不能相加。前後每類與詳細的 before/after 數字見 [q4-eps-audit-before-after.csv](q4-eps-audit-before-after.csv)。逐期基準原因和現況原因分別見 [基準 7,364 筆](q4-eps-diluted-unresolved-before.jsonl) 與 [現況 7,547 筆](q4-eps-diluted-unresolved-current.jsonl)；基準分類補值率見 [q4-eps-diluted-repair-success-by-category.csv](q4-eps-diluted-repair-success-by-category.csv)。另有 208 個 Q4 缺稀釋 EPS 但也缺 Q4 淨利的期別，未納入 7,364 目標母體。

## SEC Grade A

SEC 抓取器只查仍缺 Q4 EPS 的公司，使用 `atlas/scripts/seed_financials_sec.py` 的 `SecClient`、既有 User-Agent 與速率限制，遇到第一個 403 立即停止；`--budget` 到時寫 checkpoint，重跑同一命令即可續跑。依 `us-gaap:EarningsPerShareDiluted` / `EarningsPerShareBasic` 找 USD/shares、80–100 日且結束日落在財年 Q4 期末 ±5 日內的 facts，依存檔 split events 轉成目前股數基準。正式寫檔前還會比對 Q4 淨利與 Q3 股數，要求同號且誤差不超過 25%。

本次 SEC ledger 的結果：

| 結果 | 欄位數 | 股票數 | 說明 |
| --- | ---: | ---: | --- |
| Grade A，既存值由 SEC 直接 fact 驗證 | 5,127 | 593 | 不改數值 |
| Grade A，直接 SEC 證據更正既存值 | 20 | 15 | 只在來源明確不同時改值 |
| Grade A，null → 值 | 7 | 5 | APAM 2017、BRK-A 2012、JAKK 2019/2020、LYV 2015/2017、PTCT 2012 |
| 拒絕／Grade D | 1,803 | — | Basic 1,008、Diluted 795；包含分類、期間、拆股、交叉檢查或股類問題 |

既存值比較（5,147 筆）中位誤差 0.00%、P90 0.00%、P99 0.0006%、最大 124%；22 筆超過 1%。1% SEC 比較門檻通過。最大值來自小 EPS 的不同值及已更正衝突，不能只看中位數。上述 SEC facts 對 Q4 淨利/Q3 股數的交叉檢查 n=5,154，誤差中位 1.50%、P90 11.30%、最大 24.98%、符號錯誤 0，通過 ±25% 及同號要求。

PR #79 基準已包含前一階段的 87 個 SEC 官方 Q4 值、60 檔公司及 0.00% 中位驗證誤差；這 87 筆是繼承的基準資料，不重複計入本次 7 筆新填值。每筆新／已驗證 fact 的 CIK、accession、申報日期、期間、單位、拆股因子、原始值、URL 與驗證數據見 [SEC Grade A ledger](q4-eps-sec-A-decisions.jsonl)。SEC facts 快取與 checkpoint 在 `docs/report/financials/` 下的 `q4-eps-companyfacts-cache/`、`q4-eps-companyfacts-state.json` 等本機執行檔不一定納入版本控制；受追蹤的 decisions ledger 含可稽核來源欄位。

### 既存數值更正

相對來源快照有 174 個既存數值欄位改變且目前可由 SEC Grade A 來源核對：20 筆是明確標記的直接 SEC 更正；另 154 筆屬快照差異、目前值有 SEC A 證據，但無足夠紀錄把舊值成因逐筆歸類。174 筆中 156 筆差額超過 $0.01，18 筆不超過 $0.01。逐列 before/after 與來源見 [q4-eps-numeric-corrections.jsonl](q4-eps-numeric-corrections.jsonl)。不能把 154 筆都描述成已查明根因。

## Grade B：Basic EPS 實際日數重建

分子是同一 filing 的普通股可得盈餘年度值減九個月值，並比對 Q4 淨利；分母由年度及九個月 Basic weighted-average shares 依實際期間天數計算 Q4 股數，拆股後轉到同一基準。重建前要求期間、幣別、分子分類、權益歸屬、股類、申報版本與拆股基準能由原始 filing 證實。缺優先股／非控制權益處理、股類不明或 EPS numerator 無法還原者維持 D/null。

整體有 Grade A Basic 實際 Q4 的完整輸入遮蔽樣本 n=456：MAE $6.3743/share、絕對誤差 P50 $0.00373、P90 $0.10882、P99 $140.57603；正規化誤差 P50 0.87%、P90 16.37%；符號錯誤 3/456（0.66%）。整體不通過。極端 P99 顯示不能對所有公司套用公式。表格採 `|estimate − actual| / max(|actual|, 0.10)`，另列絕對誤差以免 EPS 接近零時相對誤差失真。

下表列出每個 Basic subgroup 的遮蔽回測誤差。P50/P90 是正規化誤差；絕對誤差以 USD/share 列示。各組彼此重疊。只有最後的 source-checked 子群可寫入，其他組只作診斷，補值 0。

| 子群 | n | P50 | P90 | 絕對 P50 | 絕對 P90 | 符號錯誤 | 門檻 | 本次 B 填值 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| 全部 Grade A 且輸入完整 | 456 | 0.87% | 16.37% | $0.00373 | $0.10882 | 3 | 失敗 | 0 |
| 推算 Q4 股數在 Q3 ±15% | 439 | 0.84% | 11.79% | $0.00368 | $0.09024 | 3 | 失敗 | 0 |
| Q1–Q3／年度股數差 ≤2% | 285 | 0.69% | 4.98% | $0.00329 | $0.02739 | 1 | 絕對精度失敗 | 0 |
| Q1–Q3／年度股數差 ≤5% | 341 | 0.72% | 7.23% | $0.00336 | $0.03968 | 1 | 失敗 | 0 |
| Q1–Q3／年度股數差 ≤10% | 378 | 0.77% | 7.44% | $0.00353 | $0.05200 | 1 | 失敗 | 0 |
| Q4 淨利為正 | 324 | 0.77% | 7.29% | $0.00351 | $0.04737 | 3 | 失敗 | 0 |
| Q4 淨利為負 | 132 | 1.63% | 849.49% | $0.00488 | $4.01899 | 0 | 失敗 | 0 |
| `|Q4 淨利|/|年度淨利| ≤10%` | 30 | 2.24% | 5.86% | $0.00339 | $0.01217 | 0 | 中位數失敗 | 0 |
| `|Q4 淨利|/|年度淨利| >10%` | 426 | 0.81% | 17.53% | $0.00378 | $0.11497 | 3 | 失敗 | 0 |
| 指定嚴格條件：股數差 ≤2%、Q4 淨利正、Q4 股數在 Q3 ±15% | 209 | 0.64% | 3.16% | $0.00317 | $0.01215 | 1 | 符號／來源門檻未全過 | 0 |
| 嚴格條件且 EPS 絕對值 < $0.10 | 16 | 2.36% | 4.88% | $0.00236 | $0.00488 | 0 | 中位數失敗 | 0 |
| 嚴格條件且 Q4/年度淨利 ≤10% | 17 | 2.37% | 5.19% | $0.00356 | $0.00900 | 0 | 中位數失敗 | 0 |
| 嚴格條件且 Q4/年度淨利 >10% | 192 | 0.58% | 2.49% | $0.00317 | $0.01210 | 1 | 符號門檻失敗 | 0 |
| 嚴格條件、Q4/年度淨利 >10%、普通股分子同號 | 191 | 0.58% | 2.42% | $0.00317 | $0.01170 | 0 | **通過** | **14** |

最後子群使用同號核對與美分精度保護：絕對 P50 ≤$0.005、P90 ≤$0.02。其結果為 MAE $0.01997/share、絕對誤差 P99 $0.25801、正規化 P50 0.5775%、P90 2.4238%，通過使用者指定的 2%/6% 門檻及額外絕對精度門檻。近零 EPS 子群未被套用。完整欄位與分財年回測見 [Basic subgroup CSV](q4-eps-basic-B-backtest-subgroups.csv)、[Basic backtest JSON](q4-eps-basic-B-backtest.json)、[Diluted-share diagnostic](q4-eps-backtest-subgroups.csv) 及 [逐年表](q4-eps-backtest-by-fiscal-year.csv)。

候選源頭 63 個、SEC 原始 filing 核驗通過 14 個（22.2%）；其中曾進入逐筆 numerator note review 的 20 個，14 個通過、6 個因 Q4 participating-security allocation 不可由年度與九個月累計資料證實而維持缺值。其餘未通過的 source facts 維持 D。CompanyFacts-only 的 1,534 組候選因無法證實普通股可得盈餘口徑、非控制權益／優先股處理、股類或拆股連續性，Grade B 通過數為 0。14 個 B 的 accession、各分子／股數事實、股類證據與 hash 見 [source decisions](q4-eps-basic-B-source-decisions.jsonl)；6 個 retained-missing 的逐筆 numerator 審查見 [review ledger](q4-eps-basic-B-numerator-review.jsonl)。

`q4-eps-from-shares` 現僅標記 14 個有 Grade B Basic Q4 的檔案。舊有 109 檔 C 級 Diluted 股數估值另標 `q4-eps-estimate-quarantined`，UI 會提示只供審核，且 P/E／成長正式輸入會排除它們。

## 覆蓋率、成長與 TTM／選股影響

| EPS 欄位 | Q4 期別 | 原始缺值 | Grade A | Grade B | Grade C | Grade D | 未標級但有值 | A/B 覆蓋率 | 正式缺值 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Basic | 43,614 | 7,384 | 2,449 | 14 | 0 | 306 | 33,468 | 5.65% | 41,151 |
| Diluted | 43,614 | 7,572 | 2,723 | 0 | 183 | 0 | 33,136 | 6.24% | 40,891 |

完整按財年、股票、EPS 欄位的表在 [q4-eps-coverage-by-fiscal-year.csv](q4-eps-coverage-by-fiscal-year.csv)、[q4-eps-coverage-by-symbol.csv](q4-eps-coverage-by-symbol.csv)；計算摘要在 [q4-eps-coverage.json](q4-eps-coverage.json)。Q4 任一 EPS 欄位具 A/B 的期別數是 2,971（6.81%）。這不是全選股資料的覆蓋率：P/E river 仍能用年度 EPS fallback；本次不改動選股門檻或自動選股規則。

對 `growth.json` 的基準比較：US quarterly EPS YoY 有 5,017 個值變 null、6 個值改變，沒有 null 變成 EPS YoY 值；annual EPS YoY 有 1 個值變 null（PRPL）；US revenue YoY 數值完全不變；基準 growth 為 6,593 列；最新重建輸出 6,591 列。PRPL 的矛盾年度 EPS 已隔離，其相應 YoY 值不作正式輸出。Q4 等級門檻只套在美股，台股 EPS 與成長流程維持原行為。

P/E river 的純資料影響，以 2026-10-16 為觀察日、按現有 `FinancialHistory` / `peRiver.epsAt` 規則重算：4,967 檔美股中 3,024 檔目前 EPS(TTM)/annual 輸入變動；TTM 可用檔數 3,068→55，年度 fallback 1,371→4,383，無 EPS 528→529。正 EPS 檔數 2,466→2,444；119 檔由正值轉成非正／無值，97 檔反向改變。台股 510 檔沒有變動。這是 Q4 正式等級 gate 導致的審慎資料效果，不表示 216 檔實際買賣決策已翻轉；repo 沒有由 P/E river 驅動的自動選股規則，也未使用即時價格重演所有估值門檻。逐檔前後資料與可重現程式見 [q4-eps-ttm-impact.json](q4-eps-ttm-impact.json)、[q4-eps-ttm-impact-by-symbol.csv](q4-eps-ttm-impact-by-symbol.csv)、`atlas/scripts/report_q4_eps_ttm_impact.py`。

## 十檔 SEC 抽查

欄位順序是 Basic / Diluted。split 調整後數值均以目前股數基準表示。18 個欄位中 16 個可在指定 SEC Q4 10-K 或 Item 2.02 8-K/EX-99.1 中直接核對；TSLA Basic 與 WMT Basic 未找到可唯一對應期間的直接 EPS 行，故保留並標註，沒有假稱直接核實。完整來源 URL、CIK、accession、filed date、表格摘錄與 hash 見 [spot-check CSV](q4-eps-spot-check.csv) 與 [reconciliations](q4-eps-spot-check-reconciliations.json)。

| 股票 | 財年 | Basic（前 → 後；官方行） | Diluted（前 → 後；官方行） |
| --- | ---: | --- | --- |
| NFLX | 2025 | $0.57 → $0.57；10-K | $0.56 → $0.56；10-K |
| GOOGL | 2025 | $2.85 → $2.85；10-K／8-K | $2.82 → $2.82；10-K／8-K |
| TSLA | 2025 | $0.26 → $0.26；未找到直接 Q4 行 | $0.24 → $0.24；8-K |
| NVDA | 2026 | $1.77 → $1.77；10-K | $1.76 → $1.76；10-K |
| AMZN | 2025 | $1.98 → $1.98；8-K／10-K | $1.95 → $1.95；8-K／10-K |
| WMT | 2026 | $0.53 → $0.53；release 行需期間映射 | $0.52 → $0.53；8-K |
| AMD | 2025 | $0.93 → $0.93；8-K／10-K | $0.92 → $0.92；8-K／10-K |
| AVGO | 2025 | $1.81 → $1.80；8-K／10-K | $1.75 → $1.74；8-K／10-K |
| CMG | 2025 | $0.25 → $0.25；8-K／10-K | $0.25 → $0.25；8-K／10-K |
| SMCI | 2026 | $1.89 → $1.83；8-K／10-K | $1.68 → $1.62；8-K／10-K |

## 必查衝突與未完成範圍

PRPL 2023 年年報 EPS +$29.25 沒有自動保留為正式值：2024-03-12 的 10-K（CIK 0001643953、accession `0001213900-24-021861`）列 Q4/年 EPS -$1.17、普通股淨損 -$120.757m、加權基本股數 103.602m；2026-03-31 的比較年報（accession `0001213900-26-036974`）在相同負淨利／股數下列 +$1.17。2026-07-20 的 1:25 反向拆股事件使 -$1.17 轉為 -$29.25，而正值會轉為 +$29.25；拆股無法解釋符號衝突。此值列 D，保留缺值。逐筆資料與兩份 SEC URL 見 [q4-eps-prpl-conflict-D.jsonl](q4-eps-prpl-conflict-D.jsonl)。

本次執行沒有逐一人工搜尋全部 7,547 個未解期別的 10-K、10-Q、8-K Item 2.02、EX-99.1、EPS footnotes、原始 iXBRL 與 IR 網站；直接 SEC 搜尋主要經由 CompanyFacts 季度 duration facts，再對合格 Basic 候選核對原始 filing，另完成上列十檔 spot-check。IPO/SPAC、公司規模分層及全部歷史版本／重複申報的全市場逐期審查也未完成。因此本報告不聲稱已逐筆排除所有錯誤、不聲稱涵蓋所有 SEC/IR 直接披露來源，也不聲稱 7,364 筆已修完。`q4-eps-basic-B-audit.json`、`q4-eps-sec-A-decisions.jsonl`、兩份 unresolved JSONL 和各 source decisions 保存尚未通過及失敗的逐筆原因。

獨立審查方面，Gemini 在此執行環境不可用；由另一 Codex GPT-6 Luna 執行原始 SEC numerator note 審查，無改動工作樹。該 agent 與主執行共用檔案系統及下載快取，故有模型差異但不具隔離 workspace 的完全獨立性。

## 重現命令

```bash
mkdir -p /tmp/q4eps-baseline/docs/report/financials
git archive 3f9406d8b:docs/report/financials | tar -x -C /tmp/q4eps-baseline/docs/report/financials
python atlas/scripts/audit_q4_eps_backfill.py --dir /tmp/q4eps-baseline/docs/report/financials
python atlas/scripts/audit_q4_eps_backfill.py --dir docs/report/financials
python atlas/scripts/report_q4_eps_audit_delta.py --baseline-dir /tmp/q4eps-baseline/docs/report/financials --current-dir docs/report/financials --baseline-label 3f9406d8b --current-label 2026-10-16
python atlas/scripts/report_q4_eps_coverage.py
python atlas/scripts/report_q4_eps_ttm_impact.py --baseline-dir /tmp/q4eps-baseline/docs/report/financials --current-dir docs/report/financials --as-of 2026-10-16
python -m unittest discover -s tests -p 'test_*.py'
cd atlas
npx tsc -b
npm run lint
npx vitest run
python -u scripts/build_growth.py
```

CompanyFacts fetcher resume example: `python atlas/scripts/fetch_q4_eps_companyfacts.py --budget 600`; rerun the same command after timeout or interruption. It reuses completed per-company cache files and checkpoints.
