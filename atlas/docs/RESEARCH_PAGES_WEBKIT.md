# WebKit 導頁時的封存回退競態

`research-pages-smoke.mjs` 的 `series/index.json … access control checks` 並非預覽伺服器的 CORS 或 Range 錯誤。WebKit 導頁先停止舊文件的網路載入，稍後才送出 `pagehide`。封存請求在這個空窗被瀏覽器取消，拒絕原因卻是 `TypeError: Load failed`，不是元件 AbortController 的 `AbortError`。

`ArchiveFirstProvider.daily()` 原先把它當成封存來源失效，通過尚未取消的 `pagehide` lifetime 檢查，接著呼叫 router。Router 的 `StaticSeriesProvider.listSymbols()` 在正在停止的舊文件啟動**新的** manifest fetch，WebKit 當場回報 access-control 錯誤，連 GET 都未送達伺服器。即使這個 Promise 有 catch，WebKit 仍把該診斷送到 Playwright `pageerror`。

這比「manifest 自己被 AbortController 取消」更精確：原來 manifest fetch 根本沒有傳 signal；先被取消的是上一層封存請求。只等 `pagehide` 的舊防護不足以涵蓋此順序。

## 可核對的證據

- 原始失敗：[run 42059838429，attempt 1](https://github.com/wudn9922/my-stock-screener/actions/runs/42059838429/attempts/1)，job `127358105005`。桌機通過，iPhone 只有 manifest 的錯誤；其餘版面、研究資料、月度圖表檢查通過。沒有把第二次重跑視為修復證據。
- [逐次結果與時序](research-pages-webkit-proof.json) 記錄每次 exit code、兩個引擎的 passed/errors、原始輸出 SHA-256、封存取消／manifest 啟動／pagehide 時序，以及資料與 bundle 指紋。
- 獨立 Node HTTP 伺服器搭配**真實 provider 程式**，不使用 Python、不帶 Range、不攔截瀏覽器請求，也能在原版重現相同錯誤。這排除 `serve_research_preview.py` 的回應標頭與 Range 邏輯。
- 原預覽伺服器的 manifest 普通 GET 為 `200 application/json`、22 bytes；`bytes=0-4` 為 `206`、`Content-Range: bytes 0-4/22`，本文等於原檔前五個 bytes。完整 smoke 的研究分片檢查也維持通過。

## 修正與保留的行為

封存下載現在以文件為共用請求的 owner，在 `beforeunload` 主動 abort，`pagehide` 仍作補充。signal 與原本的 8 秒逾時組合，保護範圍包含 response 本文的讀取；finally 移除兩個 listener。listener 不呼叫 preventDefault、不設定 returnValue，不會新增離頁確認框。

如此能在瀏覽器自行停止載入前，把取消明確傳回 `AbortError`。`ArchiveFirstProvider` 往上傳遞取消，不把它轉成「沒有封存」而啟動回退。年度檔的 `allSettled` 也保留取消，不能拿已完成的部分年度掩蓋它。

404、503、網路失效及真正逾時仍走原本回退；沒有更改 `StaticSeriesProvider`、正式資料、API、伺服器或工作流程。單一圖表取消不能中止其他圖表共用的封存下載。取消離頁後，已拒絕的 index Promise 會移除，下一次讀取可重試；沒有把整份仍開著的文件永久標成 aborted。既有 BFCache lifetime 與其測試保留。

## 重現

安裝 lockfile 的瀏覽器與系統依賴後，在 `atlas/` 執行：

```sh
npm ci
npx playwright install --with-deps chromium webkit
npm run test:research-navigation
```

新測試以 localhost 真實 HTTP 保留待回應的 archive index／CSV 本文，等伺服器確認收到請求，立即導頁。另驗證 HTTP 503 回退與取消離頁後重讀。兩個引擎各四個案例；所有 `pageerror` 均須為空。

要對照原版，在獨立 worktree checkout `afe469f5229fd66b3515cfa473053dd81480a229`，安裝依賴後，從修正分支執行同一支腳本：

```sh
RESEARCH_NAV_SOURCE=/absolute/path/to/before/atlas npm run test:research-navigation
```

原版會在第一個 WebKit 案例，以完全相同的 access-control pageerror 失敗；修正版八個案例全部通過。這個測試已接到原有 `test:research-pages` 指令，因此現有 CI 會執行它，不需要改 workflow。

完整 smoke 使用既有 CI 的 Pages 預覽流程，資料固定為 generated `4bf417ce28eb99a0a7049c1f4ce30a1648ab580d`，前後共用同一份只讀 report/public 資料；`market-warnings.json` 使用原 CI 在缺檔時才放入的真實試算 fixture。建立預覽後，分別針對原版與修正版 bundle 執行：

```sh
RESEARCH_PAGES_RUNS=20 RESEARCH_PAGES_DIAGNOSTICS=1 \
  RESEARCH_PAGES_OUT=/absolute/path/to/fresh/before-or-after \
  node scripts/research-pages-repeat.mjs
```

每次獨立啟動兩個瀏覽器、走完原 smoke 全部旅程，順序執行、無重試。輸出目錄必須是新的，以免混入舊結果。診斷 preload 只記錄 lifecycle、原生 fetch 的啟動與 signal、request/response、requestfailed 和 pageerror，不為 fetch 加入 rejection handler，也不阻止 unhandledrejection。原 smoke 只增加輸出目錄選項，所有操作、錯誤收集、數值、206、overflow 斷言都未更改。

實測環境為 Debian 13、Node 24.19.0、Playwright 1.63.0、WebKit 26.6/build 2359、Chromium headless shell/build 1243。Cloud 的系統依賴以官方 Debian 套件解壓，launcher 保留 LD_LIBRARY_PATH；`PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1` 僅避開看不到非系統路徑函式庫的 host preflight。瀏覽器仍實際啟動並執行全部斷言，沒有略過測試、忽略 HTTPS 錯誤或關閉 TLS 驗證。這是模擬瀏覽器驗證，不是實體 iPhone Safari 簽核。

## 驗證結果

| 引擎 | 修正前通過／失敗 | 修正前 pageerror／離頁後 manifest | 修正後通過／失敗 | 修正後 pageerror／離頁後 manifest |
| --- | --- | --- | --- | --- |
| desktop-chromium | 20／0 | 0／0 | 20／0 | 0／0 |
| iphone-webkit | 0／20 | 71／71 | 20／0 | 0／0 |

兩階段都是同一份 9,714 檔 report 與相同的 public 資料；只有 app bundle、index.html、sw.js 與版本檔因重新建置而不同。前後每次研究資料、Range 206、月度圖表與 overflow 檢查均通過；修正前僅因 WebKit pageerror 失敗。

完整 smoke 的公開 archive 請求照原網址讀取，沒有攔截或加延遲。測後查得最新 archive commit 為 `339f57e742d904976eefbf73b7fad506fff6d9c7`（2026-11-05 17:38 UTC，早於兩階段測試），查詢結果也收入 proof。本機 20 次的失敗比例不是 CI 發生率估計；競態需要離頁當下仍有封存下載未完成，獨立 HTTP 回歸則以保留真實回應來固定這個條件。

逐次計數與總表由實際輸出彙整於 [proof](research-pages-webkit-proof.json)，PR 另列完整檢查結果。前一次環境的 `/tmp` 紀錄在重啟後消失，因此正式證據採重新執行、保存在工作目錄的完整前後各 20 次，沒有混用遺失的紀錄。

新增 `archive-navigation.test.ts` 的八項單元案例；現有 unit 與 e2e spec、原 smoke 斷言均未刪減。只做 primary self-review，沒有宣稱獨立簽核。只推 PR，不合併或部署。
