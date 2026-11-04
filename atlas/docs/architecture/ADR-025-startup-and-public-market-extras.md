# ADR-025：初始 HTML 與唯讀行情補充快照

採用日期：2026-11-04

初始 HTML 直接提供歡迎內容，獨立 startup.js 僅處理公共程式載入狀態；不讀網址／帳號／storage，不改帳號初始化順序。現有 React root 掛載後自然取代歡迎內容，無新增動畫等待或自動重載。worker 的版本涵蓋 startup.js，獨立 drawing lab 可快取它；screener 維持既有 memory-only 政策。

補充資料仍在 MarketDataProvider 邊界內解析。Quote 的可選 extended 欄位與 BarResult 的 missingVolumeAt 可向後相容；正規交易報價與圖表 OHLC 不被延長時段覆蓋。缺量不能在同期量比中當作零量。延長時段補充欄位為空或無效時只忽略該欄，不能否決有效的正常交易資料。原有繪圖 engine、資料鍵與時間價格錨點不變。

不為盤前盤後或 IPO 變更 Supabase。新增免費公開資料收集器與有限覆蓋的 generated snapshots；前端只讀個別小檔及公開申報資料底稿，保留真實成交／核對時間、來源與不可用原因。收集器 owner 不含任何永久歷史或帳號資料。完整操作與限制見 [說明](../STARTUP_WATCHLIST_MARKET_DETAILS.md)。
