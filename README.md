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

## Atlas 研究終端

Atlas 是內嵌在每日報告裡的研究工具（Vite/React，原始碼在 `atlas/`），提供畫圖、技術指標、財報與回測。

- 網址：<https://wudn9922.github.io/my-stock-screener/atlas/>
- 指定個股與週期：`atlas/?symbol=<代號>&tf=<週期>`，例如 `atlas/?symbol=2330.TW&tf=1D`、`atlas/?symbol=%5EGSPC&tf=1W`
  - 代號使用 Yahoo 格式：`2330.TW`、`6488.TWO`、`NVDA`、`BRK-B`、`^TWII`、`^GSPC`
  - 週期：`5m`、`15m`、`30m`、`1H`、`1D`、`1W`、`1M`（每日快照目前只產生 `1D`、`1W`、`1M`）
- 報告頁入口：
  - 每張圖表右上角的「🔬 Atlas」：以全螢幕浮層開啟該檔（日 K → `1D`、週 K → `1W`），全市場分頁也有
  - 頁面上方的「🔬 Atlas 研究終端」區塊：不指定代號直接開啟
  - 浮層右上角「在新分頁開啟」：LINE App 內會改用外部瀏覽器開啟

### 更新流程

1. `Daily Stock Screener`（`schedule.yml`）成功產生報告後，自動觸發 `Atlas Research Terminal`（`.github/workflows/atlas.yml`）。
2. Atlas workflow：`npm ci` → `npm test` → 依 Supabase 群組產生股票清單 → 抓取 Yahoo 行情快照（1D / 1W / 1M）→ 抓取 SEC 財報 → `npm run build:pages` → 以建置結果取代 `docs/atlas/` 並提交。
3. 行情或財報抓取失敗時，沿用上次部署在 `docs/atlas/` 的資料；財報失敗不會阻擋部署，行情失敗會在部署後把 workflow 標成失敗以便察覺。
4. 兩個 workflow 使用不同的 concurrency 群組（避免 Atlas 排隊時擠掉每日報告），同時推送時會自動 `pull --rebase` 後重試。

手動更新：GitHub → Actions → `Atlas Research Terminal` → **Run workflow**（分支選 `main`）。
修改 `atlas/` 底下的檔案並推到 `main` 也會自動重新建置。

需要的設定（與每日報告共用）：Secrets `SUPABASE_URL`、`SUPABASE_ANON_KEY`、`SUPABASE_USER_ID`；
可選 Variable `SEC_USER_AGENT`（SEC 要求的聯絡資訊，未設定時使用預設值）。

### 資料限制

- 行情是 Yahoo 的**延遲快照**，每天在報告產生後更新一次，不是即時報價。
- 財報只有美股（SEC EDGAR）；**台股財報目前不包含**。
- Atlas 的畫圖、設定等資料存在瀏覽器的 IndexedDB，**只保存在當下這個瀏覽器**：
  LINE 內建瀏覽器、手機 Safari / Chrome、電腦之間不會同步，清除瀏覽器資料也會一併刪除。
  （報告頁原本的水平線段仍照舊同步到 Supabase，不受影響。）
