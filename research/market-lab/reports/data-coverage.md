# 資料覆蓋率

- 來源 repo main SHA：`7e0a1fa12ac74d3d848fa88ea7ad7d47369645b2`；執行日期：2026-10-19。
- 原始日 K 欄位：Date、Open、High、Low、Close、Volume。來源程式對 Yahoo 設 `period=1y`、`auto_adjust=False`、`actions=False`。
- 覆蓋率逐檔資料見 [per-symbol-coverage.csv](per-symbol-coverage.csv)，品質旗標見 [quality-issues.csv](quality-issues.csv)。

## 市場摘要

| 市場 | 個股檔數 | 最早至最新日 | 每檔根數中位數 | 少於 252 根 | 覆蓋滿 9 年 | 財報檔覆蓋 | 有標準化公告時間 |
|---|---:|---|---:|---:|---:|---:|---:|
| US | 527 | 2025-10-06 – 2026-10-16 | 206 | 481 | 0 | 527/527 | 0/527 |
| TW | 1019 | 2025-08-22 – 2026-10-19 | 206 | 995 | 0 | 1013/1019 | 0/1019 |

## 判讀與限制

repo 個股日 K 幾乎都只有約一年，不能把一年的資料稱為 2016–2025 十年樣本。空 CSV、短歷史、週末列、重複日期、OHLC 不一致、極端價格跳動與量能離群均逐檔列入品質表。`weekday_gap_upper_bound` 會把交易所假日也算入可能缺口，因此僅是上界，不稱為精確缺失交易日。

價格 CSV 沒有 Adjusted Close、股利或拆股事件欄。拆股回溯狀態標為未驗證，股利調整標為否；可能受到公司行動影響的超過 50% 價格跳動會從對應前瞻報酬區間排除並列為品質警告。策略績效因此仍不是完整總報酬。

財報檔的年度／季度列數僅表示目前快照可取得的期間數。美股標準化季度資料有 period-end，沒有逐筆 filing/acceptance timestamp；台股季資料亦沒有逐筆公告時點。兩者不能作為當時已知的財務事件訊號。資料檔存在不證明該公司在完整歷史期間持續上市；目前 repo 沒有全歷史點時 universe、下市報酬或退市處理。

只有 `^TWOII` OTC 指數序列可用；本報告僅對有共同交易日的上櫃股票估計相對櫃買指數報酬。美股及台灣上市股沒有可匹配的市場指數序列，超額報酬與 bull/bear/sideways 市場分類不估。

## 全域品質問題

| 嚴重度 | 旗標 | 檔數 |
|---|---|---:|
| high | `possible_extreme_price_discontinuity` | 16 |
| high | `very_short_history` | 6 |
| high | `empty_file` | 4 |
| high | `corporate_action_adjustment_unverified` | 1 |
| high | `costs_are_scenarios` | 1 |
| high | `financial_announcement_timestamps_missing` | 1 |
| high | `historical_universe_unavailable` | 1 |
| high | `invalid_ohlc_rows_removed` | 1 |
| high | `market_breadth_uses_current_snapshot` | 1 |
| high | `no_2016_2025_equity_history` | 1 |
| low | `extreme_volume_spike` | 692 |
| medium | `less_than_252_bars` | 1466 |
| medium | `financial_history_missing` | 6 |
| medium | `taiwan_listed_benchmark_missing` | 1 |
| medium | `us_benchmark_missing` | 1 |
