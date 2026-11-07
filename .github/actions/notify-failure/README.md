# 系統故障通知（Discord 優先）

只傳工作名稱、觸發方式、失敗 job／step 名稱、公開資料日期、run 連結與台北時間。不讀取 logs、行情內容、持股或帳號資料。

呼叫端將 `secrets.DISCORD_WEBHOOK_URL1` 傳入 `discord-webhook`。此 action 不讀取原本的價格警示 webhook；不要用 `secrets: inherit` 把其他 secret 傳入共用 workflow。Webhook、LINE token／收件人、GitHub token 都只透過環境變數傳遞，不印出、不寫 artifact；例外內容及 HTTP 回應 body 也不記錄。

1. 使用 Discord embed，停用 mentions；只允許 Discord HTTPS webhook，不跟隨 redirect。
2. Discord 未設定或發送失敗，才向原有 LINE 故障通知收件人發送。
3. 任一管道接受後輸出 `delivered=true`；否則只有固定 `::warning::`、`delivered=false`，程式一律 exit 0。呼叫端仍應保留 `continue-on-error: true`，涵蓋 runner／action 啟動失敗。

系統工作與取消備援都設定 `check-superseded: true`，共用 `run_policy.py`：failure／任何失敗 job／run、job 或 step 的 timed_out 一律通知；只有 cancelled 且沒有失敗／逾時時，才查同 workflow_id、head_branch 且建立時間嚴格較晚的 run。有後續 run 就只記公開 run ID 的略過 log，不呼叫任一通知管道。沒有後續 run、workflow／分支不同、同秒建立不算取代；push 與 workflow_run 可互相取代。讀 metadata／分頁／格式失敗時保守送一次，仍沿用送達回執避免 fallback 重送。

workflow_run 使用原結論；執行中的 helper 使用 needs 的 producer 結果、在受支援的 step.if 擷取 cancelled 狀態，以及 Actions API metadata，避免通知 job 本身還在執行時誤認原執行健康。直接的資料健康通知不啟用這個 gate，以免把資料過期通知當成可被取消的工作。

`needs-json` 只採用 job result 與 `notify_summary`；其他 outputs 全部忽略。可選的 `github-token` 只用來 GET Actions jobs metadata，補足 matrix job／失敗 step 名稱，不下載執行 log。公開文字還會遮蔽傳入的四種 secret、其他 URL 及常見 token／key 表達式。

`title`、`summary` 應來自固定步驟名稱或資料日期監測器，不能傳任意命令輸出、私人資料或 API 回應。`run-url` 只接受沒有 query／fragment 的 `https://github.com/{owner}/{repo}/actions/runs/{id}`。

需要 Python 3.9+（標準函式庫及 IANA timezone）；正式呼叫目前使用 `ubuntu-latest`。完整工作清單、取消執行備援、資料去重與限制見 [系統故障通知操作說明](../../../docs/SYSTEM_FAILURE_NOTICES.md)。

離線測試不會呼叫真實 Discord／LINE：

```sh
python -m unittest discover -s tests -p test_failure_notifications.py -v
python -m unittest discover -s tests -p test_data_freshness.py -v
python -m unittest discover -s tests -p test_cancelled_notification_policy.py -v
actionlint
```
