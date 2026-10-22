# 世界／題材可畫線 K 線圖

以最新 main `adfb6c7da` 為起點；原 PR #152 已由 owner 合併，本次另外建立 PR，不合併、不執行正式部署。

## 畫面與資料定義

- 原物料與匯率頁只呈現 K 線、必要的標的／單位／來源／日期及繪圖操作；移除原本的變化數字卡與單一價格走勢圖。四個世界分頁及既有 URL 保持相同。
- 六個原物料改用真實**期貨 OHLC**，不是把 EIA 現貨或 World Bank 月均價加工成 K 棒。頁面標示期貨、來源提供的合約名稱（不是所有歷史都屬於同一到期月）及展期限制。供應商的近月銜接時程、價差回補方式未完整揭露，不宣稱可直接用跨展期報酬做回測。
- 五組匯率改用延遲參考報價 OHLC；不是 Fed H.10、銀行牌告或即時 bid/ask。方向固定為每 1 基準貨幣兌報價貨幣。沒有集中交易所成交量，空量保留 null，畫面不顯示成交量。
- 題材詳情以參考 ETF 優先，沒有 ETF 時選第一個成分股；下拉可切換其餘成分股。圖表明確顯示實際標的，上方原有報酬、相對基準、寬度、排名與成分股清單均保留。題材等權指數只有日收盤，無法從各股不同時間發生的最高／最低價還原組合盤中高低，因此不製造「題材指數 OHLC」。
- 同一個 Lightweight Charts **5.2.1**、`ChartEngine`、`DrawingController` 與 Primitive 畫線實作；支援日／週／月 K、十種既有工具、選取／拖動、鎖定、刪除、復原／重做、磁吸、縮放與平移。資料缺失不代入假 K 棒。
- 主圖與嵌入圖共用原 `AppStore`、IndexedDB v4 與既有登入／雲端同步。繪圖狀態以穩定的研究圖表 namespace（商品、題材市場、題材 key、所選標的）與週期隔離，不更改主圖 activeSymbol、自選股或均線設定。namespace 的 FNV-1a 64-bit 編碼固定，不可任意更換而丟失畫線。

## 實際來源與 OHLC

沿用專案既有 Yahoo 行情來源，無付費 API／必要 key。圖表資料 API：

`https://query1.finance.yahoo.com/v8/finance/chart/<URL-encoded symbol>?range=5y&interval=1d`

| 圖表 | 來源代號 | 報價單位 | 顯示小數 |
|---|---|---|---:|
| WTI 原油期貨 | CL=F | USD/桶 | 2 |
| Brent 原油期貨 | BZ=F | USD/桶 | 2 |
| 天然氣期貨 | NG=F | USD/MMBtu | 3 |
| 黃金期貨 | GC=F | USD/金衡盎司 | 2 |
| 白銀期貨 | SI=F | USD/金衡盎司 | 3 |
| 銅期貨 | HG=F | USD/磅（沒有誤用公噸） | 4 |
| USD/TWD | TWD=X | TWD/USD | 4 |
| USD/JPY | JPY=X | JPY/USD | 4 |
| EUR/USD | EURUSD=X | USD/EUR | 4 |
| USD/CNY | CNY=X | CNY/USD | 4 |
| USD/KRW | KRW=X | KRW/USD | 4 |

每個標的都保留可點擊的 `https://finance.yahoo.com/quote/<encoded symbol>`。這是延遲的供應商資料，API 並無可用性保證；使用亦受供應商條款約束，不宣稱任意商業轉授權。題材 ETF／股票使用現有 `getDailyBars`／市場 provider，沒有另外建立行情系統。

驗證 `meta.symbol`、`dataGranularity=1d`、FUTURE／CURRENCY 類型、幣別、來源時區，以及 quote 陣列的 open/high/low/close。按來源時區排除當天尚未完成的日資料；有限 OHLC 且 `high >= max(open,close,low)`、`low <= min(open,close,high)` 才接受。負期貨價格可保留，缺值不變成零。不一致資料跳過並揭露筆數，沒有以收盤價修補高低。匯率來源確實存在這種品質問題。

本次實抓覆蓋 **2021-10-22～2026-10-21**：期貨各 1,256～1,257 根；FX 各 1,164～1,266 根有效日 K。FX 五年來源排除筆數：TWD 60、JPY 71、EURUSD 34、CNY 33、KRW 135。這不是完整無缺交易日覆蓋；後續以當次回應的品質數量為準。

真實範例：2026-10-21 黃金期貨 `GC=F`，O **4143.39990234375**、H **4160.60009765625**、L **4122.2998046875**、C **4150.0 USD/金衡盎司**；USD/TWD 收盤 **32.577598571777344 TWD/USD**。資料不是舊月均值改寫的 OHLC。

Python 原始資料按 Date 儲存；前端日資料以來源的 calendar date 使用 UTC 中午作座標錨點，並非聲稱該時刻成交。週／月 K 使用既有 aggregateBars，開盤取首筆、高／低取區間極值、收盤取末筆；只合併真實有效 K 棒，不補空缺日期。原始 null volume 不在畫面顯示；引擎數值介面使用的空量零不會寫回來源資料。

## 保存與每日覆寫

- 本次新增的永久市場歷史只包含 `data/world/*.csv` 的 K 線。增量讀前月來源，以 Date 合併：既有日期可被有效的來源修訂更新，**舊日期不得刪除**。相同資料重跑不改 bytes 或 mtime；原子寫入失敗保留舊檔。
- `docs/report/research/world-assets.json` 只保留當前標的 metadata、最新日期、品質及來源狀態，沒有原本的非 OHLC 統計價格歷史陣列。每日同名覆寫，不新增按日版本檔案。
- `docs/report/research/candles/<id>.json` 為上述 K 線的網站視圖，同名重建；K 線的永久權威紀錄是 CSV。CSV 與視圖的 SHA-256 都放在世界索引。來源失敗沿用上一份有效 K 線與實際日期並標示 stale。
- 抓取 job 維持 contents: read。世界 artifact 包含受白名單限制的 candle JSON 與 `_records/*.csv`；唯一的寫入 job 校驗全部 checksum 才 overlay。在 generated 模式，manifest `records.research=data/world/*.csv` 使 K 線先 normal push 留 main，current 報表送 generated；舊模式則同一筆 commit 包含 CSV 與當前報表。
- 其他既有報表仍依目前 pipeline 同名覆寫，不因本次新增功能另建每日備份。既有功能所需的有限計算窗口、財報、人工紀錄及原有保留政策未刪除；不改寫舊 Git 歷史，也沒有操作正式 `GENERATED_BRANCH_MODE` 開關或執行 squash／刪除。

11 份 K 線 JSON 約 **1.24 MB**，11 份 CSV 約 **1.20 MB**。世界索引由原約 666 KB 的統計價格歷史改為約 11 KB 的當前 metadata。K 線後續正常交易日只增加每組一筆有效日資料；source corrections 可能更新既有日期。

## 可執行驗證

```sh
python scripts/build_world_assets.py
python -m unittest discover -s tests -p test_research_candles.py -v
python -m unittest discover -s tests -p test_generated_branch.py -v
cd atlas
npm test
npm run typecheck
npm run lint
npm run build
VITE_PUBLIC_BASE=/my-stock-screener/atlas/ npm run build:pages
npm run test:site -- drawable-research.spec.ts themes.spec.ts research-modules.spec.ts
```

`drawable-research.spec.ts` 驗證世界與題材畫線、reload、日／週／月隔離、不同標的隔離與保留原統計；手機 Chromium 使用實際 CDP touchStart／touchMove／touchEnd，確認 loupe 在圖內正確定位。iPhone WebKit 以模擬手機尺寸與 pointer 操作驗證，並非實體 iPhone 驗收。`npm run test:research-pages` 另外以未 mock 的 Pages 建置讀取這次真實世界 K 線 JSON，等待商品與 FX OHLC 後才繼續。

PR CI 會執行全部 Python、Vitest、Typecheck、Lint、正常／Pages 建置、桌面／手機／WebKit 及 generated storage 驗證。實際最後結果與 head SHA 在 PR 說明及交付回覆列出；不把未執行的正式部署標為成功。
