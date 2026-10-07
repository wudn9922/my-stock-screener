# my-stock-screener

每日股票掃描與報告：產生 `docs/index.html`（GitHub Pages）、推播 LINE，並同步更新 `data/` 的 K 線 CSV。

## 讓每日報告準時（台灣時間 17:00）

GitHub Actions 的 `schedule` 不保證準時，實測常延遲 5～8 小時。
`schedule.yml` 內的 cron 只是備援；要準時，請用外部排程在 **台灣時間 17:00（UTC 09:00），週日～週五** 呼叫 `workflow_dispatch`。

兩種觸發同一天只會有一個真的執行（workflow 開頭有檢查，今天已經跑過或正在跑就略過）。
手動在 Actions 頁面重跑時，勾選 `force` 可略過這個檢查。

### 1. 建立 GitHub token

GitHub → Settings → Developer settings → Fine-grained personal access tokens：

- Repository access：只選 `my-stock-screener`
- Repository permissions：**Actions → Read and write**（其他都不要）
- 到期日建議設 1 年，並記得到期前更換

### 2. 設定外部排程（二選一）

**Cloud Scheduler（已使用 GCP 時）**

```bash
gcloud scheduler jobs create http daily-stock-screener \
  --location=asia-east1 \
  --schedule="0 17 * * 0-5" \
  --time-zone="Asia/Taipei" \
  --uri="https://api.github.com/repos/wudn9922/my-stock-screener/actions/workflows/schedule.yml/dispatches" \
  --http-method=POST \
  --headers="Authorization=Bearer <GITHUB_TOKEN>,Accept=application/vnd.github+json,X-GitHub-Api-Version=2022-11-28,Content-Type=application/json" \
  --message-body='{"ref":"main"}'
```

**cron-job.org（免費、不用 GCP）**

- URL：`https://api.github.com/repos/wudn9922/my-stock-screener/actions/workflows/schedule.yml/dispatches`
- 方法：POST，時區 Asia/Taipei，週日～週五 17:00
- Headers：`Authorization: Bearer <GITHUB_TOKEN>`、`Accept: application/vnd.github+json`
- Body：`{"ref":"main"}`
- 成功時 GitHub 回傳 HTTP 204

### 3. 驗證

排程跑過後，到 Actions 頁面確認 `Daily Stock Screener` 的觸發事件是 `workflow_dispatch`，且開始時間約為 17:00。

## 環境變數

見 `.env.example`。
