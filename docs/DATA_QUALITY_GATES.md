# 網站資料品質門檻（Data quality gates）

狀態：第一輪。範圍：derived website outputs；不改排程、不改行情/財報永久資料。本文件及各 gate 以 main `3e649600d297c7c063bf6538be606fbb6b1eac8d` 盤點；下表行號為變更前基準，新增 gate 以函式名定位。

## 1. 統一規範

1. 上游 transport success、JSON 可 parse 或有非零 rows 不等於資料完整。對固定市場/全集輸出，按市場、資料期別、有效值（不是 placeholder）及 identity 檢查；能比較時使用 restore/seed 的上一版。不要拿另一市場的增加抵銷消失市場；舊 identity 被新 identity 替代也不算保留。
2. 在寫第一個目標檔前檢查，失敗保留前版 byte-for-byte（包含 generatedAt），非 0 退出；不靜默成功、不以空檔或假的 0/null 填充。使用既有 atomic writer。多檔互相依賴須整組驗完才安裝；不准刪既有有效歷史。正常分市場/逐股 merge 可保留舊值與實際觀測日期，且應明確標出 retained/stale。schema 不明/未發布不等於真實零。冷啟動不虛構前版；有必要用 absolute minimum、關鍵標的或可信名單 coverage。
3. 數值 gate 採業務語意：價格必須有限正值；負 EPS、負成長、負利率可真實發生；虧損本益比應由現有 null/不適用語意處理，不訂任意上限。日期唯一、排序與不回退，對有歷史性的輸出做比較；不要把滾動窗口、ETF/IPO 新增移除、假日、未發布盤後成交、budget resume 當故障。
4. 新 Python 共用 `scripts/output_guard.py`（標準庫）：finite、require、unique_keys、retain_count、retain_keys；只驗證、不寫檔、不呼叫網路。呼叫端維護自己的欄位與門檻，沿用現有寫入機制；原本較嚴的 gate 不得移除。
5. workflow 既有 .github/workflows/notify-failure.yml 與 failure-notice-fallback.yml，hard failure/取消，或由 continue-on-error + step outcome 收集 partial failures。三個新增 gate：data-status.yml:52,58-66、financials.yml:59-67,89-93、fundamentals.yml:128-132,172-176、tw-stock-dividends.yml:61-65,86-90、atlas.yml:128-134,301-321；無須變更排程。注意 data-status 的 capture 預設 success-only，rotation 被拒會暫緩當次 capture，但 status-owned publish 的 always 條件仍保留前版 rotation 並發布已完成 data status；下一次排程可續跑。既有軟失敗吞掉 exit status 的缺口列下。
6. 每個 gate 測試正常更新、空/缺錨點/驟降不變且非0；臨界值與合法負值測試。所有新增測試使用 fixture/mock；不得為 gate 測試下載行情、修改正式 data/ 或 docs/report/。若資料結構有預期大規模變動，先審核新舊數量、原因，再另開 PR 調整門檻；不建議任意 force。

## 2. 盤點方法與範圍

以 .github/generated-paths.json owners 去重後 **42 個路徑模式**（含共用 owner/舊相容圖檔/wildcard），以及 records 中網站可讀的財報、修補/歷史紀錄；另查直接寫 main 的 margin-temperature workflow。依 workflow restore/build/publish chain 逐檔讀取 builder（GitHub code search/Contents API，無本地 clone）。generated-pages.yml:57 組裝 main + generated；scripts/generated_branch.py:192-226,228-313,344-358 負責 restore、owner delta、併發/append-only 與大小限制，不是行情品質 gate。wildcard 按同 producer/schema 家族列，並非逐個股票/日期檔案計數。Atlas static public 資料與 research artifact allowlist 展開；非 active 舊檔不推定為現役。

以下 **50 個邏輯列**（包含展開 wildcard 與 legacy/HTML/狀態），其中原有保護 43 列（P 26、P* 17）、新增本輪 guard 3、無足夠保護待處理 2、診斷/非資料 2。P* 意為已有限制但仍有缺口，不等同安全。下方額外 main records 8 列另計。無法 clone，不能確認 wildcard 下每個現有檔名、非索引 producer、外部部署；MA 外部 main 有讀取但可覆寫 ref，需後續實作時重新確認。

表中未加 docs/ 前綴的主表路徑皆相對 docs/report/，news/screenshot/HTML 明示相對 docs/；workflow 檔名皆在 .github/workflows/。P=已有保護（保留/部分合併或失敗），P*=已有但不全面，G=本輪新增，R=會不安全覆蓋且待處理，D=診斷/靜態特殊語意。每行列的是原始程式在異常時的行為，而非理想規格。

| 輸出 | Producer／workflow（行號） | 狀態 | 上游異常時行為、現有門檻與缺口 |
| --- | --- | --- | --- |
| `market-calendar.json` | market-calendar.yml:28-32 → scripts/build_market_calendar.py:30-54,64-101 | P | 本地 exchange-calendars 建立固定市場、有效區間、開收盤及假日；例外在寫前失敗，原子替換。無遠端行情；尚無 session 筆數門檻。 |
| `data-status.json` | data-status.yml:37-44 → scripts/build_data_status.py:137-175,206-253 | D | 資料來源異常是此輸出的觀測內容；仍寫 missing/stale/錯誤理由與固定 SOURCES 清單。不能拿上次 ok 蓋掉今日故障。GitHub workflow 資訊缺失仍可降級。 |
| `industry-rotation.json` | data-status.yml:45-52 → scripts/build_industry_rotation.py:44-86,94-148 | G | 原本只限制每產業至少 3 個有效流動性成員，空 markets / 無有效報酬仍直接覆蓋。新增按市場/期間檢查主報酬覆蓋、基準產業、日期及有效成員。 |
| `screener-history/*.json` | data-status.yml:53-59 → scripts/build_screener_history.py:33-76 | P* | 同日 first observed wins，不覆蓋已存在快照；空 universe 市場跳過；只保留 10 個交易日/index。新日期的部分資料仍可能固化且推掉舊日期，待獨立 guard。 |
| `latest.json` | schedule.yml → main.py → report_json.py::validate_report | G | 日期不倒退、保留前版市場及有效指數、核心錨點、群組唯一、分市場有效個股與世界指數覆蓋；拒絕保留前版，LINE/Dify/發布不被阻斷，由 run-only 狀態及 notice_soft_04 通知。 |
| `trending-stocks.json` | schedule.yml:118-127 → dify_report.py:55-75,127-131 | P* | 最多 5 個合法、去重代號；無資料保留；1–4 個可發布，需區分真正熱門數量與來源部分失敗，非固定 AAPL 等錨點。 |
| `universe.json` | universe-full.yml:145,250；schedule.yml:113,166 → scripts/build_universe_full.py:58-64,1043-1048,1199-1219,1280-1320；report_universe.py:85-109 | P | 全市場上市/櫃/美股至少 800/500/3000（美股分 Nasdaq/other 2000/1500），名單時效、完整 K 線 >=90%，分市場保留；每日掃描不蓋新鮮 full 市場；generated_branch.py:52-81 併發合併。 |
| `series/**` | schedule.yml:113 → main.py:615-621 → report_universe.py:112-203,204-301 | P* | 空 bars 不寫；index 只列存在檔。短但非空歷史仍能覆蓋，例外只記 log；待加同標的歷史/日期覆蓋。 |
| `themes.json` | schedule.yml:132-137 → scripts/build_themes.py:280-345,573-627 | P | benchmark 歷史長度、最新 constituent 日、fresh coverage >=60%，失敗 1 保留。 |
| `themes-tw.json` | themes-tw.yml:40-55 → scripts/build_themes.py:80-103,280-345,597-627 | P | 同 US builder，使用台股 config/benchmark；同門檻，失敗 1。 |
| `rates.json` | schedule.yml:143-150；refresh-us-rates.yml:70,79-124 → scripts/build_rates.py:235-250,289-298,435-458 | P | FRED CSV/API 驗證、每系列舊值備援；core tenor 必須 fresh，DGS10 <=10 日，失敗 1；非核心允許標記缺值。 |
| `rates-history.json` | 同 rates.json → scripts/build_rates.py:256-280,357-408,435-458 | P* | 依系列舊資料備援、core gate 共用，原子寫。若上游回傳短而新鮮的有效歷史，仍可能縮短；待做每系列日期集合保留，不能假設所有殖利率非負。 |
| `jp-rates.json` | schedule.yml:152-159；refresh-us-rates.yml:72-77 → scripts/build_bond_research.py:52-69,125-150,169-219 | P | MOF schema/非空 observations；合併舊日、不倒退；無新/舊資料回 1。 |
| `yield-band-params.json` | 同上 → scripts/build_bond_research.py:72-94,169-219 | P | 每國至少 120 月、歷史左右邊界不縮短，失敗保留原模型、不重標 fit date。 |
| `growth.json` | financials.yml:59-67；fundamentals.yml:126-132；tw-stock-dividends.yml:61-65 → atlas/scripts/build_growth.py:716-732,776-805,808-875,878-892 | G | 原本只拒絕完全無可讀檔/官方資料；有可讀但無有效 YoY 或大幅減少仍覆蓋，CLI 捕捉不報非 0。新增 per-market 可用 identity、成熟 metric cohort 與 anchors gate；失敗 exit 1。 |
| `fundamentals/tw.json` | fundamentals.yml:74-85 → atlas/scripts/build_fundamentals_tw.py:268-270,393-451,727-831 | P | MOPS/OpenAPI schema 與季別檢查，分季/來源 store 合併、預算/拒絕停止，保留舊季，無成功依 run 結果失敗。 |
| `fundamentals/us-sec.json` | fundamentals.yml:88-92 → atlas/scripts/build_fundamentals_sec.py:81,223-251,567-607 | P* | SEC frame 欄位、CIK/period/value；拒絕空及低於前版 50%（force 例外）；可能仍有大幅但未達50%衰退，保留既有門檻。 |
| `fundamentals/tw-finmind.json` | fundamentals.yml:95-101 → atlas/scripts/build_fundamentals_tw_finmind.py:274-289,942-1067 | P | 讀取 store schema、逐股逐季合併、budget/429/resume/連續失敗；部分更新是預期，不能用整體刷新率當 gate。 |
| `breadth.json` | universe-full.yml:210,250 → scripts/build_full_market_breadth.py:34-74；scripts/build_full_market_breakouts.py:28 | P | TW/US 均須有資料且有效股票/上市名單 >=90%，不足 exit 1，保留。 |
| `breadth-signals.json` | universe-full.yml:214-225 → scripts/breadth_signals/daily.py:138-153,261-304 | P | 校準各指數存在、指數完成日/最小 bars/有效 OHLC、重疊 MA 寬度差 <=3 百分點，失敗 1。 |
| `breadth-us-calibration.json` | breadth-calibration.yml:42-81 → scripts/breadth_signals/calibrate.py:181-219 | P | 先寫 temp 再驗 sample start <=2004-12-31、5 指數各至少50 trades；失敗不 install；S&P 命中率異常有 warning。 |
| `breadth-us-history.csv` | breadth-calibration.yml:42-81；universe-full.yml:214-225 → scripts/breadth_signals/calibrate.py:199-210；daily.py:261-294 | P* | 校準同上、daily 合併歷史並檢查重疊/最新指數日期；仍應補歷史 CSV 日期唯一/不縮水的獨立驗證，跨兩個寫入檔非交易式。 |
| `breakouts.json` | universe-full.yml:74-80,230,250 → scripts/build_full_market_breakouts.py:28,68-75,163-165,225-265；scripts/build_breakouts.py:80,444-471 | P | 全市場有效 >=90%；舊單獨 builder 最少 200 股票，缺市場不寫；週期 gate 不等於缺資料。 |
| `breakouts-history.json` | universe-full.yml:230 → scripts/build_full_market_breakouts.py:177-223,225-254 | P | 由保留的 archive snapshots 重建 index；即使今日 coverage 失敗仍可重建既有索引，不覆蓋歷史觀測。 |
| `breakouts-archive/*.json` | universe-full.yml:230 → scripts/build_full_market_breakouts.py:177-223,247-251；scripts/generated_branch.py:241-243 | P | 合法 ISO date、先通過同一 coverage gate；同日期快照不覆寫，append_only publisher。 |
| `market-warnings.json` | universe-full.yml:232-239 → scripts/build_market_warnings.py:210-227,258,310-373；scripts/market_warnings_params.json | P | 有效標的比率/絕對數、指數最新完成日/代理量、兩市場都缺拒絕；部分指數失敗保留該市場，JSON finite validation。 |
| `breakout-warning.json` | universe-full.yml:241-246 → scripts/build_breakout_warning.py:194-259 | G | 新增 evaluated/failedStocks；每市場失敗率 <=10%、前版有效數 >=85%，gate 拒絕整檔不寫。零訊號合法。 |
| `market-closures.json` | universe-full.yml:124-152 → scripts/scan_market_closures.py:88-144,174-212；scripts/kline_archive.py（CLOSED_SESSION_MIN_SYMBOLS） | P | archive schema/hash/日期/invalidRows 與至少 50 symbols 的 closure evidence、保留 prior evidence；不把少量缺價視為休市。 |
| `ipo-watch.json` | market-extras.yml:31-40 → scripts/build_market_extras.py:38-64,93-129 | G | Nasdaq 三個表均須明確提供 rows（陣列或 null）；空陣列及 null 均合法，缺欄位或錯誤型別拒絕、保留整檔並非0，extended quotes 收集不變。 |
| `extended-quotes/*.json` | market-extras.yml:31-40 → scripts/build_market_extras.py:66-91,130-160 | P | 時間窗口、正且有限價格、時間非未來；單 symbol/session 無較新值、預算、連線失敗保留舊價/時間標 stale。無盤前後成交合法。 |
| `warrants/*.json` | warrant-data.yml:51-57 → scripts/build_warrants.py:93-130,274-288,288-337,343-415 | G | 保留既有 master >=1000、日期及唯一代號 gate；新增各市場有效代號及未到期前版 identity >=85%，未知 mapping 計數。 |
| `tw-chips.json` | tw-chips.yml:42-63 → scripts/build_tw_chips.py:95-99,238-244,402,859-860,919-920,1055-1092 | P* | 解析掉落 >2%、每來源至少50% 名單涵蓋、單來源舊資料保留、payload validate；拒絕時保留但部分錯誤未回非0且 workflow :59 || echo，通知缺口待處理（不放寬 gate）。 |
| `ma-selector/**` | ma-recommendations.yml:65-162 → 外部 wudn9922/ma-selector scripts/build_recommendations.py:1-11,102-118,131-168 | P* | 已唯讀查看外部 main：單股失敗保留檔，缺資料標記、分 shard；本 repo 缺 full 市場時 --no-prune、max-missing 限每次補抓量，非覆蓋保護。若 full 名單被縮短仍 prune；外部 ref 可覆寫，需在外部 repo/發布前另驗。 |
| `screenshots: wsj.png / barrons.png / wsj.jpg / barrons.jpg` | schedule.yml:118-127 → dify_report.py:125；news_screenshots.py:90-155,201-229,419-577 | P* | 截圖取時效 <=36h 的 Wayback 候選、HTTP/內容檢查；單來源失敗不影響其他。這四個是 owner 容許的舊相容名稱，現役發布主要是 hash 檔；未證明每個舊名稱仍每日生成，不能假設未使用就刪。 |
| `news/*.jpg` | schedule.yml:118-127 → news_screenshots.py:273-311,340-377,577-598 | P | 內容 SHA256 命名，重複不送，舊圖不覆蓋，30 日保留為正常生命週期。 |
| `news/state.json` | schedule.yml:118-127 → news_screenshots.py:288-320,577-598 | D | 記錄來源 hash/最後成功、失敗累計；失敗本身是狀態，不能凍結；保留同來源既有欄位。 |
| `docs/index.html` | atlas.yml:205-232；schedule.yml:166（daily owner 允許同路徑）；scripts/generated_branch.py:306-313,344-358 | P | 由同一 Atlas dist-pages/index.html 複製；build 失敗/缺 index hard fail。daily owner 允許不代表每天重建此檔。 |
| `docs/atlas/** — 靜態站與 bundled public 資料（不含下列動態資料）` | atlas.yml:174-177,205-232 | P* | npm build 成功且 index 存在才整站替換；若 directory/valuation 檔缺失只 warning（:212-215），待加保留/強制驗證舊版存在的必要 public 檔。 |
| `docs/atlas/symbols/directory.json` | atlas.yml:75-94 → atlas/scripts/build-directory.ts:33-59,62-135 | P* | TWSE700/TPEx500/US3000/Nasdaq2000/other1500；逐來源舊版/內建名單備援，美股任一交易所缺直接拒絕；TW 任一無可用仍可能部分目錄，待確認。 |
| `docs/atlas/valuation/us.json` | atlas.yml:105-109 → atlas/scripts/build_valuation_us.py:43-46,233-237,278-322,414-450 | P | 至少1000目錄/報價，前版至少85%筆數，連續錯誤上限8；原子寫，失敗 1。 |
| `docs/atlas/valuation/tw.json` | atlas.yml:138-140 → atlas/scripts/build-valuation.ts:109-135,176-215 | P* | 每交易所至少500，失敗保留該交易所舊 rows、非0；價格/EPS optional，成功但部分 rows/欄位大減仍可更新，待加相對覆蓋。 |
| `docs/atlas/valuation/tw-annual.json` | atlas.yml:128-134 → atlas/scripts/build_valuation_tw_annual.py:41,59-74,83-85,95-154 | G | 原本目錄最低500，依 listed prune 舊 items，合法但縮短的目錄仍可覆蓋；新增有限 EPS、前版 identity/anchors guard。保留逐股 merge 與 budget resume。 |
| `docs/atlas/valuation/us-growth.json` | atlas.yml:115-121 → atlas/scripts/build_fundamentals_us.py:142-177,283-357,381-420 | P* | 每股按季度合併舊值/重抓 stale，連續失敗 8；依 listed prune，舊資料全域縮水仍可能成功，待加相對門檻。 |
| `docs/atlas/valuation/us-events.json` | atlas.yml:146-150 → atlas/scripts/build_events_us.py:39-44,205-240 | P | quote coverage >=60%、有 earnings timestamp 的 equity >=50%、非空 dates，失敗 1；日期改期/無事件正常。 |
| `docs/atlas/valuation/pe-river-{tw,us}.json` | atlas.yml:156-168 → atlas/scripts/build-pe-river.ts:43-67,75-119 | P* | 每市場零成功保留、>50%讀錯回非0，但可能先寫少量可算 symbols，未看前版 coverage；待 per-market guard，不能擋本益比不適用/虧損公司。 |
| `research/institutions.json + institutions/{TW,TWO}.json + turnover/{TW,TWO}.json` | research-data.yml:80-82,134-156,200-205 → scripts/build_research.py:84-109,193-309；scripts/research_publish.py:10-22,68-102,123-143 | P | 每日期 official coverage >=50%，日與市場 shards 累積、來源狀態、artifact allowlist/hash 驗證；部分預算續跑可正常。 |
| `research/volume.json + volume/{TW,US}.json` | research-data.yml:143-156,200-205 → scripts/build_research.py:312-427；research_publish.py:18,93-102 | P* | 使用本地完成日 CSV / prior MarketStore，缺 path/stats 不刪舊內容，標 stale；無新版 coverage 下降門檻，待分辨 store 與 universe 差異。 |
| `research/alerts.json` | research-data.yml:144,156,200-205 → scripts/build_research.py:430-506；research_publish.py:20,68-102 | P | 既有事件依 id 合併，缺日期拒絕來源、保留 prior；400 日窗口，disposal 不自動視為解除；零新事件合理。 |
| `research/sec-radar.json + sec/US.json` | research-data.yml:152-156,200-205 → scripts/build_sec_radar.py:300-316,408-510；research_publish.py:19,93-102 | P | 缺 UA 保留；SEC 日期/CIK/schema、previous event IDs、checkedSymbols、source stale/unavailable；只宣稱已查範圍，預算/零公告合法。 |
| `research/world-assets.json + candles/*.json` | research-data.yml:145,156,200-205 → scripts/build_world_assets.py:221-232；scripts/research_candles.py:37-96,121-154,160-241；research_publish.py:25-45,74-91,123-143 | P | symbol/currency/type、完整 OHLC、完成日、保留歷史日期，逐資產失敗留舊資料；artifact checksum 與不可刪 retained CSV 日期。 |

### 2.1 main records 與額外發布

| 輸出 | Producer／workflow（行號） | 狀態 | 現況 |
| --- | --- | --- | --- |
| `docs/report/financials/{symbol}.json` | financials.yml:55-67；fundamentals.yml:105-132；q4-8k-backfill.yml:95-123；tw-stock-dividends.yml:56-65 → atlas/scripts/build_financials.py:377-415,1847-1897,1943-1985；build_financials_tw_finmind.py:554-569,710-892；build_stock_dividends_tw_finmind.py:37,65-73,93 起 | P* | 永久 records 也由 Pages 讀。逐股歷史欄位/period/schema、舊值合併、單股失敗保留；FinMind resume、配股至少5個不同日；不刪歷史；仍應獨立稽核每欄位 coverage 與 migration 特例。 |
| `docs/report/financials/index.json` | financials.yml:55-67 → atlas/scripts/build_financials.py:1897 起 | P* | 由已存在財報檔彙總；需獨立檢查 index 與實際 symbol file set 一致、不可從部分目錄重建掉落。 |
| `docs/report/fundamentals/tw-financials-finmind.json` | fundamentals.yml:105-112 → atlas/scripts/build_financials_tw_finmind.py:569,710-779,873-892 | P | 狀態 schema/續跑，預算限制是正常，非公開財報全量快照。 |
| `docs/report/kline-corrections.json` | schedule.yml / research-data.yml / universe-full.yml；scripts/generated_branch.py:228-294；kline_integrity.py / scripts/kline_journal.py | P | 永久修補紀錄/併發合併，不是普通快照；不得用筆數比例允許刪歷史。 |
| `docs/report/market-warnings-log.csv` | universe-full.yml:235-257 → scripts/build_market_warnings.py:365-369；scripts/generated_branch.py:228-237 | P | 先驗 payload 才 append，publisher 拒絕既有 forward observations 變更；workflow verify-against HEAD。 |
| `docs/report/margin-temperature.json` | margin-temperature.yml:28-48 → scripts/margin_temperature.py:102-125,158-178,186-278,285-319 | P | 不在 generated owners，直接 commit main；TWSE schema/OHLC/融資合理性、兩資料同日一致、不完整日拒絕、追補最多40平日、無 rows 拒絕。 |
| `data/*.csv、data/world/*.csv、kline-archive` | schedule.yml / research-data.yml / universe-full.yml / kline-archive 系列；scripts/generated_branch.py:228-243；research_candles.py:121-154；scripts/kline_archive.py、kline_integrity.py | P | 永久 K 線 records/archive，不是可覆蓋 derived JSON；完整日期/價格/稽核/closure evidence、不得刪留存日期；非本輪改動。 |
| `docs/.nojekyll；tests/fixtures/rates/*.json、atlas/tests/fixtures/rates/*.json` | atlas.yml:221-225；refresh-us-rates.yml:57-65；.github/generated-paths.json records | D | Pages 配置與測試 fixture，不是上游網站資料快照；不另套市場門檻。 |

## 3. 第一輪新增三個 guard

| 輸出 | 驗證與門檻 | 理由及例外 |
| --- | --- | --- |
| latest.json | `report_json.py::validate_report`：合法且不倒退 reportDate；前版市場及其正有限指數保留；TW ^TWII / US ^GSPC 必須有效；群組 key 唯一，每市場前版 >=20 有限 close 個股時保持 >=85% 去重 symbol 數；世界指數前版 >=5 時保持 >=85% 有限 close symbol 數；sectors 不設門檻 | generated 703af090（2026-11-27）：5 市場 tw/jp/kr/eu/us、12 個有效市場指數、9 群組、TW 17 unique/17 rows（不套比例）、US 174 unique/179 rows（最低148）、worldIndices 19（最低17）。群組增減/空群合法；失敗保留舊 bytes，LINE 照常，發布後由 notice_soft_04 通知。 |
| growth.json | `atlas/scripts/build_growth.py::validate_output/usable_growth`：至少一個可用 row；對前版有資料的每市場保持 >=85% 可用 symbols 及前版 identity；原先可用的 2330.TW/AAPL/MSFT 不可消失；對每市場 EPS/營收 Q/Y cohort，前版至少100個可用 symbols 才套85%；有限 YoY/合法 turnaround + 可解析 period end，不是單純 rows；重複 symbol 拒絕 | 15% 比例沿用 us valuation 的既有 MAX_SHRINK_RATIO，較 5% 寬鬆，避免公司除牌、財報過期、正常來源切換阻擋。既有 tests/test_growth.py:502-557 證明目標為全市場（含實際財報與模擬全市場官方資料）；generated growth.json 超過 Contents API 可讀限制，改由 2026-11-27 正式 [financials run](https://github.com/wudn9922/my-stock-screener/actions/runs/45974671256) 與 [fundamentals run](https://github.com/wudn9922/my-stock-screener/actions/runs/45974827729) 日誌確認 6841 rows（TW 1987、US 4854）；最新期 EPS Q/Y 與營收 Q/Y 覆蓋 TW 1645/1650/1950/1982、US 2815/2912/3860/4346。這些是日誌的 leading >=1 counts，而 gate 計算任一有限歷史值，不是相同分母；實際門檻仍由每次前版檔動態計算。100 個 cohort floor 防小樣本稀疏欄位鎖死。非正成長合法；不要求 SPY ETF 有公司財報。 |
| industry-rotation.json | `scripts/build_industry_rotation.py::validate_output`：非空、不能失去舊市場或倒退 asOf；每市場每期間 d1/w1/m1/m3 有至少一個有限 median，ranked sector identities 與有效成員數各 >=75%；前版已 ranked 的 TW 半導體業/金融保險業、US Technology/Finance 不可消失；重複 sector 拒絕 | 2026-11-27 generated 檔：TW 34 groups/31 ranked/833–835 valid；US 13 groups/13 ranked/2669–2678 valid。流動性與季節性正常變化較大，允許25%下降，仍能擋整市場/主要產業消失與大量 null。TW m1/m3 的 paired/volume count 實際為0，短期 cache 未齊或未知量是合法；不以 optional 量/動能缺值擋主報酬更新。錨點只對前版 ranked 群有效，不硬要求個股一定在流動性排名。 |
| docs/atlas/valuation/tw-annual.json | `atlas/scripts/build_valuation_tw_annual.py::validate_output`：保留既有500 directory minimum，唯一 directory IDs；至少一個 finite EPS（負/0合法），可用 identities >=85% 且 prior retained >=85%；前版可用 2330.TW/2317.TW/2454.TW 必須保留 | generated 檔實際 1987 records/1977 finite EPS，三個錨點均有值；500 floor 無法攔腰斬而 15% 允許正常退市/改名、逐批更新；以舊可用 EPS 為準，不把新上市/未出財報的 null 當缺陷。cold start 沒舊檔只需有可用值且目錄 >=500，後續續跑逐步累積。 |
| ipo-watch.json | US Nasdaq filed/upcoming/priced 必須明確提供 rows（陣列或 null），空陣列與 null 都是合法空表；缺 section、非物件 section、缺 rows 或錯誤型別拒絕；拒絕保留 byte-for-byte 並非0 | generated 703af090：2026-11-28 共26筆、Nasdaq 25筆；月初不設筆數。失敗 reason 寫 log，避免改動前版 generatedAt。 |
| breakout-warning.json | 每市場 failedStocks/ stocks <=10%，evaluated >=85% 前版（舊 schema 用 stocks - failedStocks）；至少一個成功，零訊號合法 | generated 703af090：TW 1989/0 失敗，US 5499/0 失敗，門檻 1691/4675；避免少數成功覆蓋全市場。任一新 gate 拒絕整份檔案不寫。 |
| warrants/*.json | master 每市場 >=1000 不變；mapped 有效代號數及前版未到期 identity >=85%，跨標的唯一；過期不計入前版 | generated 703af090：883 個標的檔案（13,246,393 bytes）；審查者唯讀統計未到期代號：TWSE 41,205、TPEx 12,716，均無重複；85% 最低分別 35,025 / 10,809（41,205 × 0.85 = 35,024.25，整數向上取整）。執行時由 restored 檔計算，不使用硬編碼。 |

基準數據只讀取 generated branch，不寫正式資料；沒有連 Yahoo/SEC/交易所。以上 anchors 是「原先可用」的條件，避免冷啟動/小 fixture 必須包含全市場；如果錨點真實退市/分類改名，需要人工確認後另行調整，不能自動放寬。前版讀不到時沒有相對基準；本輪不建立額外 state。應在下一階段驗證 restore/seed baseline 可用與歷史大規模遷移策略。缺欄位/局部少數異常未達門檻可正常更新；本輪不宣稱每一格財報或任意來源完全正確。

### 3.1 待處理（後續 PR，每次最多六個輸出）

- P0：warrants（近期活躍代號、不同市場有效 coverage 與 mapping，允許到期/分批，不刪有效舊標的）；pe-river-tw/us（archive/financials 讀取成功、有效 identity，先驗後寫）；IPO calendar（分月 schema、空表與缺表區別、同月 drop）；breakout-warning（實際 evaluated/failedStocks、method rows，正常沒有 signal 不等於資料缺）。其餘缺口未修改：權證 mapping 及跨語言/多檔流程需要各自 fixture、數據與 failure contract，避免把本輪變成過大的變更。
- P1：series 歷史日保留，screener-history 新快照的 universe coverage，Atlas directory/TW valuation/us-growth 相對掉落及缺必需檔、rates-history 日期覆蓋；MA 外部 repo prune 前確認全集 coverage；research volume/store 及財報 index/逐股欄位規則。補 audit 部署後資料 baseline 缺失的通知；有 guard 但吞 exit 的 main.py/chips 等另處理；不得以放鬆 existing gate 解決告警。
- P2：在確認正常來源變動分布後，逐資料評估合理範圍、歷史唯一日期/欄位完整、publication transaction；對 data-status、合法 zero event、分股/逐日 resume 的 outputs 不使用一刀切的通用筆數。

## 4. 測試與發布

`tests/test_output_guard.py`：共用 module finite/duplicate/retention/anchor/boundary；growth 實際 temp 財報 CLI、annual mock Yahoo/parse/time.sleep、rotation temp universe/mock read_bars；正常更新、空資料、缺錨點、驟降及 null/identity replacement；拒絕時 byte-for-byte 舊檔保留，exit 1。完整 repo tests 由現有 research-review.yml（scripts/**、tests/**、atlas/scripts/**）的 unittest/pytest 及 atlas-review.yml 跑；沒有修改 CI、依賴或排程。CI 舊工作自身可能有原本的網路/唯讀校驗，本輪新增的測試不連網。

本地不能 clone：以 connector 讀檔與 remote compare 確認 diff，完整 integration tests 以 PR CI 為準；本地逐字複製 shared module 並比對 git blob SHA，兩項單元測試通過。PR 不提交 data/、docs/report/、任何正式網站資料或測試 verification JSON。合併後第一次各現有排程檢查 Step Summary/通知是否出現 OutputRejected，若正常資料被拒先保留舊檔，再用實際 market/metric coverage 研判，不重跑 force 或改歷史。
