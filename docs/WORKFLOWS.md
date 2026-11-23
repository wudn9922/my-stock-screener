# GitHub Actions workflow 對照表

排程以 UTC cron 表示，括號內是台灣時間（UTC+8）。「手動」指 `workflow_dispatch`。「失敗通知」欄記錄是否直接呼叫 [`notify-failure.yml`](../.github/workflows/notify-failure.yml)，通知規則與備援機制見 [系統故障與資料未更新通知](SYSTEM_FAILURE_NOTICES.md)。

## 核心每日管線

| 檔名 | 名稱 | 分類 | 觸發方式 | 用途 | 失敗通知 |
|---|---|---|---|---|---|
| [`schedule.yml`](../.github/workflows/schedule.yml) | Daily Stock Screener | 核心每日管線 | cron `47 8 * * 0-5`（週日～週五 16:47，備援）；手動（外部排程於同日 17:00 呼叫） | 更新行情、產生每日選股報告並推播 LINE。 | 是 |
| [`atlas.yml`](../.github/workflows/atlas.yml) | Atlas Research Terminal | 核心每日管線 | `workflow_run`：Daily Stock Screener 完成；push `main` 的 `atlas/**`、`.github/workflows/atlas.yml`；手動 | 更新 Atlas 研究資料並建置 Atlas 與 LIFF 頁面。 | 是 |
| [`universe-full.yml`](../.github/workflows/universe-full.yml) | Full-Market Universe | 核心每日管線 | `workflow_run`：Daily Stock Screener 完成；cron `17 3 * * 1-6`（週一～週六 11:17）；手動 | 更新台美股全市場掃描與市場寬度，並追加已收盤日 K 至永久封存。 | 是 |
| [`financials.yml`](../.github/workflows/financials.yml) | Financial Statement History | 核心每日管線 | `workflow_run`：Daily Stock Screener 完成；手動 | 更新財報歷史與選股用 EPS、營收年增率。 | 是 |
| [`fundamentals.yml`](../.github/workflows/fundamentals.yml) | Official Fundamentals (TW / US) | 核心每日管線 | `workflow_run`：Financial Statement History 完成；cron `37 19 * * *`（每日 03:37，UTC 隔日）；手動 | 補齊台美股官方基本面資料並更新選股成長指標。 | 是 |
| [`themes-tw.yml`](../.github/workflows/themes-tw.yml) | Taiwan Theme Rotation | 核心每日管線 | `workflow_run`：Daily Stock Screener 完成；手動 | 更新台股題材輪動資料。 | 是 |
| [`data-status.yml`](../.github/workflows/data-status.yml) | Data status | 核心每日管線 | `workflow_run`：`main` 的 Daily Stock Screener、Full-Market Universe 完成；cron `35 0,10,15 * * *`（每日 08:35、18:35、23:35）；手動 | 發布資料健康清單、產業輪動比較與選股歷史快照。 | 是 |
| [`generated-pages.yml`](../.github/workflows/generated-pages.yml) | Deploy assembled Pages | 核心每日管線 | push `main` 或 `generated`（無路徑篩選）；手動；`workflow_call`；`workflow_run`：[下列上游完成](#pages-組裝的上游) | 組裝 `main` 與 `generated` 的內容並部署 GitHub Pages。 | 是 |
| [`daily-watchdog.yml`](../.github/workflows/daily-watchdog.yml) | Daily Report Watchdog | 核心每日管線 | cron `23 17 * * *`（每日 01:23，UTC 隔日）；手動 | 檢查上一期每日報告與已收盤市場資料的日期，發送未更新或恢復通知。 | 是 |

### Pages 組裝的上游

Daily Stock Screener、Atlas Research Terminal、Financial Statement History、Official Fundamentals (TW / US)、Taiwan stock dividends backfill、Full-Market Universe、Research public data、Market trading calendar、IPO and extended quotes、Taiwan Chips、Taiwan Theme Rotation、Refresh US Rates、MA Recommendations、TW Margin Temperature、Squash generated snapshot。

## 定期資料更新

| 檔名 | 名稱 | 分類 | 觸發方式 | 用途 | 失敗通知 |
|---|---|---|---|---|---|
| [`breadth-calibration.yml`](../.github/workflows/breadth-calibration.yml) | Recalibrate US breadth signals | 定期資料更新 | cron `0 8 1 1,4,7,10 *`（每季首月 1 日 16:00）；手動 | 重新校準美股市場寬度訊號並檢查樣本與成交覆蓋。 | 是 |
| [`ma-recommendations.yml`](../.github/workflows/ma-recommendations.yml) | MA Recommendations | 定期資料更新 | cron `17 7 2 * *`（每月 2 日 15:17）；手動 | 分批重算股票的建議均線參數。 | 是 |
| [`margin-temperature.yml`](../.github/workflows/margin-temperature.yml) | TW Margin Temperature | 定期資料更新 | cron `30 14 * * 1-5`（週一～週五 22:30）；手動 | 更新台股融資溫度快照。 | 是 |
| [`market-calendar.yml`](../.github/workflows/market-calendar.yml) | Market trading calendar | 定期資料更新 | cron `20 0 * * *`（每日 08:20）；手動 | 產生並發布未來台美股交易日曆。 | 是 |
| [`market-extras.yml`](../.github/workflows/market-extras.yml) | IPO and extended quotes | 定期資料更新 | cron `41 12,13,21,22,1 * * 1-6`（週一～週六 09:41、20:41、21:41，週二～週日 05:41、06:41）；手動 | 在美股交易日更新 IPO 和盤前盤後報價快照。 | 是 |
| [`refresh-us-rates.yml`](../.github/workflows/refresh-us-rates.yml) | Refresh US Rates | 定期資料更新 | push `main` 的 `.github/workflows/refresh-us-rates.yml`、`scripts/build_rates.py`、`scripts/rates_analytics.py`、`scripts/rates_config.py`、`tests/test_rates.py`、`scripts/build_bond_research.py`、`tests/test_bond_research.py`、`atlas/data/yield-band-params.json`；手動 | 重建美國公債利率與債券研究報告。 | 是 |
| [`research-data.yml`](../.github/workflows/research-data.yml) | Research public data | 定期資料更新 | cron `23 11 * * 1-5`（週一～週五 19:23）、`23 5 * * 2-6`（週二～週六 13:23）；push `main` 的 `scripts/*research*.py`、`scripts/build_sec_radar.py`、`scripts/build_world_assets.py`、`.github/workflows/research-data.yml`；手動 | 更新法人動向、成交量、警示、全球資產與 SEC 研究資料。 | 是 |
| [`tw-chips.yml`](../.github/workflows/tw-chips.yml) | Taiwan Chips | 定期資料更新 | cron `41 11 * * 1-5`（週一～週五 19:41）；手動 | 更新台股籌碼資料，包含外資持股。 | 是 |

## 部署

| 檔名 | 名稱 | 分類 | 觸發方式 | 用途 | 失敗通知 |
|---|---|---|---|---|---|
| [`cloud-run.yml`](../.github/workflows/cloud-run.yml) | Deploy Cloud Run (breakout alert) | 部署 | push `main` 的 `breakout_alert/**`、`Dockerfile.breakout`、`Dockerfile.discord-query`、`requirements.txt`、`requirements.discord-query.txt`、`.github/workflows/cloud-run.yml`；手動 | 建置並部署突破監控與 Discord 查詢服務至 Cloud Run。 | 是 |
| [`sinopac-sync.yml`](../.github/workflows/sinopac-sync.yml) | Deploy read-only SinoPac sync | 部署 | push `main` 的 `sinopac_sync/**`、`Dockerfile.sinopac-sync*`、`data/*.TW.csv`、`data/*.TWO.csv`、`scripts/build_market_calendar.py`、`.github/workflows/sinopac-sync.yml`；手動 | 建置並部署永豐唯讀持股同步服務至 Cloud Run。 | 是 |
| [`statement-sync.yml`](../.github/workflows/statement-sync.yml) | Deploy statement sync | 部署 | push `main` 的 `statement_sync/**`、`Dockerfile.statement-sync*`、`.github/workflows/statement-sync.yml`；手動 | 建置並部署 Email 對帳單同步服務至 Cloud Run。 | 是 |
| [`supabase-functions.yml`](../.github/workflows/supabase-functions.yml) | Deploy Supabase Edge Functions | 部署 | push `main` 的 `supabase/functions/market-chart/**`、`supabase/functions/user-settings/**`、`supabase/functions/_shared/**`、`supabase/functions/chart-drawings/**`、`supabase/functions/monitor-signals/**`、`.github/workflows/supabase-functions.yml`；手動 | 部署並檢查行情圖、使用者設定、畫線與監控訊號 Edge Functions。 | 是 |

## PR 驗證

PR 的路徑篩選以各 workflow 連結內的 `on.pull_request.paths` 為準。

| 檔名 | 名稱 | 分類 | 觸發方式 | 用途 | 失敗通知 |
|---|---|---|---|---|---|
| [`atlas-review.yml`](../.github/workflows/atlas-review.yml) | Atlas PR verification | PR 驗證 | PR 至 `main`（限定路徑） | 驗證 Atlas 建置、單元與瀏覽器回歸，以及資料和帳戶儲存整合。 | 否 |
| [`failure-notice-review.yml`](../.github/workflows/failure-notice-review.yml) | System notification verification | PR 驗證 | PR（限定路徑） | 離線驗證故障通知、取消通知規則和資料日期檢查。 | 否 |
| [`kline-archive-review.yml`](../.github/workflows/kline-archive-review.yml) | Permanent K-line archive verification | PR 驗證 | PR 至 `main`（限定路徑） | 驗證永久日 K 封存格式、追加與修正規則，以及每日追加效能。 | 否 |
| [`research-review.yml`](../.github/workflows/research-review.yml) | Research Python verification | PR 驗證 | PR 至 `main`（限定路徑） | 在隔離環境執行研究資料相關的 Python 測試。 | 否 |
| [`sinopac-sync-test.yml`](../.github/workflows/sinopac-sync-test.yml) | Read-only SinoPac tests | PR 驗證 | PR（限定路徑）；手動 | 用模擬資料驗證永豐唯讀服務、Edge Function 和 Atlas 持股介面。 | 否 |
| [`statement-sync-test.yml`](../.github/workflows/statement-sync-test.yml) | Statement sync tests | PR 驗證 | PR（限定路徑）；手動 | 驗證對帳單解析、同步服務、PostgreSQL 套用邏輯和 Edge Function。 | 否 |

## 維護

| 檔名 | 名稱 | 分類 | 觸發方式 | 用途 | 失敗通知 |
|---|---|---|---|---|---|
| [`generated-squash.yml`](../.github/workflows/generated-squash.yml) | Squash generated snapshot | 維護 | cron `11 22 * * *`（每日 06:11，UTC 隔日）；手動 | 壓縮 `generated` 分支歷史並保留當前檔案樹。 | 是 |
| [`generated-verify.yml`](../.github/workflows/generated-verify.yml) | Generated storage verification | 維護 | PR 至 `main`（無路徑篩選）；手動 | 驗證產出物分支的儲存規則、Pages 組裝內容與網址。 | 否 |

## 一次性／手動回補

| 檔名 | 名稱 | 分類 | 觸發方式 | 用途 | 失敗通知 |
|---|---|---|---|---|---|
| [`kline-archive-backfill.yml`](../.github/workflows/kline-archive-backfill.yml) | Permanent K-line Backfill | 一次性／手動回補 | 手動 | 從 Yahoo 分批補齊永久日 K 封存缺漏，並保留來源衝突紀錄。 | 是 |
| [`kline-archive-five-years.yml`](../.github/workflows/kline-archive-five-years.yml) | Permanent K-line five-year expansion | 一次性／手動回補 | 手動 | 將永久日 K 封存補至五個完整年度加上當年。 | 是 |
| [`kline-archive-official-repair.yml`](../.github/workflows/kline-archive-official-repair.yml) | Official TW archive repair | 一次性／手動回補 | 手動 | 預檢並選擇是否套用台股官方日 K 補檔、還原或加權指數成交量修正。 | 是 |
| [`q4-8k-backfill.yml`](../.github/workflows/q4-8k-backfill.yml) | Q4 EPS from 8-K (manual) | 一次性／手動回補 | 手動 | 診斷或回測 SEC 8-K 盈餘解析，或補齊缺漏的美股第四季 EPS。 | 是 |
| [`tw-stock-dividends.yml`](../.github/workflows/tw-stock-dividends.yml) | Taiwan stock dividends backfill | 一次性／手動回補 | 手動 | 從 FinMind 分批補齊台股股票股利及減資資料。 | 是 |

## 共用 helper

| 檔名 | 名稱 | 分類 | 觸發方式 | 用途 | 失敗通知 |
|---|---|---|---|---|---|
| [`notify-failure.yml`](../.github/workflows/notify-failure.yml) | System failure notification helper | 共用 helper | `workflow_call` | 呼叫共用 action 發送 Discord 故障通知，必要時改送 LINE，並保存送達紀錄。 | 否（本身提供通知功能） |
| [`failure-notice-fallback.yml`](../.github/workflows/failure-notice-fallback.yml) | System failure notification fallback | 共用 helper | `workflow_run`：[下列上游完成](#故障通知備援的上游) | 上游失敗、取消或逾時且無送達紀錄時，直接呼叫共用通知 action 補送。 | 否（直接呼叫 action） |

### 故障通知備援的上游

Deploy read-only SinoPac sync、Deploy statement sync、Atlas Research Terminal、Recalibrate US breadth signals、Deploy Cloud Run (breakout alert)、Daily Report Watchdog、Data status、Financial Statement History、Official Fundamentals (TW / US)、Deploy assembled Pages、Squash generated snapshot、Official TW archive repair、Permanent K-line Backfill、Permanent K-line five-year expansion、MA Recommendations、Taiwan stock dividends backfill、Market trading calendar、IPO and extended quotes、Q4 EPS from 8-K (manual)、Refresh US Rates、Research public data、Daily Stock Screener、Deploy Supabase Edge Functions、Taiwan Theme Rotation、Taiwan Chips、Full-Market Universe、TW Margin Temperature。
