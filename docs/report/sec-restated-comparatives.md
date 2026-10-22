# SEC 重大重編比較期試跑

僅當較晚申報的營收、淨利或其他損益欄位，相對目前值有嚴格超過 2% 的差異時才切換。未申報欄位保留原值，記錄 basis-unrestated，並對該期間標記 mixed-basis。日期、期間、未切換列及其他 Q4 EPS 原封不動。

同期間須有較晚的年度申報；只有首次年報與現存值不同不視為重編。其他損益欄位含成本、毛利、營業利益、稅前利益、稅費及利息。EPS／股數差異不能觸發切換；拆股／ADR 倍率疑慮的每股欄位保留該列既有基準。

掃描 4,967 份 US 財報；可評估 4,960 份。切換 1,172 個年度期間、580 個股票。
其中營收／淨利觸發：788 個期間、425 個股票；其餘由其他損益欄位觸發。

變動欄位值共 2,151 個；一般欄位設為 null：0。
Q4 EPS 另計：設為 null 0 個值、改為非 null 值 2 個，共影響 2 個值。既有 Q4 EPS 一律不清空；已切換年度欠缺同基準季度時，整組季度值保持不變，標記 quarterly-as-reported，禁止跨基準推導。

以上是試算，未寫入 docs/report/financials/，未執行合併。原 builder 正規化、日期對齊與去重差異完全排除。逐筆原值、新值、申報來源、切換觸發欄位與保留原值稽核均在同名 JSON。

| AD 年度 | 營收原值 | 營收重編值 | 淨利原值 | 淨利重編值 |
| --- | ---: | ---: | ---: | ---: |
| 2023-12-31 | 3,906,000,000 | 100,469,000 | 54,459,000 | 54,459,000 |
| 2024-12-31 | 3,770,000,000 | 102,933,000 | -39,403,000 | -39,403,000 |

| 欄位 | 變動值數 |
| --- | ---: |
| capex | 14 |
| cash | 274 |
| costOfRevenue | 107 |
| dilutedShares | 8 |
| dividendsPaid | 3 |
| epsBasic | 23 |
| epsDiluted | 25 |
| equity | 449 |
| grossProfit | 7 |
| interestExpense | 8 |
| netIncome | 285 |
| operatingCashFlow | 10 |
| operatingIncome | 30 |
| pretaxIncome | 302 |
| revenue | 557 |
| taxProvision | 32 |
| totalAssets | 7 |
| totalDebt | 10 |

每股欄位單獨觸發切換：修正前 379 個期間，修正後 0。
前版非空 annual epsDiluted 變動 376 筆。指定因子相對誤差 ≤1% 可重現 25 筆；提供全部候選逐筆結果於 JSON，另以較寬鬆的倍率距離列出 58 筆供對照（不將其當成已證實拆股）。

| 股票 | 期間 | 原 EPS | 前版候選 | 本版 EPS | 結果 | 嚴格 1% |
| --- | --- | ---: | ---: | ---: | --- | --- |
| ABVC | 2018-12-31 | -3.6 | -0.48 | -3.6 | kept-existing-basis | False |
| AHG | 2023-03-31 | -0.06 | -0.02 | -0.06 | kept-existing-basis | True |
| AIV | 2022-12-31 | 3.87 | 0.49 | 3.87 | kept-existing-basis | False |
| BPOP | 2010-12-31 | -0.06 | -0.62 | -0.06 | kept-existing-basis | False |
| BVC | 2024-06-30 | -0.01 | -0.27 | -0.01 | kept-existing-basis | False |
| BVC | 2025-06-30 | -0.06 | -1.22 | -0.06 | kept-existing-basis | False |
| CAPR | 2018-12-31 | -0.52 | -5.17 | -0.52 | kept-existing-basis | True |
| EDN | 2022-12-31 | -399.2 | -50.3 | -399.2 | kept-existing-basis | True |
| EDN | 2023-12-31 | 1105.6 | 218.73 | 1105.6 | kept-existing-basis | False |
| EHGO | 2024-03-31 | 0.0004 | 0.01 | 0.0004 | kept-existing-basis | True |
| EJH | 2024-06-30 | -11857.5713 | -1196.74 | -11857.5713 | kept-existing-basis | True |
| GGAL | 2022-12-31 | 2228.744 | 223.36 | 2228.744 | kept-existing-basis | True |
| GGAL | 2023-12-31 | 4595.8483 | 497.89 | 4595.8483 | kept-existing-basis | False |
| GRPN | 2018-12-31 | -0.02 | -0.39 | -0.02 | kept-existing-basis | False |
| HAO | 2024-06-30 | 135.68 | 2701.53 | 135.68 | kept-existing-basis | True |
| HAO | 2025-06-30 | 168.96 | 3385.53 | 168.96 | kept-existing-basis | True |
| HEPS | 2022-12-31 | -4.06 | -21.2 | -4.06 | kept-existing-basis | False |
| HTCO | 2021-10-31 | 2.6553 | 0.11 | 2.6553 | kept-existing-basis | False |
| HTCO | 2022-10-31 | 5.8036 | 0.24 | 5.8036 | kept-existing-basis | False |
| ILAG | 2022-12-31 | -1.1 | -0.11 | -1.1 | kept-existing-basis | True |
| JDZG | 2023-12-31 | 776.2564 | 85.3 | 776.2564 | kept-existing-basis | False |
| JDZG | 2024-12-31 | 456.7724 | 45.67 | 456.7724 | kept-existing-basis | True |
| JF | 2022-12-31 | -3.196 | -0.63 | -3.196 | kept-existing-basis | False |
| JZXN | 2024-10-31 | -2641.8 | -264.18 | -2641.8 | kept-existing-basis | True |
| KTTA | 2023-12-31 | -0.42 | -12.64 | -0.42 | kept-existing-basis | False |
| LEU | 2011-12-31 | -4.07 | -102.31 | -4.07 | kept-existing-basis | True |
| LEU | 2012-12-31 | -9.84 | -245.02 | -9.84 | kept-existing-basis | True |
| MLCO | 2024-12-31 | 0.102 | 0.034 | 0.102 | kept-existing-basis | True |
| MVST | 2019-12-31 | -0.07 | -1.17 | -1.17 | income-restatement-on-existing-basis | False |
| MWG | 2021-12-31 | 0.7262 | 0.07 | 0.7262 | kept-existing-basis | False |
| MWG | 2022-12-31 | 0.3171 | 0.04 | 0.3171 | kept-existing-basis | True |
| NCI | 2022-12-31 | 19.3456 | 0.69 | 19.3456 | kept-existing-basis | False |
| NCI | 2024-12-31 | 16.48 | 2.06 | 16.48 | kept-existing-basis | True |
| OCG | 2024-12-31 | -364.32 | -121.44 | -364.32 | kept-existing-basis | True |
| PBM | 2024-03-31 | -14274.2879 | -4674.25 | -14274.2879 | kept-existing-basis | False |
| PPCB | 2020-06-30 | -9.5 | -192.45 | -9.5 | kept-existing-basis | False |
| PRFX | 2022-12-31 | -317.3 | -63.46 | -317.3 | kept-existing-basis | True |
| RGNT | 2023-12-31 | -0.807 | -24.09 | -0.807 | kept-existing-basis | False |
| SFHG | 2024-12-31 | -0.7 | -0.14 | -0.7 | kept-existing-basis | True |
| SPHL | 2023-12-31 | 0.8894 | 0.12 | 0.8894 | kept-existing-basis | False |
| SUGP | 2024-09-30 | 40.9 | 8.18 | 40.9 | kept-existing-basis | True |
| SUPV | 2022-12-31 | -375.2 | -75.04 | -375.2 | kept-existing-basis | True |
| SXTC | 2024-03-31 | -134718.7826 | -4285.66 | -134718.7826 | kept-existing-basis | False |
| SXTC | 2025-03-31 | -2882.7679 | -348.17 | -2882.7679 | kept-existing-basis | False |
| TWAV | 2018-12-31 | -0.15 | -1.5 | -0.15 | kept-existing-basis | True |
| UK | 2021-12-31 | -55545.6 | -5554.51 | -55545.6 | kept-existing-basis | True |
| UK | 2022-12-31 | -7941.2 | -750.81 | -750.81 | income-restatement-on-existing-basis | False |
| UXIN | 2022-03-31 | -36 | -2.07 | -36 | kept-existing-basis | False |
| UXIN | 2024-03-31 | -33.3 | -1.11 | -33.3 | kept-existing-basis | False |
| VSME | 2022-12-31 | 21.4578 | 1.23 | 21.4578 | kept-existing-basis | False |
| WBX | 2023-12-31 | -11.9425 | -0.6 | -11.9425 | kept-existing-basis | False |
| WLDS | 2023-12-31 | -345.6 | -115.2 | -345.6 | kept-existing-basis | True |
| WLDS | 2024-12-31 | -217.8 | -72.6 | -217.8 | kept-existing-basis | True |
| WNW | 2021-12-31 | -29.2972 | -0.97 | -29.2972 | kept-existing-basis | False |
| WNW | 2022-12-31 | -174.3817 | -10.04 | -174.3817 | kept-existing-basis | False |
| YXT | 2023-12-31 | -38.2697 | -4.71 | -38.2697 | kept-existing-basis | False |
| ZNB | 2024-06-30 | -128537.512 | -1343217 | -1343217 | income-restatement-on-existing-basis | False |
| ZNB | 2025-06-30 | -3922.4168 | -89400.12 | -3922.4168 | kept-existing-basis | False |

無法評估（不代表沒有差異）：FITB: revenue-scope-mismatch；HBAN: revenue-scope-mismatch；HONA: us-gaap-no-usable-facts；IBKR: revenue-scope-mismatch；MTB: revenue-scope-mismatch；SCNI: currency-mismatch: no cross-currency comparison；VYLR: no-us-gaap。
所有欄位（含 Q4 EPS）設為 null 均為 0。quarterly-as-reported 的季度維持原申報序列，並保留禁止跨年度基準推導的理由。
