# 美股全市場寬度訊號

`rules.py`、`core.py`、`calibrate.py` 與 `daily.py` 來自使用者提供的 `us-breadth-signals` 附件；規則版本為 `rulesVersion: 1`。規則與交易模擬照附件保留。整合只調整了套件匯入與每日流程的資料檢查：下載失敗、指數資料缺漏／過短／過期或寬度重疊差異超標時，回傳失敗且不寫入歷史或訊號檔。附件未包含 `RESEARCH.md`。

每日指數下載與市場風險警示共用 `scripts/index_daily.py`：先批次下載 Yahoo 兩年日線，只對空檔／有效根數不足／資料日不符者逐檔最多重試兩次（5／10秒）；明確限流沿用30／60秒退避，持續限流停止追加 Yahoo 請求。之後唯讀使用 `--archive-dir .cache/kline-archive` 的 per-symbol index 及經 SHA-256 驗證的年度 CSV。兩者均只用 XNYS 已收盤 session，split-adjusted、不作股利還原。每檔來源記在 `markets.us.lowSignal.indices[].meta`，備援代號也印入 log。

最低400根有效日 K、與網站寬度 asOf 同日、重疊檢查與計算規則都不變。封存不足／日期落後或兩種來源都失敗時，history CSV 與 signals JSON 仍保持原檔；不降低門檻，不自行補造新收盤。此備援不修改校準下載流程或 bootstrap。

在 repo 根目錄重新校準：

```bash
python -u -m scripts.breadth_signals.calibrate \
  --out-dir docs/report \
  --save-prices .cache/breadth-calibration-prices.pkl
```

正式 workflow 為 `.github/workflows/breadth-calibration.yml`，支援手動執行並每季重跑。它先將輸出寫到 `.cache/` 暫存資料夾，完成樣本與交易筆數檢查後，再安裝到 `docs/report/`。每日 workflow 讀取同一組校準門檻，更新長歷史，並由 `universe` generated-path owner 保存；校準與每日流程共用 `universe-full` concurrency group。

## Bootstrap 來源

`bootstrap/` 只有衍生的校準 JSON 與每日聚合寬度 CSV，不含逐檔價格。每日 workflow 僅在 generated 報表缺少校準檔或歷史檔時，將這些 seed 複製到 `docs/report/`；日常更新不會改寫 bootstrap。

這份 seed 由 2026-11-02 04:59 UTC 的真實校準產生。股票名單取自 NasdaqTrader `nasdaqlisted.txt` 與 `otherlisted.txt`，透過網站共用的 `scripts.universe_sources.parse_us_listed` 解析；下載 5,346 個代號的 Yahoo Finance 日線，資料期間為 2003-01-02 至 2026-10-30。寬度樣本為 2003-03-28 至 2026-10-30，共 5,937 個交易日。歷史 CSV 最後 120 天以網站 `breadth.json` 的值覆蓋，as-of 為 2026-10-30；校準與網站重疊差異最大值為 MA20 0.4 個百分點、MA60 0.4 個百分點。

目前校準的抓低點命中率（先碰停利的比例）與任意一天進場的對照率如下：

| 指數 | 訊號筆數 | 命中率 | 對照率 |
| --- | ---: | ---: | ---: |
| S&P 500 | 63 | 68.25% | 47.61% |
| 那斯達克 | 62 | 66.13% | 47.06% |
| 道瓊工業 | 60 | 65.00% | 46.18% |
| 羅素 2000 | 67 | 64.18% | 45.18% |
| 費城半導體 | 57 | 63.16% | 47.76% |

名單是目前仍上市的 NasdaqTrader 股票與 ADR，因此已下市公司不在 universe 內，歷史結果有存活者偏差；這些命中率是回測摘要，不保證未來表現。逐檔價格快取留在被 `.gitignore` 排除的 `.cache/`，不納入 bootstrap。
