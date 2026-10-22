# SEC 重大重編比較期試跑

僅當較晚申報的營收、淨利或其他損益欄位，相對目前值有嚴格超過 2% 的差異時才切換。未申報欄位保留原值，記錄 basis-unrestated，並對該期間標記 mixed-basis。日期、期間、未切換列及其他 Q4 EPS 原封不動。

同期間須有較晚的年度申報；只有首次年報與現存值不同不視為重編。其他損益欄位含成本、毛利、營業利益、稅前利益、稅費、利息及 EPS；候選值先對齊該列既有拆股／ADR 基準。

掃描 4,967 份 US 財報；可評估 4,960 份。切換 1,551 個年度期間、775 個股票。
其中營收／淨利觸發：788 個期間、425 個股票；其餘由其他損益欄位觸發。

變動欄位值共 4,643 個；一般欄位設為 null：0。
Q4 EPS 另計：設為 null 1,599 個值、改為非 null 值 4 個，共影響 1,603 個值。只有已切換年度欠缺同基準季度輸入時才能清空 Q4 EPS。

以上是試算，未寫入 docs/report/financials/，未執行合併。原 builder 正規化、日期對齊與去重差異完全排除。逐筆原值、新值、申報來源、切換觸發欄位與保留原值稽核均在同名 JSON。

| AD 年度 | 營收原值 | 營收重編值 | 淨利原值 | 淨利重編值 |
| --- | ---: | ---: | ---: | ---: |
| 2023-12-31 | 3,906,000,000 | 100,469,000 | 54,459,000 | 54,459,000 |
| 2024-12-31 | 3,770,000,000 | 102,933,000 | -39,403,000 | -39,403,000 |

| 欄位 | 變動值數 |
| --- | ---: |
| capex | 14 |
| cash | 284 |
| costOfRevenue | 109 |
| dilutedShares | 14 |
| dividendsPaid | 4 |
| epsBasic | 1,191 |
| epsDiluted | 1,187 |
| equity | 544 |
| grossProfit | 9 |
| interestExpense | 9 |
| netIncome | 291 |
| operatingCashFlow | 12 |
| operatingIncome | 32 |
| pretaxIncome | 305 |
| revenue | 586 |
| taxProvision | 33 |
| totalAssets | 7 |
| totalDebt | 12 |

無法評估（不代表沒有差異）：FITB: revenue-scope-mismatch；HBAN: revenue-scope-mismatch；HONA: us-gaap-no-usable-facts；IBKR: revenue-scope-mismatch；MTB: revenue-scope-mismatch；SCNI: currency-mismatch: no cross-currency comparison；VYLR: no-us-gaap。
Q4 被阻擋時的 accession／filed 是觸發年度基準的申報。一般欄位 null 數排除上面單獨列出的 Q4 EPS 例外；全部 null 均屬該例外。
