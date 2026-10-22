# SEC 重編比較期試跑

掃描 4,967 份美股檔案，已審查 4,960 份。預計變動 4,749 個股票、62,964 個期間、225,051 個欄位值。

Q4 EPS 影響 17,372 個值（9,041 個期間），其中 17,089 個會變為 null。

所有欄位變動中，192,994 個值會轉為 null。最新申報未重列的舊年度欄位（尤其資產負債表）保持 null，不從其他 accession 借值。

只產生試跑報告，未寫入 financials/；逐筆變更、accession、filed、原因與檔案雜湊見同名 JSON。不同 accession 的季度不能證明已重編；欠缺同基準季度時保留季度原值，停止跨基準 Q4 EPS 推導。

狀態：`{"reviewed": 4960, "unsupported": 7}`；停止原因：無。

無法評估（不代表沒有差異）：FITB: revenue-scope-mismatch；HBAN: revenue-scope-mismatch；HONA: short-history；IBKR: revenue-scope-mismatch；MTB: revenue-scope-mismatch；SCNI: currency-mismatch: no cross-currency comparison；VYLR: no-us-gaap。

| 欄位 | 變動值數 | 其中轉為 null |
| --- | ---: | ---: |
| buybacks | 5,738 | 5,648 |
| capex | 8,888 | 8,561 |
| cash | 19,222 | 10,148 |
| costOfRevenue | 6,063 | 5,741 |
| dilutedShares | 6,159 | 5,703 |
| dividendsPaid | 5,758 | 5,481 |
| epsBasic | 12,801 | 10,846 |
| epsDiluted | 12,580 | 10,660 |
| equity | 20,156 | 7,220 |
| fcfYahoo | 8,478 | 8,478 |
| grossProfit | 5,191 | 5,019 |
| interestExpense | 12,514 | 12,419 |
| netIncome | 1,646 | 614 |
| operatingCashFlow | 8,928 | 8,745 |
| operatingIncome | 4,818 | 4,518 |
| operatingIncomeReported | 6,656 | 6,656 |
| pretaxIncome | 6,332 | 5,606 |
| revenue | 3,558 | 2,341 |
| taxProvision | 6,134 | 5,859 |
| totalAssets | 33,736 | 33,473 |
| totalDebt | 29,695 | 29,258 |

Q4 被阻擋時的 accession／filed 指觸發年度基準的申報，並不代表它提供了保留的原季數值。變動計數包含既有 builder 的期間對齊、去重及一般正規化，逐筆原因在 JSON 中。
