# 五年、ETF 與指數封存（PR-B）

本變更只提供程式、手動 workflow 與暫存目錄試跑。沒有執行正式補檔、推送 kline-archive、部署或修改 main 上的永久行情／財報。所有舊日期仍優先；不同值寫入既有 compact conflicts，不覆寫、不刪除、不 force-push。

## 範圍與來源

`config/kline-archive-instruments.json` 是主要美股 ETF 與網站指數設定。`scripts/kline_instruments.py` 只讀公開目錄／報告，不讀個人自選、持股或帳戶。

- 普通股／ADR：既有全市場來源，加上 manifest 已有代號（下市或離開來源不刪歷史）。
- 台股 ETF：部署中的 SymbolCatalog `kind=etf`，同時接受 `.TW`／`.TWO`。2026-11-04 實測目錄有 263 檔，263 檔上市、0 檔上櫃；不以名稱猜 ETF。測試另有 `.TWO` fixture，目錄未來加入時自動納入。
- 美股 ETF：設定的 76 檔主要 ETF，加上公開目錄、每日報告、題材、熱門股及行情目錄出現且 NasdaqTrader `ETF=Y`、`Test Issue!=Y` 的代號。曾納入的公開 ETF 記在 `state/instruments.json`，離榜不刪除。
- 截至試跑共 369 個額外標的：263 台股 ETF、88 美股 ETF、18 指數。
- Yahoo 使用 split-adjusted OHLC，沒有股息調整；成交量為股數，未知為空欄。**櫃買現金指數 `^TWOII` 是明確來源例外**：Yahoo chart_period 實測 404，因此沿用網站已有的 TPEx 官方月 OHLC 端點；現金指數無拆股／股息調整。只讀現存 `data/^TWOII.csv`，補缺少月份，不呼叫會寫入原檔的 main.py 更新函式。官方表沒有成交量，保留未知；既有檔有可信正成交股數時保留。

最終 88 檔美股 ETF（2026-11-04）：

```text
AGG ARKF ARKG ARKK ARKQ ARKW BIL BITO BND BOTZ CIBR DIA EEM EFA ETHA EWG EWJ EWT EWY
FBTC FEZ FINX FXI GDX GLD GRID HACK HYG IAU IBB IBIT ICLN IEF IGV IHI ITA IVV IWM
JEPI JEPQ KWEB LIT LQD MAGS MCHI QQQ QQQM QTUM REMX SCHD SGOV SHY SLV SMH SOXL SOXS
SOXX SPY SQQQ TAN TIP TLT TQQQ UFO URA USO VEA VGK VIG VOO VTI VT VTV VUG VWO VYM
XBI XLB XLC XLE XLF XLI XLK XLP XLRE XLU XLV XLY
```

網站實際使用的 18 指數與目錄：

| 目錄 | 代號 |
| --- | --- |
| tw | ^TWII、^TWOII |
| us | ^GSPC、^IXIC、^DJI、^RUT、^SOX |
| global | ^N225、^KS11、^STOXX50E、^FCHI、^GDAXI、^FTSE、^GSPTSE、^HSI、^BSESN、^AXJO、^BVSP |

外國指數另放 global，避免日本較新的日期被算成美股資料日期。目錄保留合法的原代號，HTTP 每個 segment 做 URL encoding：`tw/%5ETWII/2021.csv`。不把未引用的代號直接插入 shell。

## Manifest 與每日追加

Manifest v2 保留既有市場統計，新增 `bySymbol`：實際範圍、kind、來源／幣別／時區、encoded path，以及每年檔的列數、bytes、日期與 SHA-256。總 bytes 含 manifest、README、進度與稽核，不含 Git objects。唯讀 verify 仍接受已部署 v1，不會自動改寫它；產生新資料時才升級 v2。大 manifest 由資料健康程式透過已授權 GitHub API 的 raw media type 讀取，避開 Contents API 1MB base64 限制。

`universe-full` 保留原普通股 cache、來源與輸出；額外標的使用 `.cache/universe-full/archive-instruments`。共用既有 Yahoo 批次 reader、限速與 OHLC/quarantine 驗證，僅完成交易日寫 cache；連續日可批次追加，缺多日則讀歷史補齊。^TWOII 使用上述官方來源。額外來源失敗仍可發布原股票 payload。合併仍經同一 append-only publisher、基準驗證及 SHA 稽核，不增加前端輪詢或通知。

## 維護者操作順序（本次沒有執行）

1. 審查並合併本 PR；不需要 Supabase SQL、函式或 GCP 部署。workflow 明確從 main 讀程式，避免在尚未審查的分支執行正式寫入。
2. Actions → **Permanent K-line five-year expansion** → Run workflow，先選 indices、batch_size 20、budget 600。再用 etfs，最後 stocks；也能 all。markets 的 US 包含獨立儲存的全球指數。
3. 檢查 fetch 的 `operations.json` 與 publish 的 manifest／conflicts。fetch 只有 read 權限；只有 publish job 有 contents write，且只推 kline-archive。不呼叫 main journal publish、不部署。
4. 重複相同 segment／設定。`state/backfill.json` 跳過已達 `2021-01-01` 目標的 done 代號，retryAfter 未到不重試；每批最多 250 檔、預設抓取 600 秒，可自行縮小。逐檔寫 checkpoint，中斷前成功取得的完整 artifact 可由後續 publish 接續。
5. 每批觀察總容量與最早日期。新上市代號標 `source-starts-later`，不製造上市前 K 棒。沿用 OHLC／成交量規則，不以刪歷史達成容量目標。普通每日排程之後自動追加已納入標的。

來源鎖沿用 universe-full，publisher 沿用 kline-archive-publish，皆不取消進行中工作。workflow 只有 workflow_dispatch，沒有新正式排程。不同值只記 compact conflicts，原日期保留。

## 真實 50 檔唯讀來源／本機試跑

2026-11-04 以 live kline-archive 的淺層 clone 為基準，push URL 設為 DISABLED；所有 payload、CSV 新增與驗證只在 `/tmp`。樣本：台／美股票各 17 檔（含新上市）、台／美 ETF 各 5 檔、6 指數（TWII、TWOII、GSPC、IXIC、N225、STOXX50E）。

| 指標 | 實測 |
| --- | --- |
| 基準代號／年度 CSV／列數 | 7,358／21,182／3,535,829 |
| 基準 CSV／全部檔案 | 175,830,402／183,955,721 bytes |
| 前 49 檔 Yahoo fetch | 35.531 秒 |
| 櫃買官方補 28 個月份 | 72.239 秒，取得 2021-01-04～2026-11-04 共 1,415 列 |
| 50 檔新增／覆寫／差異 | 39,944／0／752（既有值保留） |
| 兩份 payload 套用，manifest 留到驗證一次產生 | 2.673 秒 |
| 完整 verify(base=HEAD, refresh_manifest=True) | 49.497 秒，通過不可刪除／不可未稽核改歷史檢查 |
| 試跑後年度檔／列數 | 21,330／3,575,773 |
| 試跑後 CSV／全部檔案 | 177,948,763／190,685,844 bytes |

每個市場再依既有資料開始日分層外推，CSV 約 **458,876,295 bytes（459MB）**。加上 manifest、進度及 compact conflict 稽核，估計約 **480MB**；不是硬性保證，個股歷史／來源差異與抽樣誤差都會影響容量。每批輸出精確 bytes；超出目標時先評估儲存格式，不刪現有資料。按來源吞吐推估 fetch 約 1.5 小時，另有約 31 批 runner 排隊／安裝／驗證／push，完整操作約 3～6 小時，限流或重試會延長；每批可續傳。

7,000 代號 × 3 年合成測試另外通過：2,240,000 筆來源差異以 6,874,398 bytes compact 記錄；套用 110.431 秒、驗證 58.311 秒、完整本機每日發布 181.311 秒，維持既有 300 秒門檻。此為測試 bare-origin，沒有推到正式 repo。

## 驗證

擴充單元測試涵蓋公開 ETF 判斷、上櫃 ETF、五個完整年度起日、分批續傳、舊值不覆寫、全球指數防刪、manifest v1/v2 與年 SHA、每日缺多日補齊、未知成交量、來源故障保留、TPEx 實際欄位形狀與只讀來源／預算中斷、quarantine 沿用、額外來源失敗不阻擋股票，以及 workflow 不寫 main journal。

實際測試數與瀏覽器結果記在 PR 說明。沒有介面變更，沿用指數／題材／導覽的桌機、390px、375px Chromium／iPhone WebKit 回歸；沒有宣稱實機 iPhone 或獨立審查通過。
