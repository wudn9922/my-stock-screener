# Yahoo 年度回補結果（FY2021–FY2025）

共 1,266 份財報、11,978 個 null→value；沒有新增期間。

| 欄位 | US | TW |
|---|---:|---:|
| buybacks | 372 | 9 |
| capex | 229 | 34 |
| cash | 134 | 1 |
| costOfRevenue | 462 | 35 |
| dividendsPaid | 156 | 65 |
| equity | 29 | 1 |
| fcfYahoo | 2,662 | 105 |
| grossProfit | 665 | 35 |
| interestExpense | 859 | 102 |
| netIncome | 11 | 36 |
| operatingCashFlow | 27 | 28 |
| operatingIncome | 263 | 36 |
| operatingIncomeReported | 2,010 | 2 |
| pretaxIncome | 532 | 36 |
| revenue | 815 | 36 |
| taxProvision | 104 | 40 |
| totalAssets | 31 | 1 |
| totalDebt | 1,953 | 62 |
| **合計** | **11,314** | **664** |

| 略過原因 | 期間數 |
|---|---:|
| restated | 0 |
| unit-mismatch | 302 |
| unexplained | 27,625 |
| basis-unverified | 13 |
| no-source | 1,406 |
| no-overlap | 43 |
| ambiguous-period | 0 |

restated=0 表示沒有差異符合「Yahoo 與可驗證的較晚 SEC comparative 相符」的證據門檻；
不代表 unexplained 期間沒有重編，只是不猜測。另有 13 個重編／混合基準期間即使重疊值相符，
仍因缺乏共同申報基準證明而略過（basis-unverified）。

禁止欄位 epsDiluted／epsBasic／dilutedShares：0 填入。
全量證明：非空變動 0、季度變動 0、旗標移除 0、revision 變動 0、#164 現值變動 0。

來源快照：6,943 檔、13,886 次 Yahoo 查詢，零錯誤。
SHA-256：`d88c41408528dee77c11ae125cb7550791c42868286a6b61be0ad929091b50b0`。

詳見 [規則與重跑](../YAHOO_ANNUAL_BACKFILL.md)、[逐筆報告](yahoo-annual-backfill.json)、
[獨立證明](yahoo-annual-backfill-proof.json)。
