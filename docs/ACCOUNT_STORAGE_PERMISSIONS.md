# Atlas 帳號隔離與儲存授權

本次程式以 PR 交付，不合併、不操作正式資料庫。正式生效需要部署下述 SQL 與兩個 Edge Functions，再發布前端；只發布前端會安全降為暫存，管理功能尚不能使用。

## 使用者的保存方式

| 模式 | 分組、均線、清單、畫線與偏好 | Supabase 個人資料 | GitHub 個人資料 |
| --- | --- | --- | --- |
| 未登入／不保存 `none` | 僅本頁記憶體；重新整理清除 | 不讀寫 | 不寫入 |
| 本人裝置 `device` | 以 LINE 帳號分開保存在該瀏覽器 | 不讀寫 | 不寫入 |
| 雲端 `cloud` | 同樣使用帳號獨立裝置空間；啟用既有同步 | 只讀寫本人資料 | 不寫入其他使用者的資料 |

沒有授權紀錄的帳號預設 `none`，開頁不會自動登記帳號、分組或清單。現有管理員預設 `cloud`，可將自己改為 `device`；這樣個人研究資料也只放在自己的手機／電腦。公開的行情、財報、K 線和原有管理員日報維持既有公開發布流程；他人的分組／均線不能改寫該公開報告。

裝置模式不提供跨裝置同步，也不啟動雲端清單追蹤、服務端突破監控或雲端畫線。功能仍能在當次頁面使用。暫存模式停用個人資料匯出；已獲裝置或雲端保存授權者可使用既有匯入／匯出。

「個人操作不保存」指應用程式不將操作寫入 localStorage、sessionStorage、IndexedDB、CacheStorage、Supabase 或 GitHub。瀏覽器自身的網址紀錄、HTTP 快取、LINE 的登入程序及服務提供者的基礎連線紀錄不是應用程式能全面禁止的範圍。訪客不載入 LINE SDK；公開行情請求仍會正常送往行情服務，不包含畫線、分組或私人參數。

## 管理方式

1. 管理員在 LINE 開啟 Atlas →「參數控制台與自選清單」→「管理員」。此入口必須通過伺服器的管理員驗證才顯示。
2. 使用者在同一控制台的儲存說明查看自己的 LINE 帳號 ID，將完整 `U` 開頭 ID 提供給管理員。系統不建立訪客名冊，也不自動蒐集帳號。
3. 管理員填入 ID、辨識名稱，選擇「不保存」「只保存在本人裝置」或「允許 Supabase 雲端保存」，按「設定此帳號權限」。可從名單再次編輯。
4. 使用者重新整理或回到前景會重新取得授權；開著的頁面最多每 60 秒重新檢查一次。權限來源失敗或登入過期時，停止保存並顯示暫存說明。

即使管理員選擇裝置模式，**授權名單本身**仍保存在 Supabase 的小型權限表（最多 2,000 筆、名稱最多 80 字）。這是伺服器拒絕未授權寫入所需的管理資料，不包含那些人的研究設定。

撤銷雲端權限後，伺服器立即拒絕新的個人資料讀寫，資料庫也在每次異動時重查權限；先前交易會與撤銷動作依帳號鎖序列化。已開啟的裝置頁面在下一次在線授權檢查後切回暫存。管理員無法遠端抹除已存到別人手機的資料，也不能限制其自行使用開發者工具、截圖或瀏覽器功能。離線重新開啟時不沿用快取授權，未驗證前不讀取私人裝置資料。

## 隔離與既有資料

新版命名範例：

```text
localStorage: atlas-account-screener:<LINE_ID>:<device|cloud>:<原設定鍵>
IndexedDB:   atlas-terminal-screener:account:<LINE_ID>:<device|cloud>
```

行情快取與財報快取也加入同樣的帳號／模式命名。換帳號、撤銷或改變授權版本會換掉整個工作區，取消舊請求，拒絕舊佇列寫入。A 帳號的延遲回應不能顯示到 B 帳號。不同模式各自保存，避免切成 cloud 時意外自動上傳 device 資料。

舊版共用的 `atlas-terminal-screener` 和未分帳號 localStorage **保留原狀，但不自動讀取／認領／上傳**，因為無法可靠判斷真正擁有人。升級前如需保留它，先用舊版既有匯出功能備份，之後由本人在獲授權的新版帳號明確匯入。沒有事前備份時，可在隔離環境以舊版程式讀取原瀏覽器資料後匯出；不要把舊資料庫直接重新命名成某個帳號。原 Supabase 資料保留；原擁有人再次獲准 cloud 後才能讀回。

切換模式、撤銷或授權失敗都不刪除既有資料。清除瀏覽器資料或換手機會失去 device 資料。此 PR 不進行任何正式資料刪除、批次搬移或自動資料認領。

## 正式部署（合併前保留人工步驟）

需要 Supabase 專案管理權限；本次環境沒有正式部署憑證，以下未執行。

1. 備份資料庫並確認 `groups`、`stocks`、`chart_drawings`、`atlas_watchlists`、`breakout_alert_state` 的帳號欄位為 `line_user_id`。如有不同，先調整 migration；檢查失敗會整筆交易回滾。
2. 核對現有管理員為 `Ua0037dbd5037b64fc76804c60520ad76`。若正式 `ADMIN_LINE_USER_ID` 有覆寫，必須同步修改 SQL 的 `atlas_storage_owner_id()`，Edge Functions、Actions `vars.ADMIN_LINE_USER_ID` 和 Cloud Run 的環境變數也須一致。不能只改瀏覽器。
3. 在 Supabase SQL Editor 執行 [account_storage_permissions.sql](../supabase/manual/account_storage_permissions.sql)。它新增權限表／觸發器，關閉舊的匿名直接讀寫路徑，不刪除個人資料。套用後，舊版非管理員會被拒絕寫入；因此安排接續部署。
4. 使用有授權的 Supabase CLI 執行：

   ```sh
   supabase functions deploy user-settings --project-ref bxhqpfeberqbtxymghyt --no-verify-jwt
   supabase functions deploy chart-drawings --project-ref bxhqpfeberqbtxymghyt --no-verify-jwt
   ```

   兩個函式都要連同 `_shared/storageAccess.ts` 部署；它們自行向 LINE 驗證 ID token，並非信任客戶端傳來的帳號或 admin 欄位。沿用既有 LINE Channel、Supabase server secrets；不要將 service-role key 放進前端或 Git。
5. 保留既有 `ATLAS_FIXED_PARAMETERS_ENABLED` 與自選清單部署條件，將新的 policy 檢查一併部署到既有 Cloud Run／排程消費端。確認 `Dockerfile.discord-query` 包含新增的 policy helper。尚未更新的服務也會被 SQL 寫入觸發器攔截，但必須更新才能正確拒絕私人讀取。
6. 經 PR 審核後，才按原有流程合併、執行 Atlas 建置與 Pages 發布。本次不代為合併或發布。

正式驗收：

- 用未登入的一般瀏覽器建立分組、設均線與畫線；重新整理全數回復，瀏覽器應用儲存沒有新增紀錄，Network 沒有個人資料 API 寫入。
- 管理員將一個測試 LINE 帳號設為 device；同瀏覽器切到另一個帳號，確認分組、均線、清單、畫線皆不能看到前一人資料；私人 Supabase 表列數不增加。
- 將測試帳號設為 cloud，確認只能讀寫本人列；用另一人 token／偽造 `line_user_id` 或 `isAdmin` 呼叫私人 API 應被拒絕或只能作用於 token 本人。
- 將 cloud 改為 none，確認新請求回 403；舊資料未刪。匿名與 authenticated 角色不得直接讀寫私人表；SQL 最後提供權限檢查查詢。
- 以 device 模式的管理員測試仍可修改授權；一般使用者不能開啟管理員介面或呼叫管理 API。
- 確認原有日報、公開行情、財報、世界 K 線及手機圖表正常，GitHub 產物沒有其他帳號的分組／參數／清單。

回滾：停止新版發布，先恢復上一版前端／函式並核對舊版安全影響，再按需要移除新觸發器；保留所有個人資料與權限表備份。不要直接恢復匿名私人表權限，也不要以刪表作為回滾。

## 本次驗證

離線測試涵蓋實際 Edge handler、資料庫觸發器、帳號換頁、延遲回應、撤銷、過期 token、裝置命名、訪客零寫入與排程公開資料隔離。瀏覽器測試包含桌面 Chromium、iPhone 尺寸 Chromium 和 iPhone WebKit；使用測試身份與合成行情，不能代表真實 LINE／Safari 裝置及正式 Supabase 已完成驗收。最終執行數字見 PR 與 `atlas/REVIEW_PACKAGE.md`。
