# 資料狀態與比較功能

本次由 main `fe7b39f50348b55241056ccd373b4feab2530a04` 開分支。只新增讀取、分析及 generated 衍生檔；不改登入／帳號區塊、網站外框、Supabase functions 或永久價格／財務／K 線資料，不發通知，不部署。

## 資料狀態

入口：**我的 → 資料狀態**，與「均線參數」「顯示與App」同一組。前端只請求 `report/data-status.json`，不下載全市場檔來算日期。每列點開看原因、應更新日及交易日落後數；有時間戳者以台灣時間顯示；來源僅提供日期時標示「僅日期」，不捏造時分。讀不到檔案時有錯誤與重試，其他分頁仍可用。

`scripts/build_data_status.py` 的 `SOURCES` 是唯一門檻表，包含中文名稱、市場、檔案、日期路徑、正常延遲、過期門檻、更新類型、交易日曆、收盤後發布寬限及產生時間路徑。狀態：落後不超過正常門檻為正常；超過正常門檻但未達過期門檻為延遲；達過期門檻為過期；檔案、日期或日曆不能判讀為缺少。每個來源獨立處理，不因其中一個缺檔中斷整份報告。

| 來源 | 日期欄位（相對 docs/report/） | 類型 | 正常／過期（交易日） | 日曆／收盤後寬限 |
| --- | --- | --- | --- | --- |
| 每日報告 | latest.json → reportDate；generatedAt 另列 | daily | 0／3 | XTAI／120 分鐘 |
| 全市場台股 | universe.json → markets.TW.asOf；含 counts.stale/missing | daily | 0／3 | XTAI／120 分鐘 |
| 全市場美股 | universe.json → markets.US.asOf；含 counts.stale/missing | daily | 0／3 | XNYS／360 分鐘 |
| 台股突破 | breakouts.json → markets.tw.asOf；舊格式才用 asOf | daily | 0／3 | XTAI／120 分鐘 |
| 美股突破 | breakouts.json → markets.us.asOf；舊格式才用 asOf | daily | 0／3 | XNYS／360 分鐘 |
| 台股市場寬度 | breadth.json → markets.tw.asOf；舊格式才用 generatedAt | daily | 0／3 | XTAI／120 分鐘 |
| 美股市場寬度 | breadth.json → markets.us.asOf；舊格式才用 generatedAt | daily | 0／3 | XNYS／360 分鐘 |
| 美股題材 | themes.json → asOf | daily | 0／3 | XNYS／360 分鐘 |
| 台股題材 | themes-tw.json → asOf | daily | 0／3 | XTAI／120 分鐘 |
| 利率（FRED） | rates.json → asOf | daily | 3／6 | XNYS／360 分鐘 |
| 台股籌碼 | tw-chips.json → generatedAt | weekly | 5／10 | XTAI／120 分鐘 |
| 成長財務彙整 | growth.json → generatedAt | daily | 1／3 | XTAI／120 分鐘 |
| 美股財報（SEC） | fundamentals/us-sec.json → asOf | weekly | 5／10 | XNYS／360 分鐘 |
| 台股財報（MOPS） | fundamentals/tw.json → 最新季別的 fetch.*.at，以該季最早擷取日判定 | quarterly | 65／85 | XTAI／120 分鐘 |
| 台股財報（FinMind） | fundamentals/tw-finmind.json → fetch.*[0]，以涵蓋個股最早擷取日判定 | quarterly | 65／85 | XTAI／120 分鐘 |
| 熱門股 | trending-stocks.json → fetchedAt | daily | 0／3 | XNYS／360 分鐘 |
| 台股 K 線封存 | kline-archive:manifest.json → markets.tw.lastDate | daily | 0／3 | XTAI／120 分鐘 |
| 美股 K 線封存 | kline-archive:manifest.json → markets.us.lastDate | daily | 0／3 | XNYS／360 分鐘 |

使用與 `breakout_alert/market_clock.py` 相同的 `exchange_calendars` 交易所日曆，指定 XTAI／XNYS，不用週一至週五近似；早收盤、休市、夏令時間均依日曆。應更新日＝已收盤且已過發布寬限的最近交易日；落後數＝資料日之後至應更新日之間的交易日數。美股 360 分鐘寬限會跨至台灣次日 10:00（冬令 11:00）；台股為 15:30。這是保守的資料發布政策，不宣稱來源有即時服務保證。

注意監控口徑：這是**已發布衍生檔**的新鮮度；不重新向 TWSE STOCK_DAY_ALL、Yahoo 或財報 API 抓資料，也不保證備援成功後能得知原始端點本身是否卡住。時間戳來源只能檢查擷取／彙整是否新鮮，不能驗證其中每項內容；季度來源不推論財報是否已到申報期限。封存檢查市場最大日期，不代表每檔均已補齊。利率以 XNYS 作美國工作日近似基準，配合三交易日寬限，不宣稱涵蓋每個全球市場或 FRED 系列發布日曆。

Actions 紀錄讀取 schedule.yml、universe-full.yml、financials.yml、fundamentals.yml、tw-chips.yml、themes-tw.yml、research-data.yml、refresh-us-rates.yml 最新 main 執行，包含排隊／執行中／完成結果及連結。API 失敗只在該流程顯示無法取得，token 不寫入報告。

`.github/workflows/data-status.yml` 台北 08:35／18:35／23:35，以及每日報告／全市場 workflow 完成後執行，成功與失敗都觸發。使用固定 main 程式，不執行觸發者的程式或 artifact。權限只有 contents:write、actions:read；owner `status` 只可發布本功能的三類新輸出。沿用 `generated_branch.py` 的最新 tip、owner delta、非 force 有限重試，其他 owner 的輸出不會被覆蓋。自己的 concurrency 不取消正在發布者；額外比較建置失敗時仍可發布成功產生的狀態檔。沒有 archive checkout 或 archive push，僅透過 API 讀 manifest 與其最後提交時間。

## 個股與大盤強弱比較

入口：**個股 → 研究 → 相對大盤**。台股可選加權／櫃買；美股可選 SPY／NASDAQ／標普 500 指數。可切 1、3、6 個月與 1 年，使用共用 provider 的日 K 及來源資訊。雙方各依所屬交易時區取得交易日，再取交集；不按陣列位置配對、不補值。共同起點歸零，超額報酬為兩條價格報酬的百分點差，不含股息；不足期間明示實際天數。圖表使用現有 Lightweight Charts 5.2.1 TimeChart，保留拖曳／縮放，不修改主 K 線、均線、畫線或其設定。

## 選股結果變化

入口：**選股 → 選股結果變化**，預設收合，展開才載入日期索引及選取的兩份快照。使用同一群組、搜尋、狀態與篩選條件；顯示新進入、持續符合、已離開、觀測不足，並列出未通過的條件。條件沒有完整的歷史輸入時明確停止比較，不悄悄省略條件。每次只顯示 50 筆，可繼續展開。

`scripts/build_screener_history.py` 在新 workflow 保存 `report/screener-history/{TW|US}-YYYY-MM-DD.json` 及 `index.json`，包括公開 universe、原報告群組、財務成長與估值輸入。兩市場各使用自己的資料日，首次保存後不覆寫、不刪除。固定均線以目前既有設定分別對齊兩日的 K 線；額外計算的歷史 K 線收盤必須與凍結快照同價格口徑。分割／還原造成不一致或歷史不足時停止比較，不改用 EMA20 或推定離開；不改帳號或伺服器設定。

**不回填虛構歷史**：新 workflow 上線後才開始累積；至少兩個資料日才有可比日期。這些是擷取時保存的快照，不是精確還原歷史收盤當刻的資訊集，財報可能在收盤後才收集。來源不在名單或沒有當日有效觀測者另列觀測不足，不推定為條件離開；不使用今天的財務檔替代缺少的歷史財務。熱門股獨立來源不套用這個 universe 歷史比較。

## 產業輪動比較

入口：**市場 → 題材 → 產業輪動比較**，保留原題材清單、熱力圖與題材輪動追蹤。依目前官方台股產業／美股 sector 分組，可選 1 日、1 週、1 月、3 月，以報酬、上漲比例或量比排序。

`scripts/build_industry_rotation.py` 唯讀 universe 與還原的 universe-full cache，寫新檔 `report/industry-rotation.json`。cache 只 restore，沒有 save。價格報酬採 universe 同資料日有效股票的等權平均；量比採前後兩個等長區間、同一完整樣本的股數總和比。交易日由日曆對齊，不把缺少日、未知成交量或停牌當零。動能變化採兩期完整行情共同樣本；顯示報酬與量能有效檔數。目前成員分類有存續偏差，量比不等於資金淨流入；沒有快取時仍提供可用報酬，量能保持未知。

## 驗證

- Python：交易所休市、週末、早收盤、夏令時間、發布寬限、一／三交易日延遲、FRED 寬限、真實 fetch 結構、缺欄位／缺檔／壞檔、API 失敗隔離、來源唯讀、歷史不覆寫、兩期共同量能與未知量排除。
- Vitest：狀態解析／分組／小檔請求，日期對齊與超額報酬，歷史輸入驗證／同條件進出／缺資料，產業排序。
- Playwright：桌機、390px Chromium／iPhone WebKit，四種狀態與正常摘要、台灣時間、展開原因、API 連結、錯誤／重試、相對大盤切換、兩日名單比較、產業期間與排序；另跑 nav-inventory、console、選股、題材、個股分頁回歸。
- 以 generated 與 archive 真實資料唯讀解析；狀態檔含八條 workflow 紀錄約 10 KB。測試輸出只寫儲存庫以外的臨時副本，沒有寫永久來源。

實際通過數記錄於 PR 與 REVIEW_PACKAGE.md；無獨立外部審查簽核，無真機操作宣稱。
