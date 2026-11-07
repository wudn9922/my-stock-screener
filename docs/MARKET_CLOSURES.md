# 全市場休市與交易日資料缺漏

## 最新修訂：交易日曆與官方資料優先（2026-11-07）

本節取代下方歷史判定。永久封存只讀；不補造、不刪除、不覆寫任何K線，不觸發正式排程或通知。

共用 `scripts/market_sessions.py` 先查XTAI／XNYS：

1. 保留原正量≥5%或至少50檔的成交證據優先規則。指數缺列保留原 `primary-index-gap-with-trading`，另列 `missing-session-data`。
2. 日曆不是交易日，可記為 `exchange-calendar-closed`；台股10/26無股票觀測，結果不變。
3. 日曆是交易日，只有「主要指數缺列＋有效普通股≥95%已知零量平盤」才能判臨時休市；已驗證官方當日資料會阻止此推論。原樣本數、近期覆蓋與未知量規則不變。10/23具備兩項證據。
4. 其餘交易日缺資料，記為 `missing-session-data`，保留現存列。2021-04-06只有官方^TWOII、沒有^TWII與普通股，不能再判休市。日曆不可用時不產生新的工作日休市推論。

主要／官方指數先驗per-symbol index、SHA-256、bytes、rows與日期。官方來源限具明確TPEx provenance的^TWOII，Yahoo不冒認官方。新metadata增加 `calendar`／`calendarSession` 與 `missingSessions`；舊的日曆交易日index-only休市標記會撤銷。投影lineage保留少量完整臨時休市證據，讓下游已移除假列後仍保留正確10/23判定。

前端嚴格驗證日曆及臨時休市的兩項證據，無證據的舊policy回退至新版已驗證種子；`missingSessions`優先撤銷舊休市標記。2021-04-06已從種子移出。IndexedDB raw年檔不變，完整行情投影cache升到v5，恢復舊投影隱藏的資料；沒有新增輪詢。

## 本次兩份唯讀報告

重跑時間 `2026-11-07T03:29:16.986973+00:00`，固定commit `62bd86a6e7fcde3572c56aa607eed5018a0cfe68`：7,689代號、41,507年檔、9,780,006列、0無效OHLC，103.851秒。只有TW2026-10-23含2,214筆疑似休市占位列；2021-04-06已從休市清單移出。archive掃描前後git status乾淨。

| 市場／日期 | 可觀察股票 | 有列 | 股票缺列檔數 | 佐證／分類 |
| --- | ---: | ---: | ---: | --- |
| TW 2021-04-06 | 1,767 | 0 | 1,767 | TPEx ^TWOII當日OHLC |
| TW 2022-02-04 | 1,805 | 0 | 1,805 | 待核對臨時休市／日曆差異 |
| TW 2023-01-18 | 1,846 | 0 | 1,846 | 待核對臨時休市／日曆差異 |
| TW 2024-10-31 | 1,938 | 0 | 1,938 | 待核對臨時休市／日曆差異 |
| TW 2025-08-01 | 1,969 | 3 | 1,966 | TPEx ^TWOII當日OHLC |
| TW 2026-07-10 | 1,982 | 0 | 1,982 | 待核對臨時休市／日曆差異 |
| US 2026-11-06 | 5,373 | 0 | 5,373 | 最新收盤日尚未封存（尾端），非既有範圍內缺口 |

每個代號每年一個CSV，表中的missingSymbols也是該日受影響的普通股年檔數；主要指數是否缺列另外列於JSON。跨日相同年檔可能重複計數，彙總不是互異檔案總數。台股6個歷史疑點合計11,304筆股票缺列；美股既有覆蓋範圍內0日，最新11/6另列1個尾端待封存日。

[休市掃描](research-verification/market-closure-scan-20261106.json)、[資料缺漏掃描](research-verification/market-data-gaps-20261106.json)。保留原審查檔名，generatedAt為實際重跑時間；來源commit固定在報告中，不宣稱之後live分支相同。

缺漏掃描檢查2021起已收盤的XTAI／XNYS session：主要指數無列，或>50%的可觀察普通股無有效列，就列出日期、檔數、比例及官方OHLC佐證。分母為封存 firstDate≤該日的普通股；firstDate是資料覆蓋界線，不是已驗證IPO日。未推定下市日，結束更新的股票仍可能算在缺列內，現行目錄也有存活者偏差。

`coveragePosition`區分既有主要指數範圍內缺口，與最新收盤日尚未進入封存的尾端；後者可能只是排程尚未完成。已確認休市不列為待補資料。**這是缺漏疑點清單**：無官方交易佐證的全空日期可能涉及臨時休市或日曆誤差，保持待查，不自動刪日期或補檔。2021-04-06與2025-08-01有TPEx當日OHLC；其餘歷史疑點沒有同等佐證。

一次讀CSV產生兩份報告：

```sh
python -u scripts/scan_market_closures.py \
  --archive-dir /path/to/read-only-kline-archive \
  --out /tmp/market-closure-scan.json \
  --missing-out /tmp/market-data-gaps.json
```

獨立缺漏掃描入口：

```sh
python scripts/scan_market_data_gaps.py \
  --archive-dir /path/to/read-only-kline-archive \
  --out /tmp/market-data-gaps.json
```

兩個入口都拒絕輸出到archive內。原 `--cache-policy` 與 `--frontend-seed` 選項保留，只寫衍生metadata。每日寫入、bootstrap、ETF／指數補充、stage與研究共用判定；永久日期與價格、原追加／衝突／拆股稽核、成功口徑、研究門檻、前瞻CSV不變。只有primary self-review；測試與圖片見 [REVIEW_PACKAGE](../atlas/REVIEW_PACKAGE.md)。

<details>
<summary>前一版修訂紀錄（已由上方新規則取代，舊掃描結果供追溯）</summary>

## 舊版：全市場休市日判定、掃描與資料保留

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

</details>
