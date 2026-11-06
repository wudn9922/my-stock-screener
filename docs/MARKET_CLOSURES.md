# 全市場休市日：判定、掃描與資料保留

本次修正只產生衍生日期證據、阻擋新的休市日輸入，並在讀取時隱藏舊假列。**沒有修改、刪除或推送 kline-archive 分支內容，也沒有執行正式排程。**

`scripts/market_sessions.py` 為共用核心。先檢查有效普通股觀測的正成交量：占比 ≥5% 或至少50檔就保留當天所有列，並在指數缺列時記錄 `primary-index-gap-with-trading`。沒有這項成交證據時，主要指數 ^TWII／^GSPC 在成功讀取、OHLC 有效且已收盤的首末日期之間缺列（包含完全沒有股票觀測），或至少 95% 的有效普通股樣本為已知零量且開高低收完全相同，就視為休市。零量不必等於前一日收盤，未知量不算零。週末照舊排除；正常交易日的少數停牌股票保留。

指數必須先驗證 per-symbol index 的年度 bytes／SHA-256／筆數／日期範圍。整份讀取失敗、超過最後已知 bar 的日期都不是休市證據。每日快照以有效名單總數作分母，部分回應不會放大成 95%；歷史樣本保留近 20 日四分位覆蓋檢查，stage／掃描另沿用至少 50 檔。這些覆蓋條件避免少數孤立資料關閉整個市場。

`docs/report/market-closures.json` 包含：`version`、`generatedAt`、`threshold`、市場的主要指數來源／覆蓋範圍、各 `closedSessions` 的 `date`、`reasons`、觀測／零量／未知量／有效分母數、占比與覆蓋參考。`primary-index-missing`、`flat-zero-cohort`、`weekend`、`published-closure` 可追查判定來源。完整掃描額外列出受影響列數、樣本代號與 archive commit。每日新增觀察會保存於可丟棄 cache，再透過 universe owner 發布，兩種 generated／舊 commit 模式皆支援；不發任何通知。

## 本次唯讀掃描

報告：[market-closure-scan-20261106.json](research-verification/market-closure-scan-20261106.json)。固定掃描 archive commit `339f57e742d904976eefbf73b7fad506fff6d9c7`，讀取 7,689 個代號、41,507 個年度 CSV、9,772,433 列，無無效 OHLC。這是該 commit 的快照，不宣稱此後的正式分支內容相同。

| 市場／日期 | 疑似休市日列（含 ETF） | 普通股零量平盤／有效觀測 | 判定理由 |
| --- | ---: | ---: | --- |
| 台股 2026-10-23 | 2,214 | 1,981／1,981（100%） | 主要指數缺列＋零量平盤樣本 ≥95% |
| 台股 2021-04-06 | 1（僅 ^TWOII 指數） | 0／0 | 主要指數缺列且無普通股觀測；新回補差異，待人工核對 |
| 美股 | 0 | — | 未發現含列的疑似休市日 |

報告也列出主要指數涵蓋範圍內、原本就沒有股票列的休市日期（例如 10/26）；這些不計入 `suspectedRows`。普通股樣本數與受影響列數不同，因為後者也包含 ETF／指數。最新分支已補入更多2021年資料：普通股／ETF疑似列仍只有10/23的2,214筆；另有上述1筆指數列，依本次指定的「完全無股票觀測＋缺主要指數」規則列為休市。完整掃描因此是2個日期／2,215列，不能宣稱最新封存只有一個日期。這筆指數資料只列出供核對，不修改封存。沒有將「指數缺列」自行命名為颱風假或新增官方假日。

唯讀重現（報告寫在 archive 外）：

```bash
python -u scripts/scan_market_closures.py \
  --archive-dir /path/to/read-only-kline-archive \
  --out /tmp/market-closure-scan.json
```

每日 workflow 另外傳 `--cache-policy .cache/universe-full/market-closures.json`，保留已確認但比 archive 更新的休市證據。維護者要更新前端歷史種子時，可加 `--frontend-seed atlas/src/market-data/verified-market-closures.json`，審查報告與差異後一起提交；runtime 不修改種子檔。掃描器拒絕任何指向 archive 內的輸出路徑。

## 寫入、分析與前端

- `build_universe_full` 先收完整市場快照，再更新 benchmark／個股。所有 cache 寫入與 fetch 結果排除已判定日期；當日休市快照不進待追加佇列。ETF／指數補充沿用同一證據。
- `kline_archive stage` 以全樣本（包含已封存列）判定，避免只看變動列造成分母偏差。新的休市日列不放入 transport。現存永久列保留；真正價格基準調整也把現存休市列原值帶回，不藉此刪除或修正它。
- full-market breadth、breakouts、monthly 及 market warnings 在計算前投影資料；warnings 的交易日序列也排除該日期。因此不會讓一個假休市列造成之後 21 日家數、領頭羊觀察窗口都失去有效覆蓋。數學公式、成功規則、門檻與前瞻 CSV 的既有內容不變。
- `KlineArchive` 在 raw CSV／SHA 驗證後才過濾。IndexedDB 年檔保留原始內容，圖表投影排除休市日，先過濾日 K 再聚合週／月 K；live 合併不得把同日假列加回。股票單獨零量不再視為市場休市。
- 瀏覽器只讀小報告，每 UTC 日／頁面最多一次、1.5 秒逾時；失敗保留前次有效資料，首次失敗用報告衍生的歷史種子。Yahoo 不可用仍顯示有效封存與正確的最後收盤日期。沒有新增即時報價輪詢。

## 限制

主要指數缺列本身不再足以關閉有正常成交的市場。正量≥5%／50檔的成交證據優先，即使舊metadata曾記為休市也撤銷阻擋；指數缺列另列人工查核。完全沒有股票觀測時仍可依已驗證指數範圍內缺日判定休市。範圍外且未達樣本覆蓋的日期維持未知，不能假裝已證明休市。這個 PR 沒有修復永久 archive，下載原始 CSV 仍會看到原本的 10/23 列；網站與計算只讀時排除。已有 `market-warnings-log.csv` 的前瞻觀察不回寫、不截斷。

測試結果與桌機／390px／375px 圖片見 [REVIEW_PACKAGE](../atlas/REVIEW_PACKAGE.md)。自動化 WebKit 是模擬環境，沒有實體 iPhone 驗證或獨立審核簽章。
