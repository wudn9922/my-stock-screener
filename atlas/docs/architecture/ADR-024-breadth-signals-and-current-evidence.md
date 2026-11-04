# ADR-024：衍生寬度訊號、週期固定與清單條件證據

Status: accepted for implementation; deployment remains maintainer-controlled.

## Context

各週期圖表原已具備 symbol + timeframe 的獨立物件所有權，但主要推薦入口偏向日 K。市場寬度需要長歷史校準界線；清單名單快照沒有先前的指標數值，無法可靠重建過去發生的技術事件。

## Decision

沿用既有 v4 圖表儲存、canonical time/price 畫線與 AppStore 鎖定 API，僅提供目前週期的編輯／固定入口。日 K 雲端監控參數仍獨立，沒有跨週期轉用、遷移或新增 Supabase schema。

將使用者附件的寬度規則放在純 Python 模組；校準／長歷史／每日訊號是新增衍生檔，既有原始寬度與永久價格資料保持唯讀。訊號工作失敗不影響既有寬度產生／發布。generated owner 與 workflow concurrency 共用 universe，避免兩個生產者同時重寫長歷史。前端的訊號檔是可選的，日期不一致或格式損壞時只顯示原有寬度。

進出榜原因由原篩選器逐條計算本次證據，暫存於 UI 記憶體。保留原名單比較與帳號儲存契約；沒有舊指標時不聲稱歷史穿越原因，資料不可用時不生成假的出榜。

## Consequences

不新增 chart engine、像素持久化、授權路徑或永久資料寫入。長歷史目前名單回推帶有存活者偏差，台股依附件標記交易訊號不適用。未來如需精確保存「觸發當時」的原因，需另提有版本的逐日證據契約，不能從本次數值反推。
