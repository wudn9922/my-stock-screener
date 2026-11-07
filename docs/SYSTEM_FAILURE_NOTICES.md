# 系統故障與資料未更新通知

這項變更只交付 PR。沒有合併、部署、觸發正式 workflow，也沒有發送真實 Discord／LINE 訊息。離線測試在子程序內替換 HTTP transport，所有測試憑證都是假值。

## 通道與故障處理

正式工作新增一個獨立的 `notify-system-failure` job，等待所有原有 jobs，以 `failure() || cancelled()` 或已收集的部分失敗結果決定是否通知。原有報告、資料計算、發布條件與價格推播不變；每日 Screener／Dify 的正常 LINE 報告仍走原本步驟。

2026-11-06 修訂：取消不是直接發送的充分條件。in-run helper 與 workflow_run fallback 都先用同一個 `run_policy.py` 查核。failure、失敗 job、run／job／step timed_out 一律通知；cancelled 且無失敗／逾時，只有找不到同 workflow_id／branch／event、created_at 更晚的 run 才通知。被取代只記 log；排隊中根本沒有 job 的 run 也適用。API／格式／分頁查核失敗採保守通知，沒有因 metadata 故障略過真正故障。

共用 [.github/actions/notify-failure](../.github/actions/notify-failure/README.md) 先使用 **`DISCORD_WEBHOOK_URL1`**，未設定或失敗才使用既有 `LINE_ACCESS_TOKEN`／`LINE_USER_ID` 故障備援。沒有使用、讀取或修改既有價格警示 `DISCORD_WEBHOOK_URL`。Webhook 和 token 只從 secrets 經 action input／環境變數傳入；不在命令列、log、cache 或 artifact 輸出。

Embed 只含 workflow、觸發事件、失敗 job／step、run URL、台北時間，資料監測另含公開來源的日期／狀態。停用 mentions，不下載 job logs，不轉送原始例外／API 回應。Actions token 只需 `actions: read` 與讀取程式碼的 `contents: read`；呼叫端只明確傳三個通知 secret，不使用 `secrets: inherit`。

通知 helper 的 job 和各步驟皆容許通知程序失敗，Python 正常處理發送錯誤後 exit 0。兩個管道皆失敗只寫固定 warning，不把成功的資料工作改成失敗。

## 每個 workflow 的變更

所有下列工作都新增失敗／取消通知；有 `continue-on-error` 的工作，最後收集步驟的 **outcome**（不是會被改成 success 的 conclusion），發一則「部分失敗」，不改變原工作結論。

| Workflow | 通知變更／部分失敗步驟 |
| --- | --- |
| `atlas.yml` | 失敗／取消；部分失敗：Build full-market symbol directory (TWSE / TPEx / US)、Build US valuation data (Yahoo Finance)、Build US growth data (Yahoo Finance)、Build Taiwan annual EPS (Yahoo Finance)、Build Taiwan valuation data (TWSE / TPEx)、Build US earnings dates (Yahoo Finance)、Restore full-market daily bars for saved watchlists (read-only)、Refresh saved watchlist observations |
| `breadth-calibration.yml` | 失敗／取消 |
| `cloud-run.yml` | 失敗／取消 |
| `daily-watchdog.yml` | 失敗／取消；新增資料過期／恢復／每日缺報檢查，通知與 cache 失敗只警告 |
| `data-status.yml` | 失敗／取消 |
| `financials.yml` | 失敗／取消 |
| `fundamentals.yml` | 失敗／取消；部分失敗：Taiwan official statements (MOPS / OpenAPI)、US SEC XBRL frames、Taiwan history (FinMind, resumable)、Taiwan statements (FinMind, resumable) |
| `generated-pages.yml` | 失敗／取消 |
| `generated-squash.yml` | 失敗／取消 |
| `keepalive.yml` | 失敗／取消 |
| `kline-archive-backfill.yml` | 失敗／取消 |
| `kline-archive-five-years.yml` | 失敗／取消 |
| `ma-recommendations.yml` | 失敗／取消 |
| `market-calendar.yml` | 失敗／取消 |
| `market-extras.yml` | 失敗／取消 |
| `q4-8k-backfill.yml` | 失敗／取消 |
| `refresh-us-rates.yml` | 失敗／取消；部分失敗：Rebuild Japan spreads and monthly reasonable bands |
| `research-data.yml` | 失敗／取消 |
| `schedule.yml` | 失敗／取消；部分失敗：Build theme rotation、Build US rates research、Build Japanese spreads and monthly yield bands |
| `supabase-functions.yml` | 失敗／取消 |
| `themes-tw.yml` | 失敗／取消 |
| `trim-history.yml` | 失敗／取消 |
| `tw-chips.yml` | 失敗／取消 |
| `universe-full.yml` | 失敗／取消；部分失敗：Shallow checkout permanent K-line archive、Seed missing rolling bars from permanent archive、Refresh archive-only ETFs and indices through the existing Yahoo batch reader、Combine archive-only instruments with the accepted stock payload、Build breadth trading signals、Build market risk warnings |

原本的 `atlas.yml`、`cloud-run.yml`、`schedule.yml`、`supabase-functions.yml` 內嵌 LINE 故障 curl 移除，改用同一 helper。`daily-watchdog.yml` 的故障／缺報訊息也經同一 action。GCP 容器、breakout_alert 程式、SQL、Supabase 函式、登入／帳號與永久資料沒有改動。

PR review 工作 **atlas-review、research-review、generated-verify、kline-archive-review** 不通知；新增的離線 `failure-notice-review` 也不通知。正常 PR 檢查沒有通知 secret，也不觸發正式資料排程。

## 取消執行與去重回執

`notify-failure.yml` reusable helper 從 main checkout 通知 action，使用獨立 runner，不干擾生產者的 checkout。成功送達後，上傳只含公開 run ID 的一般檔案 `notification-delivered.txt`，artifact 名稱 `system-failure-notice-{run_id}`，保存一天；不含訊息或憑證。

手動取消整個 run 可能中止清理 job，因此 `failure-notice-fallback.yml` 另以 `workflow_run: completed` 監看上表正式工作。原執行為失敗／取消／逾時，且沒有送達回執時才補送；它只 checkout **main**，只讀原 run 的 job／step metadata 和 artifact 名稱，不執行原分支或 artifact 程式。不監看 PR 事件、外部 fork 或通知 helper，避免迴圈。

補送也會執行「較新執行取代」判定，沒有繞過。查詢使用 `actions: read`：先讀原 run 和所有 jobs，再讀該 workflow 的 runs（branch、event、created 篩選），同時在程式內重新核對欄位；比較的是建立時間，不以較大 run ID 猜測順序。job 和候選 run 都支援分頁，超過查核上限或回應不完整時不抑制通知。

回執與去重是 best effort：若送達後 runner 在寫回執前中止、artifact API 不可用或回執過期，取消備援可能重複一次。若通知服務和 runner 都不可用，系統無法保證送達；這不會改動生產者結論。

## 每日資料日期檢查

`daily-watchdog.yml` 每天 UTC 17:23（台北隔天 01:23）執行。原本每日報告的「台北時間減六小時」查詢窗口保留；沒有排程報告的週六略過缺報檢查，資料日期則七天都檢查。沒有為本 PR 手動觸發它。

[scripts/check_data_freshness.py](../scripts/check_data_freshness.py) 只透過 GitHub GET 讀取：

| 來源 | 市場／資料日期 | 異常條件 |
| --- | --- | --- |
| kline-archive `manifest.json` | `markets.tw/us.lastDate` | 落後已收盤交易日超過 1 個交易日 |
| `market-warnings.json` | `markets.tw/us.asOf`、`update.status` | 日期落後超過 1 日，或 retained／unavailable |
| `breadth-signals.json` | `markets.us.asOf` | 日期落後超過 1 日（現行檔僅 US） |
| `breakouts.json` | `markets.tw/us.asOf` | 日期落後超過 1 日 |
| `universe.json` | `markets.TW/US.asOf` | 日期落後超過 1 日 |
| schedule.yml 執行結果 | 台北報告日的 runs | 沒有成功執行，區分未執行／仍在執行／未成功 |

資料分支依 **`vars.GENERATED_BRANCH_MODE`** 決定 generated 或 main，封存一律只讀 kline-archive。交易日只使用已發布的 `market-calendar.json`，依每個 session 的 Unix close 時間處理時區、假日、週末與提前收盤，不猜星期規則，也不把今天盤中當成已收盤。

日曆需涵蓋市場當地今天；日曆讀不到、過期或沒有已收盤 session 時，通知「交易日曆不可用」，不把未檢查來源誤報為已恢復。來源缺檔／缺資料日也會明確列異常。日曆只保留約30天過去資料，長時間落後以已知 session 計算「至少落後 N 日」，仍可判定超過1日；不宣稱精確總天數。

### 同日一次與恢復通知

每個來源／市場（以及每日報告）各有穩定 problem key。以台北日期計算，每個問題同日只送一次；隔日仍異常可以再提醒。恢復時發「已恢復」，重複健康檢查不再發。同一問題同日反覆恢復／異常也維持每天各一則的上限。

小狀態檔只有 problem key、日期、active、公開資料摘要，以 Actions cache `data-health-notices-v1-` 保存。concurrency 序列化此監測；restore 舊 key、每次以 run ID／attempt 存新 key。不使用 generated 或永久資料存狀態。

先產生 proposed／events，**任一管道接受通知後才 commit 狀態並存 cache**；都失敗則不標記已通知，下次可以重試。沒有事件的健康檢查可直接保存狀態；壞 cache 不阻擋檢查，也不捏造恢復。只 cache `state.json`，不 cache token、行情 payload 或持股。

Actions cache 被清除／驅逐會失去去重記錄；下一次會重新提醒目前異常，且無法補發失去記錄前的恢復。這是選用 cache 的限制，不是永久通知日誌。此 PR 不寫入、修復或清除任何行情／研究產物。

## 維護者上線順序與驗證

1. 審查並自行決定合併此 PR。helper、action、備援與生產者必須一起進 main，因為正式通知程式碼從 main 讀取。
2. 維護者確認 repository Actions secret `DISCORD_WEBHOOK_URL1` 已設定；若要 LINE 故障備援，保留原兩個 LINE secret。不要更換或傳入價格警示 webhook。
3. 等待正常排程；觀察失敗的 job／step 摘要與 run 連結、通知 helper 結論、僅含 run ID 的回執，以及每日日期檢查的計數。通知發送錯誤只會 warning。

不需要 SQL、資料庫、Supabase 部署或 GCP 變更。PR 實作期間只跑離線測試，不傳真實訊息、不 dispatch 正式工作；真實送達與 runner 中止時序仍需維護者在正式啟用後確認。

## 離線驗證

```sh
python -m unittest discover -s tests -p test_failure_notifications.py -v
python -m unittest discover -s tests -p test_data_freshness.py -v
python -m unittest discover -s tests -p test_cancelled_notification_policy.py -v
actionlint
```

通知測試真的啟動 action 的 Python entry point，但替換所有 HTTP：Discord 成功不發 LINE、Discord 失敗退回 LINE、兩者失敗只 warning 且 exit 0、缺 secret、URL／敏感字串遮蔽、只讀 job metadata、可上傳回執、全工作涵蓋、正常 LINE 報告保留，以及真實 shell 部分失敗收集器。日期測試含過期／正常／一天寬限、台北日期去重、恢復、提早收盤、假日／週末、盤中、來源／日曆不可用、cache 損壞及 prepare／commit 流程。

未放寬、略過或更改任何既有測試。完整實測結果記於 PR；只有 primary self-review，未聲稱獨立審核。

本次本機實測（2026-11-06，基於 main `e2ea0322c5`）：

| 檢查 | 結果 |
| --- | --- |
| `test_failure_notifications.py` | 13 通過 |
| `test_data_freshness.py` | 13 通過 |
| 既有 `test_market_calendar.py` | 8 通過 |
| 既有 `test_generated_branch.py` | 35 通過 |
| 既有 `test_kline_archive_expansion.py` | 17 通過 |
| 既有 `test_research_operations.py` | 11 通過 |
| 既有 `test_data_status.py` | 13 通過 |
| 既有 `test_market_extras.py` | 7 通過 |
| 合計 | **117 通過，0 失敗** |
| actionlint 1.7.7 | 全部 workflow 通過 |
| `git diff --check` | 通過 |
| 原生產者 steps 與 main 的 YAML 結構比對 | 除指定的故障通知替換、soft-step ID、收集器與 watchdog 外無更動；正常報告與計算／發布步驟相同 |

未變更任何前端檔案，這個 PR 無畫面變更或瀏覽器互動。GitHub CI 仍以 PR 最終 head 的 checks 為準，不把尚未完成的結果預記為通過。

## 21 次取消的唯讀回放

資料取自 GitHub Actions metadata 的 GET，查詢日期 `2026-10-30..2026-11-06`，排除 PR 事件後共有 **21 次**，全部為 Deploy assembled Pages。每筆都核對 jobs（其中一筆排隊取消，沒有 jobs）以及一筆同 workflow／branch／event、建立較晚的實際後續 run。共用決策的離線回放為 **21 筆略過、0 則通知**，沒有為驗證發真實訊息。

為避免日期窗口混淆：以 11/06 18:30:46 UTC 往前精確168小時計為17筆；本報告保留題述21筆所對應的10/30起始日期窗口，兩者均為0通知。來源 fixture、每筆 successor／結論與可點擊 run URL 都納入版本控制。

[回放報告](research-verification/cancelled-notices-20261106.json)、[原始精簡 metadata](../tests/fixtures/notifications/cancelled-week-20261106.json)。可離線重現：

```sh
python scripts/replay_cancelled_notifications.py \
  --input tests/fixtures/notifications/cancelled-week-20261106.json \
  --out /tmp/cancelled-notices-replay.json
```

新增單元測試涵蓋被取代／無後續／不同分支事件與 workflow／相同建立時間、failure、三種逾時、API 失敗只送一次、in-run／fallback 相同規則、job／run 第二頁，以及21筆真實形狀回放。所有發送 transport 都是假的；合計37項通知／健康／取代規則測試與全部 workflow 的 actionlint 已通過。
