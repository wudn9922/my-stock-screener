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

全市場來源解析將未知量保留為 `None`。快取沿用 `main` 的單一缺量設計：`clean()` 在 `frame.attrs[UNKNOWN_VOLUME_DATES]` 記錄未知日期，數值欄仍補 0 供既有計算使用；合併、來源一致性比較、CSV／封存序列化前依 attrs 還原缺值。因此未知量在快取及封存都寫空白，明確的 0 仍寫 0。已存在且沒有缺值 lineage 的舊快取，不能事後猜測其 0 是否曾由缺值轉成；封存不假造這項歷史資訊。既有永久 CSV 沒有改寫。

封存不設 TTL、KEEP_BARS 或刪除清單。移出 universe、改名、下市或停牌的代號檔案全部保留；新代號建立新目錄，不移走舊代號。`manifest.json` 列出實際代號清單、各市場筆數、最早／最新日期、CSV bytes 與包含 README／狀態／稽核的總 bytes；總大小不包含 Git objects／pack。無資料變化不產生時間戳改寫或空提交。

此分支不是 `generated`，不在 `.github/generated-paths.json` 的 owners，不安裝 Pages relay、不放網站產物或執行程式。一般 generated 清理／squash 不會操作它。資料與分支歷史不可 squash、force-push 或刪除。

## 每日流程

1. 原 Full-Market Universe 工作先還原 Actions cache，再以 depth 1 讀取 `kline-archive`。
2. 快取檔／完整狀態缺少時，從永久封存取該代號最新 `KEEP_BARS=320` 根回種，再執行原有官方報價、Yahoo 增量補抓、均線與選股流程。已有完整快取不覆寫。
3. 下載前先暫存已接受的 cache baseline，避免首次啟用時滾動裁切／下市清理先丟掉尚未封存的列；這一步不推送。`Save daily-bar cache` 之後才組成 baseline＋本次資料的封存 artifact。只有狀態完整、CSV 與 state 最後日期一致、已結束交易時段的資料可進入 artifact；來源錯誤與跳過代號明列在輸出中。
4. 專用 publish job 序列化處理 artifact，重新 depth 1 checkout **當前**封存分支。每天只接在代號原最後日期之後；日常重疊差異留下衝突，不能覆蓋或補入舊日期。通常只改當年 CSV。
5. 推送前比對基準 commit 驗證：既有檔案／日期不得消失；歷史值改動必須有精確 old→new SHA-256 年度檔稽核鏈。push 被拒絕時只在 `kline-archive` 做 `git pull --rebase origin kline-archive`，最多三次普通 push。rebase 衝突會 abort 並失敗，不選 ours/theirs、不 reset 丟資料、不強制推送。下一次 producer 會從最新封存重做。

初始化尚未完成、分支無法讀取或封存驗證失敗時，新的全市場採集暫停，避免在沒有永久保存前先裁切舊快取；原 cache／報告的 always 保存與發佈仍繼續。已退出名單的 cache 也須先確認日期已進入永久封存才可清理。不要把「封存來源不可用」當成空白封存重新建立。

## 發布競爭、每日效能與停更復原

`generated_branch.py` 發布 main records 時，只有 `docs/report/kline-corrections.json`
可在同路徑競爭後使用既有 `kline_integrity.merge_logs` 的 union 規則；collector 與
publisher 共用同一個純標準庫函式，事件按 id 去重、保留雙方事件與既有隔離狀態。
每次普通 push 失敗都重新 fetch main，最多五次；不強推、不修改呼叫者的 checkout
或 index。任一其他 record 同路徑衝突仍立即失敗，且不發布 generated outputs。
Q4 工作使用 main 的 helper 副本時，也複製同版 union 模組。

每日 archive publisher 使用單一 `git cat-file --batch` 讀取必要的基準 blob；以
Git blob hash 略過未變年度的歷史差異比較。所有現存 CSV 仍逐列嚴格驗證一次，
包含沒有改變的年度；其統計直接用於 manifest，不再次解析全市場。新舊資料只常駐
單一股票／年度，批次 Git 回應暫存在磁碟；不一次載入全市場新舊列。

artifact 中完全相同且已處理的 daily baseline／final 操作只套用一次，包含已記錄且
保留舊列的衝突；任何中間修正都會清除該股票的去重 checkpoint。兩份 transport
仍各自驗證 digest，不能因略過重複操作而接收損壞資料。publisher 在資料／歷史稽核通過後
用已驗證的統計建立精確 manifest；一般 `verify` 仍唯讀且拒絕不符的 manifest。
檔案／日期刪除、未登記的歷史變更、舊 audit 被改寫仍會阻擋發布。

每日 archive job 的上限由 20 分鐘增至 40 分鐘，提供 checkout／Git push 的餘裕；
`python -u` 會即時列出 apply、批次 Git、列與歷史驗證、stage、commit、push 的秒數。
apply 開始時立即輸出訊息，分開列出狀態載入、每份 snapshot 的驗證／合併、稽核、
衝突寫入與選用的 manifest 重建耗時；每 250 檔與最後一檔輸出進度、筆數及略過數。
逐檔預先驗證 transport，不同時在記憶體保留全市場的 rolling rows。
CI 另建 7,000 檔 × 3 年、21,000 個 packed 年度檔／3,654,000 列的合成封存，對每檔
保留的 320 根製造微小來源差異（共 2,240,000 筆）再追加一天，要求 apply 與
3,661,000 列驗證各少於 300 秒、衝突摘要小於 12 MB，也完成對本機 bare origin 的普通
commit／push；不讀寫正式資料或 ref。

封存停在 2026-10-23 時，後續正常 producer 會從還原的 320 根 rolling cache 暫存
所有未封存且已完成的交易日，並依原順序追加；沒有只保留今日／最後一根的篩選。
TW／US 的多日復原與重跑去重有離線測試。合併後下一次成功的正常工作會執行這條
復原路徑；這個修正 PR 本身不觸發正式採集、封存 push 或網站部署。

```sh
KLINE_ARCHIVE_PERFORMANCE=1 python -u -m unittest discover -s tests \
  -p test_kline_archive_performance.py -v
```

## 拆股／價格基準修正

沿用 `universe_cache.compare_overlap`／`merge_full` 的偵測：舊／新重疊收盤差超過既有 0.5% 容忍度，且本次完整 Yahoo 重抓成功時，保存包含比對日、前後收盤、來源時間與 payload hash 的 cache receipt。

若永久封存比一般 2y 下載更早，builder 在**原請求預算、間隔與 429 退避內**向 Yahoo 取得 max 歷史。沒有足夠時間或來源未涵蓋舊年度時保留原快取／封存、延後重試，不推算縮放係數。未完成的修正 receipt 隨 cache 保留，下一次會繼續驗證；封存已套用後才移除這份可丟棄的 cache receipt。

publish 必須確認修正資料涵蓋原有較早交易日。只修正該股票有變動的年度，所有原日期保留；來源縮短或遺漏舊交易日會阻擋該股票修正並留下衝突，其餘股票仍可封存。

調整日誌採**一次調整／股票／有變動的年度檔一筆**，不再記錄每個歷史列的副本。`docs/report/kline-corrections.json` 保留既有 `kline_integrity` schema／union 機制；事件的 `symbol`、`year`、`new.path`、`old.sha256`、`new.sha256`、`new.rowsChanged`、`comparison`（`date`、`oldClose`、`newClose`、`worst`）、`adjustmentId`、`sourceUrl`、`fetchedAt` 都可追查，`reason` 為 `kline-archive/adjustment-year:<id>`。確切列差異可比較該分支前後 commit 的年度 CSV。例如三年共數百列拆股，只新增三筆年度事件；無變動年度與重跑不新增事件。

驗證會依雜湊連線追蹤舊檔到新檔，不依賴同秒事件的日誌排序；鏈缺一段、只改一列卻沒有新 hash、刪除檔案或日期都會失敗。回補的年度 hash 只能授權插入缺漏日期，不能單獨授權覆寫既有值。同一 artifact 的 baseline 追加、價格修正及之後日 K 追加會合成涵蓋該交易最終檔案的年度紀錄；只整理尚未提交的事件，已提交的歷史日誌不重寫。稽核暫存使用臨時檔，不同時持有全市場歷史 K 線。

只有精簡的 `adjustment-year` 事件會再合併回 main 同名永久日誌；舊的逐列封存事件及回補事件不複製到 main。一次調整新增到 main 的事件數不超過有變動的年度檔數；既有 2.8 MB 主日誌不改寫或刪除。這一步只發佈該 journal，不操作 `data/*.csv`、其他 records 或 generated outputs。main 發生並行 journal 更新時，重新讀取最新 main 並 union，不覆蓋別人的事件。若 journal 發佈失敗，封存分支仍保有所有事件，下次可重播；main checkout 不做 pull/rebase。

## 全市場臨時休市（颱風假等）

交易所日曆只知道預定假日，不知道臨時休市（例如 2026-10-23 颱風休市）。那天整個市場每檔都會是「開高低收相同、成交量 0、等於前一日收盤」的空白 K 棒。

- **每日流程**：`stage` 若發現某市場某日至少 50 檔、且 95% 以上都是這種空白 K 棒，就判定為全市場休市，不寫入封存（`operations.json` 的 `closedSessions` 會列出）。個別股票在交易日沒有成交的平盤 K 棒仍照常保存。
- **已寫入的清除**：`python scripts/kline_archive.py remove-closed-session --archive-dir <封存> --market tw --date YYYY-MM-DD`。只有當天該市場每一檔都是空白 K 棒時才會執行，否則整批拒絕、不改任何檔案。每個檔案記一筆 `status: removed`、`reason: kline-archive/closed-session` 的稽核（新舊 SHA-256）。
- **驗證**：`verify` 仍拒絕任何刪除日期，唯一例外是有對應稽核、且被刪的那列確實是空白 K 棒。

## 一次性歷史回補與續跑

`Permanent K-line Backfill` 使用同一份上市／上櫃／美股普通股與 ADR universe 來源，與既有下載工作共用 `universe-full` 鎖，避免同時增加 Yahoo 請求。fetch job 只有 `contents: read`；只把資料 artifact 交給唯一序列化的 publish job，後者才有 write。

預設 `--years 2 --budget 600`。`years` 可設 1–100，budget 為 30–2400 秒；下載使用精確 period1／period2 的日資料、原 `REQUEST_DELAY=0.3`、30／60／120 秒 429 退避與錯誤停止規則。預算為軟截止，已發出的單次來源請求／transport fallback 可能跨過截止時間；不開始沒有足夠請求時間的下一檔。

每檔完成後更新 artifact checkpoint；發佈後進度永久保存在 `state/backfill.json`，不靠 Actions cache 存活。同一組 years 的首次起點會固定，跨日續跑不向後滑動；下次略過已完成且涵蓋所選起點的股票，不為已完成股票重建 Yahoo session。增加 years 會補更早期間。新上市、來源起點較晚、休市及來源缺漏都如實保留第一／最後來源日期，不補值、不把部分資料稱為完整兩年。失敗股票保留錯誤與重試日期；日後移出名單也不刪除其進度或 CSV。

回補只加入不存在的日期。既有日期有任何價格／成交量差異就保留舊列並寫入 `state/conflicts.json`，同一組差異去重。補入舊日期以每年度檔案的 before/after SHA-256 與新增筆數作稽核，不為每個補入日期膨脹一份逐列修正日誌；既有歷史列仍不得改動。股價基準真的改變時只能走上面的 cache 調整路徑。

### 精簡衝突狀態（version 2）

每次 apply 只讀取衝突檔一次，在記憶體彙整所有股票後原子寫入一次；中途其他操作
失敗時也保存已觀察到的差異。單檔 `put_symbol` 呼叫同樣批次寫入，不逐列重寫 JSON。
每組 `symbol + reason` 只保留一份摘要：`count` 是累計不同衝突日期數，
`firstDate/lastDate` 是日期範圍，`firstObservedAt` 是首次觀察時間，`samples` 最多三份
依內容固定排序的舊／新值範例。`dateBits` 以每年度至多 366 bits 的十六進位日期集合
精確去重，因此重跑或同日來源改版不會累加 count，也不會隨拒收的列／版本數增加
逐列 JSON 紀錄。空白量、零量與價格仍依原規則比較，沒有新增容忍度。

既有 version 1 狀態會在載入時彙整為相同摘要，保留日期集合、最早觀察時間及至多
三份範例；舊逐列觀察仍可在 Git 歷史查到。這是衝突診斷資訊的精簡，不是修改既有
K 線或稽核日誌。重播相同資料不改寫狀態時間戳或新增摘要。

實際 artifact 的量測表、可重現步驟與**尚未實作的來源優先序建議**見
[2026-10-26 archive conflict investigation](report/kline-daily-40317163328.md)。

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
python -m unittest discover -s tests -p '*report_metrics*.py' -v
KLINE_CORRECTIONS_LOG=/tmp/test-kline-corrections.json python -m unittest discover -s tests -v
git clone --depth=2 --single-branch --branch kline-archive \
  https://github.com/wudn9922/my-stock-screener.git /tmp/kline-check
python scripts/kline_archive.py verify --archive-dir /tmp/kline-check --base HEAD^
```

首個 orphan commit 沒有 parent，省略 `--base`。驗證包含日期排序／唯一性、正數 OHLC 與上下界、整數或空白成交量、年度、manifest 實際 bytes、不可刪除日期／檔案、不可重寫既有 audit，以及未登記的歷史改動。`Permanent K-line archive verification` 在相關 PR 跑離線測試；遠端分支存在後也檢查所有 CSV 與最近一次 commit。未初始化時 job summary 會明說 live size 0／尚未驗證 live archive，不冒充全市場驗收。

驗證只常駐全市場的檔案 metadata；K 線逐檔驗證，歷史差異只讀取有變動股票／年度的 baseline，不把全市場新舊 OHLC 同時載入記憶體。日常追加只讀取涉及年度及最後年度；冷啟動回種從最新年度向前取滿 320 根即停止。

2026-10-26 修正後的可重現合成測試使用 7,000 檔、21,000 個 packed 年度 CSV；逐檔追加一天後共 3,661,000 列、205,500,841 bytes。本機驗證為 48.755 秒，包含 apply、驗證、stage、commit 與本機 bare-origin push 的每日發布為 72.397 秒；基準 7,000 個 blob 僅啟動一次 Git batch。此為合成價格，不是正式行情；GitHub runner 另受 CI 的 300 秒驗證門檻約束。唯讀檢查正式 `4c934296a` 封存的 21,133 檔、3,480,830 列為 36.722 秒；沒有改動正式 main、generated 或 archive ref，也沒有宣稱已補齊正式資料。

大小隨實際股票與歷史變動：例如 7,000 檔 × 252 日 × 每列約 70 bytes，新增 CSV 原始內容約 124 MB／年（約 0.5 MB／交易日）；Git 的 tree/blob/pack 與真實修正另計，不能把 CSV byte 增長冒充 repository 實際儲存量。每次驗證 job 都回報當時真正的檔案總大小。
