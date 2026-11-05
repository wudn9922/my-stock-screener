# ADR-026 — 永久封存優先與共用即時報價

狀態：使用者授權的 PR-C。沿用單一 MarketDataProvider、既有 IndexedDB 行情快取與 LiveQuoteFeed，不新增行情伺服器、通知、排程或持久化個資模型。

`KlineArchive` 直接驗證公開的個股 index v1（代號、bucket、日期、編碼路徑與年度明細），不下載 manifest，再平行讀需要的年度 CSV。index／404 存進既有 IndexedDB，按 UTC 日更新；沒有 index 就走原資料來源。每個年檔驗 SHA-256，舊年長期快取，今年以日期／SHA 更新。快取 identity 含年份與內容 SHA，修正後不重用舊 blob。所有資料仍通過 usableBarResult／quarantine；未知成交量有獨立標記，原始單位維持股，顯示端照原有規則換台股張數。

`ArchiveFirstProvider` 在可取得封存時直接回傳日 K，週／月 K 依原 calendarPeriodStart 聚合。App 在背景接更早的 Yahoo 原生週／月資料；本益比歷史讀取也保留較早資料的補充。公司事件背景使用含 events 的十年月 K payload，不再為事件下載五年日 K。世界資產保留原有路由；無封存的代號走既有 Yahoo／官方序列。

LiveQuoteFeed 是今天與缺少日期的唯一新增訂閱來源。同日以市場日期對齊，封存日期值優先，未封存的形成中 K 棒可以持續更新。重疊 OHLC 超過 2% 時不拼接兩種調整基準，先顯示 Yahoo 片段，再改讀完整 Yahoo；較早週／月的已完成期也比對收盤基準。網路、逾時、驗證或公司事件失敗不移除已取得的封存。手機另直接顯示封存截止日，既有更新按鈕透過同一批次 feed 重試；分 K 失敗可切日 K。

自選走勢讀六個月、最多 126 根與兩個年度檔。缺檔、讀取失敗或分割失配才回既有資料路由；日漲跌仍比較最後兩個交易日。側邊清單只訂閱真正可見列。小圖只在可見時訂閱五分鐘更新；同代號取活躍訂閱的最短間隔。有限的暖快取避免捲回時重抓，離開即取消訂閱。長均線的 warm-up 與市場小圖原有縮放歷史保留，連續檢視仍預設六個月。

額外舊離線回歸在未改前端的基準與本 PR 都發現相同問題：正常繪圖實驗室的 worker 只快取 HTML 的直接資源，遺漏懶載入的 ChartPage。建置插件現在把公開 JS/CSS chunk 清單寫進非選股站點的 shell 預載，恢復原 Demo 離線旅程。選股站點的 SCREENER_MEMORY_ONLY／worker 退役策略不變；不快取帳戶 API、報告、登入交接頁或個資。

此變更只讀 archive/main/generated；沒有對永久行情／財報或遠端封存寫入。PR-B 正式補檔由維護者執行，實際覆蓋依 per-symbol index，不假定已完成五年補檔。[驗證與截圖](../PR_C_ARCHIVE_FIRST.md)。
