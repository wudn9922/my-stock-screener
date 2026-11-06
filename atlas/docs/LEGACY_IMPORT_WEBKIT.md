# 舊版匯入：等待「我的」頁完成掛載

`legacy-import.spec.ts` 原先把頁尾匯入卡片可見當成「我的」頁已就緒。但 `Site.tsx` 的 `LegacyImportCard` 在路由的 Suspense 外，冷啟動 ConsolePage 時，卡片可以先出現在載入占位下方。卡片本身已有 handler，整頁版面仍會在懶載入頁面掛載時改變。

WebKit 的點擊可能以占位版面的按鈕座標開始，輸入事件尚未處理完時頁面就替換成參數表單。匯入按鈕移走，click 沒有送到它。控制器因此維持 `available`／`aria-busy=false`，也沒有開始匯入所需的授權重查。根因是測試的頁面就緒條件不足；不是匯入請求被取消或合併後遺失成功狀態。

## 原始 CI 證據

[PR #231 run 42168222865 attempt 2](https://github.com/wudn9922/my-stock-screener/actions/runs/42168222865/attempts/2)，`compact-ui-regressions` job `127741896613`：109 通過、1 失敗。

下載的是同次失敗上傳的 artifact `13481483973`，ZIP SHA-256 為 `4ba2ca775226d4d87b632fbdfa7c40140dd156af30f5066fce601c731302beb8`，與 job log 一致。其 trace 顯示：

| 時序（trace monotonic ms） | 事件 |
| --- | --- |
| 143329.989 | 開始 confirm click；畫面仍為路由載入占位＋可操作的匯入卡片 |
| 143375.267 | action snapshot：按鈕 box `x=29,y=466.59375,w=56,h=44`，目標點 `(57,488.59)` |
| 143382.701 | performing click action |
| 143483.488 | click action done |
| 143483.798 起 | ConsolePage 的 groups.list、stocks.list、watchlists.save 才開始 |
| 143498.705 | after snapshot 已出現 parameter-console，原卡片位置變成參數表單 |
| 接下來 10 秒 | 匯入卡片仍是提示；沒有新的 access.get，沒有匯入新增的 legacy-drawing 儲存 |

所有記錄到的雲端 fixture 回應均為 200，沒有取消／失敗的匯入請求。`LegacyImportController.copy()` 一啟動就會發布 importing，接著呼叫 refreshAccess；此處兩者皆未發生。CI trace 沒有記錄 DOM 的 pointerdown/up 目標；下列受控重現另外補上這項證據，不把兩者混為同一份紀錄。

[CI 點擊前：載入占位與可見卡片](screenshots/legacy-import-webkit/ci-loading-prompt.png) · [CI 失敗後：同座標附近已變成參數表單](screenshots/legacy-import-webkit/ci-after-lost-click.png)

## 修正

只修改 e2e 就緒條件：在各個確認匯入的入口，先斷言既有具名稱的 region「原有均線參數控制台」可見，再執行原來的原生 click。這代表 ConsolePage 已經真正掛載，不能只根據邊界外的卡片判斷。

全部原測試步驟、click 與斷言保留；移除新 helper 與五處呼叫後，檔案與原版逐字一致。完成文字仍是 `/匯入完成|已匯入/`，expect timeout 仍為 10 秒，test timeout 仍為 60 秒。沒有重試點擊、force、dispatchEvent、skip、延長 timeout 或更動正式程式／登入／Supabase／SQL。fixture 原有的衝突保留、原資料不變、server-only 畫線、跨帳號隔離與重跑不重複檢查均維持。

## 受控重現

可重跑的診斷腳本 `scripts/legacy-import-race.mjs` 複製同一份 spec 到忽略的 test-results，沿用原 fixture 與全部斷言。它只控制冷路由的到達順序：保留 ConsolePage 的真實本機 JS 回應，在原生 pointerdown 後釋放，等頁面掛載再送 pointerup。這模擬 CI 的輸入／懶載入交錯，並記錄 trusted 原生事件；不是一般 20 次測試的輸入方式，也沒有向正式服務寫入資料。

修正前：pointerdown 在匯入 BUTTON，pointerup 在「分組」LABEL，click 在 MAIN；沒有觸發匯入授權重查，原 10 秒完成斷言失敗，與 CI 相同。

修正後：guard 先等頁面掛載，pointerdown/up/click 全在匯入 BUTTON；授權重查、雲端合併與原 spec 後續全部斷言通過。診斷 gate 的 5 秒自動釋放只用於重現，沒有加入日常 spec 或變更原有 timeout。

```sh
cd atlas
# lockfile 與瀏覽器準備完成後；退出碼 1 是原版的預期重現結果。
LEGACY_IMPORT_SPEC_REF=2e8799ba4dc9fa01c4d1e617ab79a551b0c6d21c \
  LEGACY_IMPORT_RACE_OUT=/absolute/path/to/race-before \
  node scripts/legacy-import-race.mjs

# 預設讀取目前工作樹的修正版；退出碼應為 0。
LEGACY_IMPORT_RACE_OUT=/absolute/path/to/race-after \
  node scripts/legacy-import-race.mjs
```

## 正常流程各連跑 20 次

兩階段都使用原本的 375×667、iphone375-webkit、同一 fixture 與同一命令；沒有診斷 hook、CPU 壓力或人為延遲：

```sh
PLAYWRIGHT_JSON_OUTPUT_FILE=/absolute/path/to/before-or-after.json \
  npx playwright test --config e2e-site/playwright.compact.config.ts \
  legacy-import.spec.ts --project=iphone375-webkit \
  --grep 'cloud explicitly merges old settings, preserves account conflicts, and can rerun without duplicates' \
  --repeat-each=20 --workers=1 --retries=0 --trace=on \
  --output=/absolute/path/to/fresh/artifacts --reporter=line,json
```

| 組別 | 原版 | 修正版 |
| --- | --- | --- |
| 正常流程 20 次 | 20 通過／0 失敗，3.4 分鐘；本機未自然撞到偶發時機 | 20 通過／0 失敗，3.3 分鐘 |
| 固定交錯的診斷案例 | 0 通過／1 失敗；同一個 10 秒完成斷言失敗 | 1 通過／0 失敗；包含完整後續合併與重跑檢查 |

不把原版 20 次未重現當成修復證據，也不聲稱正常流程前後有統計上的失敗率改善。因果證據為指定 CI trace、可重跑的輸入交錯，以及修正後在相同交錯下通過。[逐次結果與指紋](legacy-import-webkit-proof.json) 記錄每次 duration、retry、errors、trace SHA-256 與相關回歸結果。

環境沿用本次 WebKit 調查的 Debian 13／Node 24.19.0／Playwright 1.63.0／WebKit 26.6 build 2359。Cloud 函式庫以官方 Debian 套件解壓，host preflight 使用既有非系統函式庫路徑的設定，所有案例實際執行、TLS 驗證保留；這不是實體 iPhone Safari 簽核。一次修正後啟動與 npm ci 誤重疊，只有模組載入錯誤、零案例執行，獨立保留記錄；正式的 20 次是在安裝完成後重新從第 1 次開始，沒有混算。
