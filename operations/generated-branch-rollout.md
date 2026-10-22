# 再生輸出分支：PR 1、切換、PR 2 與回滾

這是第一階段：只加入基礎設施及單一開關，不刪除目前 `docs/`、`data/` 的任何檔案，不改寫 main，不執行切換、不建立遠端 generated、不合併 PR。`GENERATED_BRANCH_MODE` 未設定或不等於 `1` 時，原有產生／提交流程維持原樣。唯一無條件改變是停用 `trim-history.yml`：即使手動執行也只有 read 權限並立即失敗。

## 盤點與容量

盤點基準為 main `485761e717879f58dff669e40d8b05e5e5029a9e`（2026-10-22）。[完整逐檔清單](generated-path-inventory.tsv) 涵蓋 `docs/`、`data/` 全部 **9,635 個受追蹤檔案**，每列含路徑、`regenerated`／`record`、bytes 與理由；目錄按其中各檔案分類，不將混合用途目錄整批移走。[機器可讀基準](generated-baseline.json) 記錄 SHA、數量、大小及近 7／30 日估算。

| 路徑 | 分類 | 理由 |
| --- | --- | --- |
| `data/*.csv` | regenerated | 每日 OHLCV 快取 |
| `docs/index.html`、`docs/wsj.png`、`docs/barrons.png` | regenerated | 再生報告／截圖；先保留目前版本，即使目前 main.py 主要寫 JSON |
| `docs/atlas/**` | regenerated | `atlas/` 原始碼的 build:pages 與行情資料產物 |
| `docs/report/{latest,universe,breadth,breakouts,growth,rates,rates-history,themes,themes-tw,tw-chips}.json` | regenerated | 定期重建的報告 |
| `docs/report/series/**`、`docs/report/research/**` | regenerated | 指數時間序列、驗證後發布的公開市場資料 |
| `docs/report/fundamentals/{tw,tw-finmind,us-sec}.json` | regenerated | 官方來源自動下載的基本面快取 |
| `docs/report/financials/**`（包括 `index.json`） | record | 所有財報與完整 git 歷史明確保留 main |
| `docs/report/fundamentals/` 其餘檔案 | record | 財報續跑狀態、回補進度、凍結名單、來源紀錄；包括未來的 `q4-8k-state.json` |
| `docs/handoff/**`、`docs/report/q4-8k/**`、`docs/report/sec-q4-eps-*/**` | record | 人工審核、答案集、研究證據 |
| q4-eps 分類／acceptance ledger、fiscal-period 報告、coverage／restoration／ohlcv-backfill 報告、驗證與 review 文件 | record | 診斷、決策與驗收紀錄；即使可用腳本產生，也保留歷史 |
| 其他／未明確列入搬移白名單的檔案 | record | 不確定就留 main；新增搬移範圍必須另外 review |

共 **2,601 個 regenerated（162,498,450 bytes，含 data）**、**7,034 個 record（173,438,361 bytes）**。這些都是 current tree 的大小，並非歷史大小。

組裝網站保持目前 `docs/` 的全部 **7,120 個 URL 路徑**，總計 **291,796,838 bytes，278.28 MiB**，為十進位 1 GB Pages 上限的 29.18%。`data/` 與 generated 的 workflow relay 不會上傳網站。每次組裝重新測量，達到 1,000,000,000 bytes 就拒絕部署，並在 Actions summary 輸出大小。

預期 main 的**再生輸出增量為 0 MB／日**。main 的總增量不可能為零，因為財報歷史、手工紀錄與程式仍須保留。近 30 日保留路徑的新版本物件本機大小約 **57.35 MB，1.91 MB／日**，其中財報 **45.52 MB，1.52 MB／日**；近期 7 日含大量修補約 **4.43 MB／日**。這是容量預算參考，不是 GitHub 壓縮後增量的保證。估算方法：`git log --raw --no-abbrev --since='30 days ago' origin/main -- docs data keepalive.txt` 的 new-side blob 去重，再以 `git cat-file --batch-check='%(objectname) %(objectsize) %(objectsize:disk)'` 計算本機物件大小；pack／delta、重用既有 blob、回補與人工提交會影響數字。不要把初次 seeding 或 30 日 bulk backfill 誤認為穩態日增量。

既有 main 的約 511 MB 本機／420 MB GitHub 歷史不會因本 PR 變小。generated 每週只留 current tree 的一個根 commit；GitHub 對不可達物件的 GC 時間另計。

## 寫入與部署設計

唯一開關是 repository variable `GENERATED_BRANCH_MODE=1`。執行範圍由 [`.github/generated-paths.json`](../.github/generated-paths.json) 定義。

- 每個 reader job 以 main 的程式執行，先從 generated 還原所需的全部再生輸入到原路徑；刪除已在 generated 消失的舊輸出，避免 main 舊副本混入。財報與紀錄仍從 main 讀取。
- 每個 writer 只發布自己白名單內、相對還原基準真正改變的檔案。使用獨立 git index，fetch 最新 tip，將自己的變更重新套在新 parent，再 normal push；失敗最多重試五次。這是 index 層的 rebase，不會 reset／stash／commit 工作目錄中的其他檔案。
- 不同 producer 的變更可同時保留；同一個檔案被另一個 run 改成不同內容時，拒絕覆蓋並要求重跑 producer。daily／universe 的 `universe.json`、daily／rates 的 rates 等重疊輸出也適用。
- `financials.yml`、`fundamentals.yml` 先 normal push 財報／永久狀態到 main，再將 growth 等衍生輸出 push 到 generated。`refresh-us-rates.yml` 的測試 fixture 仍在 main。Atlas `.nojekyll` 若有變化仍在 main。
- `main.py` 的內嵌 publisher 在新模式停止自行 push，統一由 schedule 最後發布。`dify_report.py` 先只發布 screenshots，讓 LINE 即時取圖，再由 schedule 完成其他 daily 輸出。新送出的 LINE 圖片使用 `raw.githubusercontent.com/.../generated/docs/*.png`，避免拿到 main 的凍結副本；網站 `/wsj.png`、`/barrons.png` 路徑維持相同。歷史訊息中的 raw/main 圖片 URL 是 Git 分支內容 URL，不是 Pages URL，PR 2 移除 main 副本後不能靠 Pages 路由保留這種 URL；為遵守既有 URL 相容性，PR 2 必須把 main 的兩個凍結 PNG 留下作相容副本（不再更新，仍不會日增）；generated 持有之後的最新圖片。
- `q4-8k-backfill.yml` 只還原讀取輸入；fill 仍寫入 `backfill/q4-8k` 供審核，diagnose／backtest 仍寫 `runs/q4-8k`。財報、`q4-8k-state.json`、答案集不會送到 generated。既有 `95184ade77cd30c565d3873e9f9050f3ec7c0054` pin 不變。
- `keepalive.yml` 僅每月提交 root `keepalive.txt` 到 main，沒有搬移範圍，保留原行為。
- 每週 squash 使用最新 generated tree 建立無 parent commit，驗證 tree SHA 相等，且只執行 `--force-with-lease=refs/heads/generated:<已讀取 SHA>`。遇到併發更新重新讀取再做；程式沒有 force main 的路徑。
- Pages workflow 以 main 靜態文件加上 generated 的 docs 白名單組裝 `_site/`，用 `actions/upload-pages-artifact@v3`、`actions/deploy-pages@v4` 部署。main 與 generated 的 push、所有現有 main writer 的 `workflow_run`、squash 完成、手動觸發都支援；失敗流程也可能已儲存有效的部分輸出，因此完成時仍檢查最新 tree。
- `GITHUB_TOKEN` 的 push 不會引發另一個 push workflow，所以 producer 的 `workflow_run` 是必要的。純 orphan 分支不含 main workflows，故 seed 另放一個固定的最小 `.github/workflows/generated-pages-relay.yml`；外部使用者／token push generated 時，relay 呼叫 **main 上**的 reusable Pages workflow。generated 除再生輸出之外只允許這個固定 relay；restore／assemble／squash 發現 code 或 record 混入就拒絕。
- Pages 共用 `assembled-pages` concurrency，取消較舊 run，每次讀最新 main／generated tip，讓最後一次部署反映最新資料。

## Reader 查核

| Reader／用途 | 輸入與處理 |
| --- | --- |
| `main.py`、`report_metrics.py`、daily breadth／themes／rates | schedule checkout main 後先 restore；`data/*.csv`、latest／universe／series 保留原路徑 |
| Atlas directory／valuation／fundamentals_us／events／refresh-watchlists | atlas workflow 先 restore；原本從 `docs/atlas` seed `atlas/public` 的 fallback 邏輯不變；build base 保持 `/my-stock-screener/atlas/` |
| 財報／growth／官方基本面 builders | financials／fundamentals workflow 先 restore universe、latest、themes、data 與快取；financials 始終直接來自 main |
| universe／breakouts／chips／themes-tw／rates | 各 workflow 在 builder 前 restore；`.cache/universe-full` 的 Actions cache 保持原樣 |
| research institutional／volume／alerts／SEC／world | 每個 collector 與 publisher 都 restore；artifact 模組 allowlist、checksum 驗證及 collectors 無 write credentials 的分隔保留 |
| `scripts/research_{coverage,storage_audit,store}.py`、本機 screener／OHLCV／swing 工具、Atlas preview／驗收工具 | 本機先執行下列 restore 命令；CLI 的預設檔案路徑不變。這些工具不會假設 main 永遠帶著 current cache |
| `atlas-review.yml`（含 full-market-report job）、`research-review.yml` | 所有 PR reader jobs 也在測試／預覽前 restore，PR 2 刪除副本後仍能讀到 inputs |
| Atlas `src/report/*`、`StaticSeriesProvider`、財報讀取端 | 原本同源 `../report/`、`report/series`、`financials/` 與 research 子 JSON URL 全部由相同 docs layout 提供；以全站逐檔 SHA-256 比較覆蓋 |
| LINE report link／LIFF app | `REPORT_LIFF_URL`、Atlas Pages base 均不改；網站根報告、Atlas、report JSON、圖片的 Pages URL 不變；raw 圖片傳輸例外如上 |
| Supabase `market-chart`、`user-settings`／Cloud Run `breakout_alert`、Discord query | 搜尋程式及兩個 Dockerfile，沒有從 checkout 的 docs/data 讀取或從 raw/main 取這些輸出的依賴；Docker 明確只 COPY breakout_alert，`.dockerignore` 排除 docs/data。Supabase 的 Pages CORS origin 保持相同；資料仍走外部行情 API／Supabase |
| git history readers：`build_q4_eps_answer_set.py`、`backfill_financials_us_market.py`、`report_financials_us_coverage.py`、`restore_yahoo_nulls_from_git.py` | 全部 financials 路徑、index 與歷史不動，trim 禁用，答案集 pin 不動；需要完整歷史的本機使用完整 clone，不把 generated 當歷史來源 |

本機／臨時工作環境在切換後先執行：

```bash
git fetch origin main
GENERATED_BRANCH_MODE=1 python scripts/generated_branch.py restore
# 現在使用原有 CLI；writer 若需要發布，用對應 owner：
GENERATED_BRANCH_MODE=1 python scripts/generated_branch.py publish daily
```

不要在切換後手動 `git add docs/report data` 提交 cache 到 main。PR 2 可另外加入 `.gitignore` 作為本機防護，但永遠不能忽略 financials 或紀錄。

## Owner 切換順序

1. Review 並自行合併 **PR 1**。本次工作只開 PR，不代為合併。先暫停排程／外部 dispatch，**等待所有 in-flight writer 與 chained workflow_run 完成**；確認沒有舊模式 run 還能寫 main。這一步避免 seed 與設定變數之間遺漏最後一次輸出。暫停的 workflow 名單：schedule、atlas、financials、fundamentals、universe-full、research-data、tw-chips、themes-tw、refresh-us-rates、q4-8k-backfill、keepalive；記錄原本哪些已停用，之後不要誤啟用。
2. **先 seed**：在 owner 的完整 clone 執行 `python scripts/generated_branch.py verify --ref origin/main`，再執行 `python scripts/generated_branch.py seed --push`。後者會重新 fetch main、驗證每個搬移 blob、建立 orphan generated；若遠端已有 generated 則拒絕覆寫，必須先查明原因。使用 owner 可寫 workflow 的既有 git 憑證（PAT 需 workflow scope，或 fine-grained token 的 Workflows write）；因為初次建立固定的 relay workflow，不能使用一般 workflow 的 GITHUB_TOKEN 來 seed。這是憑證權限，沒有新增模式開關。此時 variable 仍未設定。
3. **設定 repository variable `GENERATED_BRANCH_MODE=1`**。檢查 generated 的 current files 與 seed 命令成功；不要將同名 secret／其他開關當成替代。
4. **Settings → Pages → Build and deployment → Source：GitHub Actions**。既有 custom domain／路徑不變。若 `github-pages` environment 有 deployment branch 限制，允許 main 與 generated relay 的部署 ref。手動觸發 `Deploy assembled Pages`，確認成功與 summary 容量；再恢復原本啟用的 producers／外部排程。generated 只是儲存分支，**不要**把 branch Pages 改成從 generated/docs 發布，否則會缺少 main 的財報及紀錄。
5. **觀察至少完整一天**：daily、Atlas、financials→fundamentals、universe、research 兩個市場時段、themes-tw、chips、rates 都須有成功 run；手動-only workflow 可檢查設定／按需執行。確認每次 producer 結束後都有 Pages run、頁面時間正確、LINE／LIFF 圖表與 research 子檔可載入，main diff 只有白名單 records／fixture／keepalive／code，generated 沒有 financials 或手工紀錄。若有同檔衝突，重跑該 producer；不要 force push。
6. **然後才開第二個小 PR**：僅對 manifest 選出的 regenerate 檔案 `git rm` main 副本，加適當 ignore 防止本機誤提交；保留 main 的全部歷史、financials、fundamentals 永久狀態與人工紀錄。為保留舊 raw/main PNG 連結，保留兩個凍結 PNG 副本並在 PR 2 說明這個相容例外。先檢查 seed 成功證據、generated 有所有 current outputs；生成器合法刪除的過期 hashed Atlas bundle 與 CSV 應以 seed manifest 和 producer run 追溯，不能把意外缺檔當作清理。PR 2 diff 必須只有已核准搬移路徑與 ignore 規則，沒有新 generator 行為。此 PR 也不要自行合併。

PR 2 搬移路徑可用 manifest 與保守 predicate 選出（輸出 NUL 分隔給 `git rm --pathspec-from-file=... --pathspec-file-nul`）；不要用 `git rm -r docs/report`。用 full clone 驗證 `git cat-file -e 95184ade77cd30c565d3873e9f9050f3ec7c0054^{commit}`，並確認 `git merge-base --is-ancestor <切換前main SHA> origin/main`。

## 回滾

**快速回復 PR 1 的原行為**：暫停並 drain producers，**取消設定 `GENERATED_BRANCH_MODE`**，再將 **Settings → Pages → Source 切回 Deploy from a branch，main /docs**，恢復 producers。PR 1 還保留 main 的舊副本，故立即可回到切換前版本；最新 generated 副本仍在 generated，沒有被刪除。這兩個設定恢復的是程式／部署路徑，不會自動把新資料寫回 main。

**要保留切換後最新內容，或 PR 2 已移除 main 副本**：在暫停／drain producers 後，先建立可 review 的回滾分支：

```bash
git fetch origin main
python scripts/generated_branch.py rollback-branch --branch rollback/generated-20261022
git push origin rollback/generated-20261022
# 開 PR 到 main，確認只還原 generated 的最新檔案／合法刪除，不改 records。
# Owner review／合併這個回滾 PR 後，再取消 GENERATED_BRANCH_MODE，切回 main /docs。
```

`rollback-branch` 只建立本機新分支，不 push、不改 main，不帶入 generated relay，並用正常 parent 保留全部 main 歷史。PR 2 後不能只切 branch Pages source 就期待缺少的 HTML／Atlas 自動回來。回滾時不刪 generated，方便核對與再次切換。

`trim-history.yml` 在任何模式都不恢復；絕不 force main，也不透過重建 main 來「回滾」。

## 驗證

- `python -m unittest discover -s tests -p test_generated_branch.py -v`：14 個整合／契約測試通過（隔離 bare origin，沒有 production 寫入），涵蓋 orphan seed、重複 seed 防護、全部盤點分類、開關、main.py 內嵌 push 防護、owner scope、財報分流、刪除／新增檔、模擬 PR 2、不同 producer push race、同檔衝突、每週 squash 與 lease race、producer 在 squash 後繼續、回滾分支、禁止 record 混入。
- `python scripts/generated_branch.py verify --ref origin/main`：用同一個 main SHA 做本機 orphan seed、分離 main 靜態檔案與 generated overlay，再逐檔比較 URL 路徑與 SHA-256；7,120 檔、291,796,838 bytes 完全相同。全程不 push。
- `python -m unittest discover -s tests -p 'test_research*.py'`：68 個通過。
- `python -m py_compile main.py dify_report.py scripts/generated_branch.py tests/test_generated_branch.py`、`actionlint 1.7.7 -shellcheck=''`、`git diff --check` 通過。actionlint 未另跑 shellcheck。
- PR 的 `Generated storage verification` job 會重跑 git 測試、從**當下 main**做 byte-identical 驗證，並用 upload-pages-artifact 打包供下載；切換後改驗證 live overlay，避免 PR 2 已刪 main cache 時把缺檔誤當基準。`Atlas PR verification` 照原有測試／browser 流程執行，新增 restore 支援。
- 實際 production Actions deploy、Pages source 切換及一整天觀察屬 owner 合併後 rollout，這次不執行；即時 CI 結果以 PR checks 為準。
