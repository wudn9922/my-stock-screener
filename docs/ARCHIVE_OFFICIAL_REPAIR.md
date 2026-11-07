# 官方封存修補：前置核對與待辦

2026-11-07 從最新 main `22ffc817f` 建立 `codex/official-archive-repair`。GitHub API 兩次確認 #230 仍為 OPEN、`mergedAt=null`，head 為 `37cc1f333a9386d3762c52c82f76e5b93b360f00`；main 尚無該 PR 的交易日曆、verified-market-closures、缺漏掃描器。尚未將未合併的 #230 納入分支，等待確認要沿 main 等待合併，或改納入 #230。

## 已完成的獨立工作

- #236：同 workflow_id、head_branch、created_at 嚴格較晚即可取代，不限制 event；API 查詢移除 event 篩選。failure／timed_out 優先通知與 API 不確定時保守通知一次不變。
- 原 21 次取消 metadata 的離線回放，加入原本已保存的跨 event 候選，以取消完成時間過濾，21 次抑制、0 通知；另測未來候選不得抑制當時通知。
- 官方實際回應欄位核對及四個待查日查證，見 [官方證據](research-verification/official-market-days-20261107.json)。四日 TWSE 明確無資料、TPEx 回應日期正確且行情空白，均應加入已驗證休市。
- 固定封存 `62bd86a6e7fcde3572c56aa607eed5018a0cfe68` 的唯讀盤點，10/23 正好 2,214 筆符合條件，差異 0%；見 [盤點摘要](research-verification/placeholder-preview-20261023.json)。沒有寫入該本機封存、遠端分支或 dispatch workflow。

## 已核對官方端點

| 用途 | 端點與參數 | 實際欄位 |
| --- | --- | --- |
| 上市 OHLCV | `https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date=YYYYMMDD&type=ALLBUT0999` | tables 中每日收盤行情：證券代號、成交股數、開盤價、最高價、最低價、收盤價 |
| 上櫃 OHLCV | `https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=民國年%2FMM%2FDD&id=&response=json` | tables：代號、成交股數、開盤、最高、最低、收盤；回應外層為西元日，table.date 為民國日 |
| ^TWII 月歷史 OHLC | `https://www.twse.com.tw/indicesReport/MI_5MINS_HIST?response=json&date=YYYYMMDD` | 日期、開盤指數、最高指數、最低指數、收盤指數；日期為民國格式，須選精確目標日 |

`exchangeReport/MI_5MINS_HIST` 與 `rwd/zh/afterTrading/MI_5MINS_HIST` 在核對時回 HTML，不應當 JSON 解析。可用的官方 indicesReport 端點不提供成交量，後續實作應保留未知量或另取官方可證明的指數成交量，不能造零量。

## 尚未完成，不得視為可套用的資料修補工具

- 官方補缺漏腳本與預設 dry-run workflow、兩側各二個交易日的價格基準檢查、原寫入路徑與前後驗證。
- 10/23 刪除模式、apply 前完整備份、逐位元組還原模式及測試。
- 待查四日加入 #230 的 verified-market-closures，並驗證缺漏掃描與下游圖表投影。
- 正式資料 apply。維護者審查後自行執行；本次不直接操作正式資料。

基準因子應明確定義為 archive.close / official.close，四個鄰近日都須有官方與封存資料；資料不足或比值不穩定跳過。OHLC 乘因子、成交量除因子。停牌、缺價 `--`、無成交統一跳過，報告原因；不猜價格或填造平盤。

## 已完成部分的本機驗證

[驗證報告](research-verification/archive-repair-prerequisite-checks-20261107.json)：Python 全套 pytest 1,941 passed、168 subtests passed；其中既有需額外啟用的壓力測試已另以 `KLINE_ARCHIVE_PERFORMANCE=1` 跑過，1 passed。7,000 檔、21,000 年檔、224 萬筆衝突，apply 149.940 秒、verify 76.129 秒，均低於原 300 秒門檻。該測試只向臨時目錄內的 bare Git remote 寫入。

取消策略 14 tests passed；Vitest 158 files／2,017 tests passed；lint、build、diff check 通過。固定正式封存快照的唯讀驗證（含 HEAD^ 轉換）通過：41,573 年檔、9,796,080 列，含 global 市場；驗證前後快照 git status 乾淨。尚未進行修補，這不是修補後驗證。
