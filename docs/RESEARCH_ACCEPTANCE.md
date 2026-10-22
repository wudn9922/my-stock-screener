# 七項研究功能驗收（PR-only）

交付分支：`codex/seven-research-modules`；[PR #152](https://github.com/wudn9922/my-stock-screener/pull/152)。起點為最新 main `f82e42d776f5fec58786c2ac5cd0dd9e6f195f9b`。依最後指示，**不合併、不部署正式站**。以下「可用」是 PR／測試建置可用，不表示正式網站已更新。全程由同一模型實作與自我審查，沒有其他模型或外部 AI 審核。

## 功能矩陣

| 模組 | 功能／前端 | 後端／實際來源 | 已取得歷史 | 排程 | 單元測試 | 手機 E2E | 正式部署 | 外部限制 |
|---|---|---|---|---|---|---|---|---|
| A 台股法人買賣超 | 可用：股／張、1/5/10/20/60 日、連買賣、成交量比、價格對照柱圖、法人獨立切換 | TWSE T86、TPEx JSON／Big5 CSV，日期及分類合計已實抓驗證 | 2026-07-20～10-21，兩市場各 65 個報表日；個股缺列不補零 | 新獨立 job 已設定 | 通過 | Chromium／iPhone WebKit 通過 | 未部署（依指示） | 12 檔在回補報表未出現，保留缺值；非 100% 全市場完整法人樣本 |
| B 大戶＋股東人數 | 可用：兩級比例／人數、週變化、4/13/26/52 週基準、雙線圖；延伸原股東卡片，預設收合 | TDCC 庫存股數口徑，第 16 級排除、第 17 級分母，已驗證實際 CSV | 大戶分級 **只有 2026-10-16 一週**；原外資持股／股東人數歷史保留 | 沿用 `tw-chips.yml` | 通過（含既有 44 項相容測試） | 通過 | 未部署 | 公開下載僅最新週，4/13/26/52 週資料尚不足，顯示 N/A 並續累積 |
| C 異常量／流動性／價量結構 | 可用：可調門檻，5/10/20 日四項比例保留分子／分母、RVOL、ADV、Amihud、零量／有效樣本與風險 | 重用既有 `data/` 與全市場日 K cache；較新的日報優先，同日期採完整 cache | 現有 universe 全部 7,317 檔已有快照；各指標仍依有效樣本數判定 | 新獨立 job 已設定 | 通過（含因果窗口、缺值、公司行動等） | 通過；另有畫線／週期隔離回歸 | 未部署 | 不是所有股票都有完整 60 日／可用 RVOL；數量見下表 |
| D 注意／處置 | 可用：搜尋、市場／種類／公告區間篩選、30/60/90 日次數、原因、限制、歷史及官方原文 | TWSE／TPEx 四種正式來源及歷史端點已實測 | 2026-07-19 起查詢，2,364 筆事件；最新公告日依來源分別顯示 | 新獨立 job 已設定 | 通過 | 通過 | 未部署 | 公告期間外是否順延／解除須官方確認；不自行宣稱解除或明日必處置 |
| E 法人買超日價格區間（估計） | 可用：三類法人、5/20/60 日、正買超日數／價格範圍／加權代理／距離與覆蓋率 | 官方法人股數＋TWSE／TPEx 同日成交值與量，既有日 K 檢查價格基準；每筆估計日及基準可查 | 兩市場各 65 日官方市場成交輸入；不足／價格基準不符會顯示配對筆數 | 隨 A 獨立更新 | 通過 | 通過 | 未部署 | 官方市場日均價優先，缺值才明示收盤代理；價格基準不符者排除。不是實際法人均價／庫存成本 |
| F SEC 8-K／8-K/A | 可用：1/7/30/90 日、股票／分類／原始修訂、個股及自選清單、官方原文 | SEC ticker map、submissions、分頁、每日索引與文件；CIK＋Accession 去重、股別別名保留、進度／冷卻 | 1,254 個去重事件、33 個修訂；7 個每日索引及 38 檔逐股查詢；22 筆有實際內容節錄 | 新獨立 job 已設定 | 通過 | 通過 | 未部署 | **EXTERNAL BLOCKER（部分歷史／內容）：**SEC 間歇 403，已持久化冷卻；其餘保留「內容尚未解析」，不編造摘要 |
| G 世界原物料／匯率 | 可用：四個穩定 URL、6 原物料、5 匯率、單位／方向／頻率、歷史圖與期間變化 | EIA、World Bank、Fed H.10；ECB 同日交叉匯率備援亦已驗證 | 能源日資料到 2026-10-20，匯率到 10-16；金銀銅月資料到 **2026-09** | 新獨立 job 已設定 | 通過（方向／頻率／曆月／過期 YTD） | 通過，含返回／重載 | 未部署 | 已找到並驗證 2026 年版 World Bank 月資料，解除舊檔限制。金銀銅維持月均基準；1d/1w 為 N/A，沒有冒充每日即時行情 |

新排程在未來獲准 merge 後才會生效；本次沒有執行 main 的資料發布或正式部署 workflow。PR 的 full-market job 僅讀取既有 cache 並上傳產物，沒有新增行情請求或寫入 main。所有正式資料日期、來源與覆蓋統計另存 [coverage.json](research-verification/coverage.json)。

## 真實資料範例與公式

以下來自實際回應與既有行情，不是測試造數：

- **2330.TW，2026-10-21 外資**：買進 15,102,335 股、賣出 21,256,233 股，淨額 **−6,153,898 股**；近 20 日淨額 −133,736,579 股（20/20 筆）。股轉張直接除以 1,000。同日官方成交股數 27,095,005，買賣超占量比 −22.7123%。
- **TDCC 2330，2026-10-16**：總集保戶 3,098,860；>400 張 2,617 戶、22,647,940,885 股；>1,000 張 1,476 戶、21,928,634,563 股。分母 25,932,370,067 股，比例分別 **87.334636%／84.560858%**。這是集保戶分布，不是最終受益人歸戶或法人交易。
- **2330 價量，2026-10-21**：成交量 23,935,196 股，前 20 個有效日均量 23,407,574.3，`RVOL20 = 23,935,196 / 23,407,574.3 = 1.022541`。近 20 日上漲縮量 `3/7 = 42.9%`、下跌放量 `6/9 = 66.7%`；其餘有效日可含平盤日，沒有拼成不透明總分。
- **2330 外資近 20 日估計**：5 個正淨買超日，以同日官方成交金額／官方股數加權，參考價 **2,522.8199 TWD**，市場日均價區間 2,472.4275～2,567.1145；買超日收盤區間 2,480～2,575，10-21 收盤 2,420，距參考價 −4.0756%。公式為 `Σ(當日市場均價或明示代理 × max(淨買超,0)) / Σmax(淨買超,0)`；本例是市場日均價代理估計，非法人實際成交／剩餘庫存成本。
- **2330 官方市場成交，2026-10-21**：成交金額 65,515,856,247 TWD，官方股數 27,095,005，市場日均價 2,418.00495；ADV20 為 66,053,541,742.55 TWD（20/20 官方金額）。原日 K 成交量與官方股數不同，價量訊號維持原日 K，官方量另列對照；不把官方成交值除以另一來源的量。
- **SEC 實際內容**：AAPL，2026-09-01，Accession `0001140361-26-035325`，Item 5.02，已取得 [官方 8-K/A 原文](https://www.sec.gov/Archives/edgar/data/320193/000114036126035325/ef20081427_8ka.htm)。內容節錄只來自文件文字，分類標題來自正式 Item，不由 Item 猜測事件細節。
- **EIA／Fed**：WTI 107.89 USD/桶（10-20）；USD/TWD 32.4000、EUR/USD 1.1231（10-16），各自表示每 1 USD 兌 TWD／每 1 EUR 兌 USD。金價 4,319.00、銀價 64.60 USD/金衡盎司、銅價 14,474.00 USD/公噸，均為 **2026-09 月均基準**，不稱每日即時行情。

ADV 使用本幣每日成交金額（缺官方 amount 則 close×volume 代理）。Amihud 為 `mean(abs(日報酬小數)/當日本幣成交金額)×1,000,000`。日內高低振幅不是 bid-ask spread。資產變化採實際觀測日、曆月／曆年基準，不補值；來源不同頻率不冒充每日行情。完整公式、原始欄位、精度、時間基準、限制及每個實際 URL 見 [RESEARCH_DATA.md](RESEARCH_DATA.md)。

## 覆蓋與日期（全市場／日報分開）

| 指標 | 台股 | 美股 |
|---|---:|---:|
| 原 universe 普通股名單 | 1,980 | 5,347 |
| 沿用原規則排除近 20 日零成交 | 2 | 8 |
| 原 universe 實際列出、此次快照涵蓋 | **1,978／1,978** | **5,339／5,339** |
| 占完整原始名單（含被原規則排除者） | 99.899% | 99.850% |
| 每日日報名單涵蓋 | 1,011／1,011 | 523／523 |
| 最新資料分布 | 1,550 檔至 10-21；428 檔至 10-20 | 5,338 檔至 10-20；1 檔至 10-14（stale） |
| 最新可算 RVOL20 | 1,947 | 5,094 |
| 具完整最近 60 日有效成交量觀測 | 1,722 | 4,661 |
| 最近 20 日 ADV 全部採官方成交金額 | 1,816 | 0（明示收盤×量代理） |
| 法人報表至少一筆 | 1,966（上市 1,086、上櫃 880） | 不適用 |
| 法人完整 60 日外資觀測 | 1,522 | 不適用 |
| 外資 20 日估計具完整價量配對／有正淨買超參考價 | 1,599／1,916 | 不適用 |

快照有值不等於每一個指標都完整；介面保留各指標觀測數、低可信度與 N/A。TDCC 最新分級覆蓋上述 1,978 檔，歷史仍只有 1 週。SEC 可對應 5,332／5,339 檔 CIK，但這**不是全部公司均已查完 90 日申報**：逐股查詢只有 38 檔，每日索引已收錄 10-13、14、15、16、19、20、21；事件涉及 961 個發行人。公告資料是事件筆數，不把沒有公告等同於資料缺失，也不把來源不可用視為正常。

全市場產物來自 [Actions run 39723556039](https://github.com/wudn9922/my-stock-screener/actions/runs/39723556039) 的 `full-market-research`，來源 commit `6029e62d65bd5d7241420edc16a803b04cb2f54a`，之後用 130 份官方成交輸入更新成交金額指標與法人價格估計，保留既有價量訊號。匯入產物時保留 649 檔較新的日報快照，沒有以較舊 cache 倒退資料。產物追溯見 [full-market-artifact.json](research-verification/full-market-artifact.json)。再以 [完整 cache 的官方成交對帳產物](research-verification/full-market-cash-reconciliation.json) 核對：價量指標沒有數值變動；390 份法人快照採用完整 cache 的價格輸入／精度及公司行動檢查，20 日完整配對增至 1,599 檔，保守排除不一致基準後 1,916 檔有參考價。所有資料日未倒退。另實抓 TPEx 回補最早日 07-20，808 檔普通股解析結果與原 cache 完全相同，見 [日期／雜湊驗證](research-verification/tpex-history-check.json)。

## 執行過的驗證

| 指令／檢查 | 實際結果 |
|---|---|
| `npm ci` | 通過（現有 lockfile，無新增 npm 依賴） |
| `python -m unittest discover -s tests` | **本機 1,457 通過、0 失敗**（包含最新日報優先、官方成交金額與 cache 失效復原測試）；CI 實際結果以 PR checks 為準 |
| `python -m unittest discover -s tests -p test_research.py` | **55 通過、0 失敗** |
| `python -m unittest discover -s tests -p test_tw_chips.py` | **44 通過、0 失敗** |
| `npm run typecheck`／`npm run lint` | 通過 |
| `npm test` | **957 通過，79 檔**；包含新增 17 項實際全市場 Schema／SSR／路由／缺值測試 |
| `npm run build` | 通過 |
| `VITE_PUBLIC_BASE=/my-stock-screener/atlas/ npm run build:pages` | 通過；沒有替換正式 `docs/atlas/` |
| `npm run test:site -- research-modules.spec.ts` | **15 通過**：桌面 Chromium 5、手機 Chromium 5、iPhone WebKit 5 |
| `npm run test:site -- financials.spec.ts pe-river.spec.ts stock-volatility.spec.ts screener-filters.spec.ts unified-console.spec.ts` | **138 通過**，三個專案各 46；涵蓋原財報、河流圖、選股、自選清單、原波動統計等 |
| `npm run test:browser -- drawing-v1.spec.ts timeframe-scope.spec.ts --project=desktop-chromium --project=mobile-chromium --project=iphone-webkit` | **6 通過**，含畫線操作、週期隔離、重載、匯出與原鎖定狀態 |
| 實際 Pages 建置、未 mock 的桌面／iPhone WebKit HTTP 冒煙 | **2 專案通過**；各 8 筆研究 JSON 均 HTTP 200、0 page error、0 橫向溢出，見 [receipt](research-verification/pages-live-smoke.json) |
| `actionlint` 三份新增／調整 workflow | 通過 |
| 官方端點小規模抓取＋65 日回補 | 實際執行；SEC 限流仍保留真實狀態；World Bank 2026 版下載已驗證，可取代過期舊檔 |

PR Actions 現在也執行 `npm run test:research-pages`：啟動實際 Pages 建置與完整 report 目錄，不攔截 HTTP 資料，並上傳 `research-pages-live` 截圖與結果。

Vitest 原先只探索 `.test.ts`，已擴充包含 `.test.tsx`，並實際執行新增 17 項測試。第一輪 GitHub Python 工作因舊測試需要 `origin/main`／歷史 commit 而失敗，已將 checkout 設為完整歷史，沒有略過該測試。全市場 Schema 測試在並行讀取大量實際檔案時曾超過預設 5 秒，已對兩項全量驗證設定 30 秒預算，重跑 957 項全數通過。最終 CI／提交狀態請直接查看 [PR checks](https://github.com/wudn9922/my-stock-screener/pull/152/checks)，本文件不把排隊中的工作預寫成 PASS。

本機等價 Pages 實測已通過；新增可重跑腳本後的一次啟動曾被 Cloud 自動審批服務以用量上限拒絕，沒有冒記為 PASS。冷卻重設後已重試成功。PR CI 第一輪真實 Pages 測試也找到預設 build base 與網站 URL 不符，已明確以 `/my-stock-screener/atlas/` 建置，並重跑驗證；原建置腳本的開發預設未改動。

WebKit 使用 Playwright iPhone 13 模擬。Cloud 缺少的系統庫下載並解壓到 `/tmp`，未修改系統安裝；測試確實啟動瀏覽器，不是只跳過依賴檢查。這不是實體 iPhone Safari 效能認證。已檢視 [iPhone 世界頁](research-verification/iphone-world.png)、[iPhone 個股](research-verification/iphone-stock.png)、[桌面世界頁](research-verification/desktop-world.png)；主要數值可讀、來源日期可換行、無頁面橫向溢出，表格只在卡片內捲動。

## 檔案、審查與可執行部署驗收

- 資料擷取／數學：`scripts/build_research.py`、`research_sources.py`、`research_metrics.py`、`research_io.py`、`research_coverage.py`、`build_sec_radar.py`、`build_world_assets.py`；延伸 `build_tw_chips.py`，加上程式例外的非零結束，來源失敗仍能保留前份資料。
- 前端：`atlas/src/report/research.ts`、`ui/research/*`、`pages/WorldAssetsView.tsx`；對既有 World／Financials／Markets／Console／Chart 只做局部整合，包含手機頁標題、穩定 route、按需載入與股票切換隔離。
- 排程／驗證：`research-data.yml`、`research-review.yml`、`atlas-review.yml`，Python fixtures／tests、Vitest、Playwright。資料快照獨立提交，方便只看程式碼 diff。
- 自我審查：確認沒有新增 service-role／LINE token／付費 API、沒有修改 Supabase RLS／登入資料／原有策略與圖表引擎。SEC 文件只作不可信文字解析，沒有可執行指令或 AI 提示詞途徑；未新增自動下單或通知。

[既有正式網站](https://wudn9922.github.io/my-stock-screener/atlas/) 已實際 GET，HTTP 200；[雜湊與時間](research-verification/production-before.json) 是原 main 部署，**不表示新功能上線**。正式網址／路由沒有更改。沒有合併、正式 migration 或部署動作；新功能目前全部位於 PR／測試建置。

可在本機或 CI artifact 重現部署驗收（完整重建／資料更新指令見 RESEARCH_DATA.md）：

```bash
cd atlas
VITE_PUBLIC_BASE=/my-stock-screener/atlas/ npm run build:pages
cd ..
mkdir -p /tmp/atlas-pr-review/my-stock-screener
cp -a atlas/dist-pages /tmp/atlas-pr-review/my-stock-screener/atlas
ln -s "$PWD/docs/report" /tmp/atlas-pr-review/my-stock-screener/report
python -m http.server 4188 --bind 127.0.0.1 --directory /tmp/atlas-pr-review
# 開啟 http://127.0.0.1:4188/my-stock-screener/atlas/?page=world&view=commodities
# 在另一個終端（需已安裝 Playwright 瀏覽器）：
# cd atlas && npm run test:research-pages
# 未 mock 實際來源與手機截圖／receipt 寫入 atlas/test-results/research-pages-live/。
# 另驗證 view=indices / 預設美債、個股財報籌碼及研究面板、返回與重載。
```

目前剩下的是資料來源與覆蓋限制：TDCC 歷史需逐週累積；SEC 已實測冷卻後續跑，逐股檢查從 16 增至 38、去重事件增加 59 筆；被拒絕的歷史／文件會繼續按冷卻續跑；金銀銅使用已驗證的免費官方月資料，仍不是每日即時價格。這些限制均未以零值、假資料、固定測試例或捏造內容取代。
