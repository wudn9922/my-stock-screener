# 永豐台股持股唯讀同步（維護者手動啟用）

獨立 Cloud Run 服務，不共用 breakout_alert。只供一個 LINE 擁有者與一個 holdings UUID，正式／模擬永豐均未連線驗證；測試全部是假回應。本 PR 不部署、不執行 SQL。

## 資料與權限

Shioaji 1.7 官方 agent 的 ACCOUNTING / PREPARE / CONCEPTS 為介面依據。建立只勾「行情/資料、帳務、正式環境」、不勾「交易」的金鑰，限定適用的台股帳戶。程式登入時關閉交易訂閱並強制刷新 session，僅查詢 Common / Share 兩份持倉；finally 登出。不啟用憑證、不讀取商品合約、不提供交易介面。請勿把金鑰交給網頁。

Common 張數 ×1000 與 Share 股數以 Decimal 合併，price 為成本，加權均價四捨五入至 holdings 既有的 18 位小數，保存字串。Sell / ShortSelling / SBLShortSelling 略過並計數；MarginTrading 買進視為多頭。優先 TSE / OTC，無市場欄位則以 repo `data/*.TW.csv` / `*.TWO.csv` 代號清單判斷；無法辨識會整次失敗，保留既有持股，不猜市場。

沿用 `atlas_holdings_action` 的 list / replaceAccount 與 expected_revision；寫入透過薄包裝 `atlas_sinopac_replace`，在同一交易取得既有 storage-policy advisory lock 並重查 cloud 授權後，呼叫原 holdings RPC。claim 也先檢查授權，撤銷後不讀持倉、不登入，進行中的舊 snapshot 也不能於撤銷後寫入；衝突回報後由使用者重試，不覆蓋新 revision。數值與來源未變不寫入 holdings；空清單會清空該帳戶。持倉只存既有 holdings 表，新增表只有時間、結果代碼、融券計數與限流租約。原子 claim 供手動／排程／不同 instance 共用，60 秒內最多開始一次；執行中另有 180 秒租約，Cloud Run / gunicorn 最長 120 秒。崩潰後最長等 180 秒可重試。

永豐 API 帳戶不可從持股頁改成本或重新匯入；原 CSV 永豐帳戶仍可編輯。網頁確認管理帳戶前先停用編輯。帳戶 UUID 必須與設定一致；若沿用現有 CSV UUID，第一次同步會取代該帳戶持股，維護者應選擇新的 UUID。帳戶 broker=sinopac、kind=tw-broker、currency=TWD；持股 source=api，沒有新增 account source 欄位。

## 一次性部署順序

1. **SQL**：由維護者在既有 account_storage_permissions.sql 與 holdings SQL 完成後，手動檢閱並套用 `supabase/manual/atlas_sinopac_sync.sql`。表與 claim/finish RPC 僅 service_role 有權限，沒有 anon/authenticated 讀寫政策。程式與 workflow 不會套用 SQL。
2. **Google Cloud**：啟用 Cloud Run、Artifact Registry、Secret Manager、Cloud Scheduler；沿用 `.github/workflows/cloud-run.yml` 的 GitHub Workload Identity 設定。建立獨立 runtime SA，只授予所需 Secret 的 `roles/secretmanager.secretAccessor`；部署 SA 需要映像 push、Cloud Run 管理、runtime SA 的 actAs 等既有部署權限。
3. **Secret Manager**：建立四個 Secret：SJ API key、SJ secret key、Supabase service_role key、共享密鑰。共享密鑰須由密碼學亂數產生至少 32 bytes（例如在維護者本機以 `secrets.token_urlsafe(32)` 產生），只存 Secret Manager 與 Supabase secrets。不要貼進 PR、Actions variables、截圖或 log。指定 Secret **版本號**，更新版本後重新部署。SJ 金鑰只在 instance 啟動時從注入環境變數讀一次，不在每次請求呼叫 Secret Manager。
4. **Repository variables**（只放識別資訊／Secret 參照，不放 Secret 值）：

   | Variable | 設定 |
   | --- | --- |
   | GCP_WIF_PROVIDER / GCP_DEPLOY_SA / GCP_PROJECT_ID / GCP_REGION | 與既有 Cloud Run workflow 相同 |
   | SINOPAC_SYNC_SERVICE | 新獨立服務名稱 |
   | SINOPAC_SYNC_IMAGE | Artifact Registry 映像路徑，不含 tag |
   | SINOPAC_RUNTIME_SA | 上述 runtime SA email |
   | SINOPAC_OWNER_ID | 擁有者 LINE ID，`U` 加 32 個小寫十六進位字元 |
   | SINOPAC_ACCOUNT_ID | 預先產生的新 UUID，Cloud Run 與 Edge 必須一致 |
   | SINOPAC_SUPABASE_URL | 專案 URL |
   | SINOPAC_SCHEDULER_EMAIL | 專用 Scheduler SA email |
   | SINOPAC_SECRET_BINDINGS | 下列注入參照（Secret 名稱／版本可替換） |

   ```text
   SJ_API_KEY=sinopac-api-key:1,SJ_SEC_KEY=sinopac-sec-key:1,SINOPAC_SHARED_SECRET=sinopac-shared:1,SUPABASE_SERVICE_ROLE_KEY=sinopac-supabase:1
   ```

5. **部署**：維護者核准後手動執行新 workflow，或合併後由 main 的相關變更觸發。任何必要 variable 未設定會整個略過成功，不會導致既有部署失敗。服務為私有（無 allUsers invoker）、min instances 0、max instances 1、concurrency 1。workflow 會設定實際 URL 為 OIDC audience。不自動修改其他服務、IAM 或 Scheduler。
6. **手動入口 IAM 與 Supabase**：另建僅有此 Cloud Run 服務 `roles/run.invoker` 的 SA；將其 email / PKCS#8 PEM 私鑰存 Supabase secrets `SINOPAC_INVOKER_EMAIL` / `SINOPAC_INVOKER_PRIVATE_KEY`（保留 PEM 換行），不要給它資料庫或 Secret Manager 權限。這是 Supabase runtime 的服務呼叫身分，GitHub 部署仍使用 WIF。由維護者管理、輪替此 SA key。再設定 Edge secrets：`SINOPAC_SYNC_URL`（服務 https://…run.app origin）、`SINOPAC_OWNER_ID`、`SINOPAC_ACCOUNT_ID`、`SINOPAC_SHARED_SECRET`。透過 Dashboard 安全輸入 Secret，不放命令列參數或版本控制。既有 Supabase URL/service_role、LINE ID token 驗證與雲端儲存授權不變。部署更新後的 `user-settings`，保留專案既有 JWT / LINE 驗證設定。手動呼叫先驗證 LINE 與 cloud grant，再檢查擁有者；用 Google 簽署的限 audience ID token 通過 Cloud Run IAM，同時使用常數時間驗證共享密鑰標頭。
7. **Scheduler**：專用 Scheduler SA 只授予此服務 `roles/run.invoker`，確認 Cloud Scheduler service agent 的標準 IAM 角色與建立工作者的 actAs 權限。建立以下兩個工作（維護者自行填入變數；範例不含 Secret）：

   ```bash
   gcloud scheduler jobs create http sinopac-session --location="$REGION" \
     --schedule='*/10 9-13 * * 1-5' --time-zone='Asia/Taipei' \
     --uri="$SERVICE_URL/sync" --http-method=POST \
     --oidc-service-account-email="$SCHEDULER_SA" --oidc-token-audience="$SERVICE_URL" \
     --headers='Content-Type=application/json' --attempt-deadline=120s \
     --max-retry-attempts=0 --message-body="{\"ownerId\":\"$OWNER_ID\",\"accountId\":\"$ACCOUNT_ID\"}"
   gcloud scheduler jobs create http sinopac-final --location="$REGION" \
     --schedule='35 14 * * 1-5' --time-zone='Asia/Taipei' \
     --uri="$SERVICE_URL/sync" --http-method=POST \
     --oidc-service-account-email="$SCHEDULER_SA" --oidc-token-audience="$SERVICE_URL" \
     --headers='Content-Type=application/json' --attempt-deadline=120s \
     --max-retry-attempts=0 --message-body="{\"ownerId\":\"$OWNER_ID\",\"accountId\":\"$ACCOUNT_ID\"}"
   ```

   第一個工作為 09:00～13:50 每 10 分鐘，第二個為 14:35；服務會再驗證 Scheduler 的簽章、audience、email 與 owner/account body。Scheduler 不需保存共享密鑰；私有 IAM 及服務內 OIDC 驗證均保留。不要使用匿名測試請求或真實券商連線來驗證本 PR。

## 日曆、錯誤與運作

映像使用 repo 既有 `scripts/build_market_calendar.py` 與相同的 exchange-calendars 4.13.2 / XTAI 建立台股休市資料，涵蓋建置前 30 天到後 366 天。休市先跳過、不登入；日曆過期則停止同步並顯示「交易日曆尚未就緒」。維護者至少每年重建，遇交易所臨時休市或日曆修訂應更新既有日曆套件／資料來源並重建。沒有下載 Shioaji 商品檔。

應用程式只 log step / 固定錯誤代碼 / elapsedMs，SDK 呼叫期間連原生 stdout/stderr 一併抑制；不回傳原始 upstream exception。固定訊息涵蓋登入、權限、流量、逾時、休市、revision 衝突等。metadata 的 last_success_at 與最近成功的融券略過筆數於失敗／休市時不變；網頁錯誤也不改掉已確認持股。只有永豐同步的前端請求預算為 125 秒以容納 120 秒 Cloud Run 請求，其他設定操作仍為 15 秒，測試 timeout 不變。

## 離線／假回應驗證

```bash
python -m venv /tmp/sinopac-test
/tmp/sinopac-test/bin/pip install -r sinopac_sync/requirements.txt
/tmp/sinopac-test/bin/python -m unittest discover -s tests -p test_sinopac_sync.py -v
cd atlas
npx vitest run tests/sinopac-handler.test.ts tests/holdings*.test.ts tests/mexc*.test.*
npm run test:site -- sinopac.spec.ts holdings.spec.ts holdings-hide.spec.ts mexc.spec.ts
npm run lint
npm run build
```

所有 broker API、Google token exchange、Cloud Run 與 Supabase 回應皆 mock。新 CI 的 Python、日曆與 Docker 建置不提供券商金鑰；不會 import/login 真 SDK 進行測試。CI 不套用 SQL。390px 畫面為假資料、模擬裝置；不是實機或正式券商驗證。
