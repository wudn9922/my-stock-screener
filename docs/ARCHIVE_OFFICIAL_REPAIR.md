# 官方封存補檔、占位列刪除與完整還原

本 PR 一般 merge main `0f42fef0b`（已合併 #230），merge commit `cdd648d7f`；沒有另納入 #230 分支、rebase 或 force-push。工具預設 dry-run，正式寫入由維護者審查後自行執行。本次只對唯讀封存與無 remote 的本機副本驗證，沒有推送 kline-archive 或 dispatch workflow。

## 官方來源與欄位

| 用途 | 官方端點 | 實際欄位／日期 |
| --- | --- | --- |
| 上市 OHLCV | `https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date=YYYYMMDD&type=ALLBUT0999` | 依 tables.fields 找證券代號、成交股數、開盤價、最高價、最低價、收盤價；不依表格序號 |
| 上市官方等價路徑 | `https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=YYYYMMDD&type=ALLBUT0999&response=json` | 同一日期、同一 ALLBUT0999 與欄位驗證 |
| 上櫃 OHLCV | `https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=民國年%2FMM%2FDD&id=&response=json` | 代號、成交股數、開盤、最高、最低、收盤；外層西元日與有提供的 table 民國日都核對 |
| ^TWII 月歷史 OHLC | `https://www.twse.com.tw/indicesReport/MI_5MINS_HIST?date=YYYYMMDD&response=json` | 日期、開盤指數、最高指數、最低指數、收盤指數；資料列民國年轉西元，選精確目標日 |
| ^TWII 官方等價路徑 | `https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?date=YYYYMMDD&response=json` | 相同月資料與 OHLC 欄位／日期驗證 |

實際請求中舊路徑會回 307／HTML。工具不跟隨 Location，只能改查表列固定官方等價路徑；錯誤、HTML、欄位改版或日期不符都不能當成休市。報告保存實際成功 URL、回應 SHA-256／bytes 與 HTTP 嘗試。來源快取也核對實際端點與 SHA；`--offline` 缺資料即停止，不使用 Yahoo。

成交股數的單位為股，不是千股。停牌、零量、缺 OHLC（含 `--`）及缺成交量的個股一律跳過，列出原因；不造平盤。MI_5MINS_HIST **沒有成交量欄位**，^TWII 的量保留未知（CSV 空欄），不填零。

## 補檔與價格基準

目標 TW 2021-04-06、2025-08-01。以封存已有 TW 代號為範圍；官方有、封存無歷史的代號只計入排除數，不推測 IPO 或建立缺乏比較證據的歷史。

1. 從官方 ^TWII 月資料取得真正交易日，取目標日前後各最近兩日；只在本月不足時讀相鄰月。
2. 每檔四日都必須同時有官方與封存收盤；少一日即跳過，不能以更遠日期代替。
3. 比值定義為 `archive.close / official.close`。四個比值都在 `1±0.005` 內，factor=1，保留官方原始價量。
4. 否則須 `max(ratios)/min(ratios)-1 <= 0.005`；採四值中位數（中間兩值平均）為 factor。OHLC 乘 factor、量除 factor，再用既有四位價格／整數量格式寫入。
5. 不穩定或不足一律跳過。報告逐檔保留 factor、四日比較、擬新增列與跳過理由；年度 backfilled 稽核也保存 `officialBasis.factor`／witnesses 和實際官方 sourceUrl。

只把缺日期送入既有 `put_symbol(..., mode='backfill')`。既有列不送修正、不覆寫、不刪除；寫前再次確認目標不存在。年檔、per-symbol index 的 SHA／bytes／rows 和 manifest 沿用 `kline_archive`。大量稽核共用原 schema 批次沖寫，不逐檔重讀整份 journal。

[完整 dry-run](research-verification/official-backfill-dry-run-20261107.json) 固定快照 `62bd86a6e7fcde3572c56aa607eed5018a0cfe68`，前後均用既有 verifier 驗證 41,573 年檔、9,796,080 列（含 global），封存 git status 乾淨：

| 日期 | 新增列／涉及年檔 | factor≠1 | 原有列保留 | 跳過 |
| --- | ---: | ---: | ---: | ---: |
| 2021-04-06 | 1,727 | 661 | 1 | 495 |
| 2025-08-01 | 1,783 | 216 | 193 | 247 |

此表涵蓋股票、ETF 與指數，和 #230 普通股觀測數的口徑不同。跳過原因與每個代號均在 JSON 中；包括當日沒有官方觀測、無成交／缺價、比較資料不足、基準不穩定。firstDate 是封存覆蓋界線，不等於已驗證 IPO 日期。

```sh
python scripts/repair_kline_archive.py --archive-dir /path/to/kline-archive \
  --mode backfill --report docs/research-verification/official-backfill.json
```

省略 `--apply` 不寫封存。報告與來源快取必須在 archive 外；需乾淨、已有 v3 per-symbol indexes 的獨立 Git checkout。寫前、寫後都跑既有 archive 驗證，包含原有列與歷史稽核的比較；metadata 可在同一驗證掃描中重建。

## 10/23 刪除與備份

只選 `tw/*/2026.csv` 中日期 2026-10-23、Volume=0、O=H=L=C 的列，不要求等於前一日收盤。其他市場、日期、正量或非平盤列均保留。預期數固定採 #230 的 2,214；差超過 1% 即停止並輸出報告，CLI 沒有關閉保護的選項。刪除會令年檔為空也停止，不另改年檔格式。

```sh
python scripts/repair_kline_archive.py --archive-dir /path/to/kline-archive \
  --mode remove-placeholders --report docs/research-verification/placeholder-removal.json
```

`--apply` 先在臨時 clone 完成刪除、原格式重建與驗證，再於 **原 checkout 變更任何內容之前**，create-only 寫入並 fsync：

`docs/research-verification/removed-placeholders-20261023.json`

備份包含每筆代號／OHLCV／年度路徑／刪前 SHA，以及所有改動年檔、index、manifest、journal 的原始 gzip+base64 bytes、前後 SHA-256 和完整前後 Git tree。既有備份不覆寫。原 checkout 的 HEAD／全 tree 與每個前置 SHA 都必須仍一致，才逐位元組原子搬入已驗證的檔案，並再跑 verifier。刪除稽核有固定 `kline-archive/verified-placeholder-20261023` reason、每檔前後 SHA；新 reason 不能授權其他日期或美股刪除。

## 還原

```sh
python scripts/repair_kline_archive.py --archive-dir /path/to/kline-archive \
  --mode restore \
  --backup /safe/path/removed-placeholders-20261023.json \
  --report docs/research-verification/placeholder-restore.json
```

預設只驗證備份與擬還原數；維護者審查後加 `--apply`。整個封存內容 tree 必須仍等於備份的刪除後（或已還原）tree；有後續更新就拒絕，不覆蓋後續資料。備份的路徑、解壓大小、原始 SHA、每筆零量平盤及刪後內容都核對後，才還原原始 bytes。測試包含 CRLF 原檔，不只是重新序列化相同數字。

完整還原也恢復 index、manifest 與 journal bytes。既有歷史驗證器只新增一個窄例外：**當前所有封存內容必須逐檔等於緊接前次指定刪除的 Git parent**，並重新核對該次差異只有允許的 TW 10/23 刪除與 metadata；用一批 Git blobs 驗每筆原始零量平盤與前後 SHA。一般刪日期、改價、改稽核或其他市場仍拒絕。Git 的刪除／還原 commit 歷史保留，沒有 reset 或 force-push。archive review 讀三層 commit，才能在還原 commit 的 `HEAD^` 比對時取得原件證據。

## Workflow 與維護者操作

`Official TW archive repair`（`kline-archive-official-repair.yml`）只有 workflow_dispatch，mode 為 backfill／remove-placeholders／restore，**apply 預設 false**。本次沒有 dispatch。

- prepare 只有 contents:read；dry-run 只產報告。apply=true 也先在該 job 的本機獨立 checkout 操作。
- 報告／完整刪除備份先成功上傳 artifact `archive-repair-RUN_ID`（保留90日），publish job 才可執行；請另行下載長期保存恢復備份。
- publish 只有 apply=true、prepare 成功且確有變動時取得 contents:write，重新 checkout 精確相同 archive tip，套用已準備 patch，重新跑含 base 的 archive verifier，再單次非 force 推送。tip 改變就停止，不 rebase 或自動重試。
- 與所有原 archive publishers 共用 `kline-archive-publish` lock。失敗通知沿用 #236，共用取消判定；fallback 已列入新工作名稱。
- restore 必須提供**正式刪除那次**的 `backup_run_id`，下載其恢復備份；本機演練備份不能當成正式恢復檔。

## 四個已核對休市日與下游

2022-02-04、2023-01-18、2024-10-31、2026-07-10 均由 TWSE 明確無資料與 TPEx 指定日期的空行情共同確認。證據與逐日網址見 [官方核對](research-verification/official-market-days-20261107.json) 及 verified-market-closures.json。新增 `official-no-quotes` 必須有兩個精確官方日期 URL、空資料狀態與 SHA；不是裸 marker，也不把網路故障當休市。

Python 分類、資料缺漏掃描與前端同用 reviewed seed；舊 missing-session marker 不會重新開市，實際股票成交或新的官方交易證據仍可否決。未知成交量保護保留；10/23 在實體刪列後可延用已驗證隊列證據。前端完整投影 cache 升 v6，raw 年檔 cache 不改。

[重跑休市掃描](research-verification/official-repair-closure-scan-20261107.json)、[缺漏掃描](research-verification/official-repair-market-gaps-20261107.json)：四日不再列缺漏，TW 只剩兩個待補日；10/23 仍有原 2,214 筆占位列（因正式封存未改）。日／週／月聚合及分析在刪除前後結果一致；專項包含未知量、正量否決、個股停牌、既有快取與實體刪列後的圖表。

## 完整副本演練與瀏覽器驗證

[本機全量演練摘要](research-verification/official-repair-rehearsal-summary-20261107.json)：使用同一完整封存的獨立 clone，移除 Git remote，先新增 3,510 列，再刪除 2,214 列、完整還原；每階段前後均跑原 verifier。還原前也核對備份內每個檔案與獨立 Git 原件 blob，不只相信備份自己記錄的 SHA。

刪前與還原後的全部內容 tree 均為 `f1a5b15680b895346066bf0d4f2e2577da574b9d`，不是只比對 OHLC 數值。4,430 個恢復檔案含年檔、index、manifest、journal。備份 16,715,910 bytes、SHA-256 `4a335c6e5ec152ea5eec14ad6cb43831a42a64e30876f262b483cad25a65b464`。原始演練備份與原始報告另打包保留在工作區，不冒充正式操作的恢復 artifact，也不放進 main 的正式資料。

25 項圖表瀏覽器回歸通過：桌機／390px Chromium／iPhone WebKit 15 項，375px Chromium／WebKit 10 項；包含日週月、個股停牌、缺漏交易日保留、實體刪列後日期／價格及手機無溢出。這是 Playwright 模擬，不是實體 iPhone。

最終[驗證總表](research-verification/official-archive-repair-verification-20261107.json)：完整 Python 1,982 passed、168 subtests passed；既有需額外啟用的壓力案例已另跑通過，未新增 skip。修補專項21項通過；完整 Vitest159檔／2,029項、lint、build、全部workflow的actionlint 1.7.12通過；21次取消當時口徑回放0通知。壓力測試224萬筆衝突，apply161.493秒、verify92.126秒，原300秒門檻未變。
