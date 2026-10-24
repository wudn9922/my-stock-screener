# 全市場日 K 永久封存

日 K 的永久副本在獨立 orphan 分支 **`kline-archive`**。`main` 的 `data/*.csv`、`data/world/*.csv`、財報紀錄及選股規則不變。Actions 的 `.cache/universe-full` 仍是 320 根的運算快取；它可以被驅逐，永久封存不依賴其保存期限。

## 保存格式與邊界

```text
kline-archive (orphan; no main parent)
  README.md
  manifest.json
  tw/2330.TW/2025.csv
  tw/2330.TW/2026.csv
  us/AAPL/2025.csv
  us/AAPL/2026.csv
  docs/report/kline-corrections.json
  state/backfill.json
  state/conflicts.json
```

每年一檔，欄位固定為 `Date,Open,High,Low,Close,Volume`。Yahoo quote OHLC 與既有 universe cache 同口徑：**分割調整後、未含股利還原**；不讀取 adjusted-close 來再還原價格。價格固定四位小數，成交量為整數股數；來源未知的成交量為空白，確定的零成交量為 `0`。無法以四位小數表示為正數的極低價格會明確拒收，不能寫成 `0.0000`。

這次修正全市場來源解析與快取的缺量表示，避免把新的 null 成交量轉為 0。已存在的舊快取數字沒有缺值來源證據，不能事後猜測其 0 是否曾由缺值轉成；封存不假造這項歷史資訊。既有永久 CSV 沒有改寫。

封存不設 TTL、KEEP_BARS 或刪除清單。移出 universe、改名、下市或停牌的代號檔案全部保留；新代號建立新目錄，不移走舊代號。`manifest.json` 列出實際代號清單、各市場筆數、最早／最新日期、CSV bytes 與包含 README／狀態／稽核的總 bytes；總大小不包含 Git objects／pack。無資料變化不產生時間戳改寫或空提交。

此分支不是 `generated`，不在 `.github/generated-paths.json` 的 owners，不安裝 Pages relay、不放網站產物或執行程式。一般 generated 清理／squash 不會操作它。資料與分支歷史不可 squash、force-push 或刪除。

## 每日流程

1. 原 Full-Market Universe 工作先還原 Actions cache，再以 depth 1 讀取 `kline-archive`。
2. 快取檔／完整狀態缺少時，從永久封存取該代號最新 `KEEP_BARS=320` 根回種，再執行原有官方報價、Yahoo 增量補抓、均線與選股流程。已有完整快取不覆寫。
3. 下載前先暫存已接受的 cache baseline，避免首次啟用時滾動裁切／下市清理先丟掉尚未封存的列；這一步不推送。`Save daily-bar cache` 之後才組成 baseline＋本次資料的封存 artifact。只有狀態完整、CSV 與 state 最後日期一致、已結束交易時段的資料可進入 artifact；來源錯誤與跳過代號明列在輸出中。
4. 專用 publish job 序列化處理 artifact，重新 depth 1 checkout **當前**封存分支。每天只接在代號原最後日期之後；日常重疊差異留下衝突，不能覆蓋或補入舊日期。通常只改當年 CSV。
5. 推送前比對基準 commit 驗證：既有檔案／日期不得消失；歷史值改動必須有精確 old/new 修正事件。push 被拒絕時只在 `kline-archive` 做 `git pull --rebase origin kline-archive`，最多三次普通 push。rebase 衝突會 abort 並失敗，不選 ours/theirs、不 reset 丟資料、不強制推送。下一次 producer 會從最新封存重做。

初始化尚未完成、分支無法讀取或封存驗證失敗時，新的全市場採集暫停，避免在沒有永久保存前先裁切舊快取；原 cache／報告的 always 保存與發佈仍繼續。已退出名單的 cache 也須先確認日期已進入永久封存才可清理。不要把「封存來源不可用」當成空白封存重新建立。

## 拆股／價格基準修正

沿用 `universe_cache.compare_overlap`／`merge_full` 的偵測：舊／新重疊收盤差超過既有 0.5% 容忍度，且本次完整 Yahoo 重抓成功時，保存包含比對日、前後收盤、來源時間與 payload hash 的 cache receipt。

若永久封存比一般 2y 下載更早，builder 在**原請求預算、間隔與 429 退避內**向 Yahoo 取得 max 歷史。沒有足夠時間或來源未涵蓋舊年度時保留原快取／封存、延後重試，不推算縮放係數。未完成的修正 receipt 隨 cache 保留，下一次會繼續驗證；封存已套用後才移除這份可丟棄的 cache receipt。

publish 必須確認修正資料涵蓋原有較早交易日。只修正該股票有變動的年度，所有原日期保留；來源縮短或遺漏舊交易日會阻擋該股票修正並留下衝突，其餘股票仍可封存。每筆改動的舊值、新值、Yahoo URL、擷取時間、receipt ID 都寫入封存分支的 `docs/report/kline-corrections.json`，使用既有 `kline_integrity` 日誌 schema／union 機制。

真正的 adjustment 事件再合併回 main 同名永久日誌；這一步只發佈該 journal，不操作 `data/*.csv`、其他 records 或 generated outputs。main 發生並行 journal 更新時，重新讀取最新 main 並 union，不覆蓋別人的事件。若 journal 發佈失敗，封存分支仍保有所有事件，下次可重播；main checkout 不做 pull/rebase。

## 一次性歷史回補與續跑

`Permanent K-line Backfill` 使用同一份上市／上櫃／美股普通股與 ADR universe 來源，與既有下載工作共用 `universe-full` 鎖，避免同時增加 Yahoo 請求。fetch job 只有 `contents: read`；只把資料 artifact 交給唯一序列化的 publish job，後者才有 write。

預設 `--years 2 --budget 600`。`years` 可設 1–100，budget 為 30–2400 秒；下載使用精確 period1／period2 的日資料、原 `REQUEST_DELAY=0.3`、30／60／120 秒 429 退避與錯誤停止規則。預算為軟截止，已發出的單次來源請求／transport fallback 可能跨過截止時間；不開始沒有足夠請求時間的下一檔。

每檔完成後更新 artifact checkpoint；發佈後進度永久保存在 `state/backfill.json`，不靠 Actions cache 存活。同一組 years 的首次起點會固定，跨日續跑不向後滑動；下次略過已完成且涵蓋所選起點的股票，不為已完成股票重建 Yahoo session。增加 years 會補更早期間。新上市、來源起點較晚、休市及來源缺漏都如實保留第一／最後來源日期，不補值、不把部分資料稱為完整兩年。失敗股票保留錯誤與重試日期；日後移出名單也不刪除其進度或 CSV。

回補只加入不存在的日期。既有日期有任何價格／成交量差異就保留舊列並寫入 `state/conflicts.json`，同一組差異去重。補入舊日期以每年度檔案的 before/after SHA-256 與新增筆數作稽核，不為每個補入日期膨脹一份逐列修正日誌；既有歷史列仍不得改動。股價基準真的改變時只能走上面的 cache 調整路徑。

Actions artifacts 只負責短期交接（7 天），不是真正的永久副本。一次 fetch 在發佈前失敗最多重抓該批資料；遠端已保存的日期與完成進度都不會消失。

## Owner 手動操作（此 PR 不合併／不部署）

不需要新 API key、付費資料、Supabase migration 或新 secret；沿用有 `contents: write` 的工作 token。Owner 合併並啟用程式後：

1. 執行一次 create-only 初始化；已存在分支時拒絕覆蓋：

   ```sh
   gh workflow run kline-archive-backfill.yml --ref main -f operation=initialize
   ```

2. 在 GitHub branch rule／ruleset 對 `kline-archive` **禁止 force push 與刪除**，並避免任何 squash／清理自動化包含此分支。允許受信任的 Actions 普通 fast-forward push；不要對此機器資料分支要求每次開 PR 才能更新。Repository Actions 若禁用了 write token，需要 owner 開放這個既有能力。
3. 執行回補，重複 dispatch 直到進度涵蓋目標股票；可分 TW／US 跑，不會互相刪除資料：

   ```sh
   gh workflow run kline-archive-backfill.yml --ref main \
     -f operation=backfill -f years=2 -f budget=600 -f markets=TW,US
   ```

4. 檢查 branch manifest、job summary 總 bytes、`state/backfill.json` 與衝突清單。實際覆蓋以這些檔案為準；本 PR 沒有建立遠端封存分支或聲稱已回補全市場。
5. 正常每日 workflow 會接續封存。可人工刪除**測試用** Actions cache 後重跑 Full-Market Universe，核對 bootstrap 的 seeded 數與 Yahoo full-request 數下降；不可刪除永久封存來測試。

GitHub 平台／有繞過權限的管理員仍有能力移除 repository；這份程式不替代分支保護或 owner 額外備份政策。

## 可重現驗證

小規模實際 Yahoo 驗證（2026-10-24，僅 `/tmp`，未推送資料分支）：period1／period2 回補 AAPL 與 2330.TW，共 986 根、6 個年度 CSV；含 metadata 的驗證目錄 59,019 bytes。AAPL 有 501 根，2024-10-24 至 2026-10-23，最後 Close `347.7300`、Volume `39496500`；2330.TW 有 485 根、同起訖日期，最後 Close `2385.0000`、來源明確回傳 Volume `0`。這是兩檔來源／格式驗證，不是全市場回補完成率；沒有把任何行情範例寫入 main 的資料 CSV。

```sh
python -m unittest discover -s tests -p 'test_kline_archive*.py' -v
python -m unittest discover -s tests -p test_universe_full.py -v
git clone --depth=2 --single-branch --branch kline-archive \
  https://github.com/wudn9922/my-stock-screener.git /tmp/kline-check
python scripts/kline_archive.py verify --archive-dir /tmp/kline-check --base HEAD^
```

首個 orphan commit 沒有 parent，省略 `--base`。驗證包含日期排序／唯一性、正數 OHLC 與上下界、整數或空白成交量、年度、manifest 實際 bytes、不可刪除日期／檔案、不可重寫既有 audit，以及未登記的歷史改動。`Permanent K-line archive verification` 在相關 PR 跑離線測試；遠端分支存在後也檢查所有 CSV 與最近一次 commit。未初始化時 job summary 會明說 live size 0／尚未驗證 live archive，不冒充全市場驗收。

大小隨實際股票與歷史變動：例如 7,000 檔 × 252 日 × 每列約 70 bytes，新增 CSV 原始內容約 124 MB／年（約 0.5 MB／交易日）；Git 的 tree/blob/pack 與真實修正另計，不能把 CSV byte 增長冒充 repository 實際儲存量。每次驗證 job 都回報當時真正的檔案總大小。
