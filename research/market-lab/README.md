# Market Research Lab

這是 `my-stock-screener` 的獨立研究命名空間。管線只讀取 repo 的 `data/*.csv` 與既有財務摘要；不改寫舊策略、網站、工作流程、通知或資料庫。

## 執行

```bash
python -m pip install -r research/market-lab/requirements.txt
python research/market-lab/run_research.py \
  --data-dir data \
  --financial-dir docs/report/financials \
  --tw-fundamentals docs/report/fundamentals/tw.json \
  --output-dir research/market-lab/reports
```

選項 `--resume` 會以輸入檔案 SHA-256、規格版本及程式版本做快取；輸入未變時重用原始逐檔盤點與事件結果。報告生成可加 `--no-cache` 強制重算。研究只使用本機既有資料，不會自動呼叫付費資料服務或擴大 repo 的排程。

`reports/testing-log.csv` 記錄最終日期區間曾否被先前輸出檢視。若新執行與先前已輸出的測試日期重疊，該區間會標為已暴露，不再列作 untouched holdout，也不允許策略通過標記。

## 資料輸入與輸出

原始價量 CSV 不複製到本目錄。報告與快取預設放在 `reports/`、`.cache/`；大量逐事件資料可留在本機，Git 僅保存可審查的摘要。若環境安裝 `pyarrow`，管線輸出真正的 `event-study-results.parquet`；否則以壓縮 CSV 存在同名 `.csv.gz`，並在覆蓋率及品質報告標示 Parquet writer 缺失，不會建立假 Parquet 檔。

補充歷史資料時，使用 `market_lab/backfill.py` 匯入有授權且帶來源、授權條款、調整狀態與 SHA-256 的本機檔案。匯入依 `symbol/date` 冪等合併、保留原始檔與 manifest，並支援分批續跑。它不會繞過來源限制或自行啟用付費服務。

## 目前資料快照

初始快照為 `main` SHA `7e0a1fa12ac74d3d848fa88ea7ad7d47369645b2`（2026-10-19）。其中 1,547 個價量 CSV 約含 527 檔美股、1,019 檔台股與 1 檔櫃買指數；多數個股只有約 206 根日 K，且缺少股利欄位、完整公司行動與歷史成分股名單。這些資料只支持條件式、一年左右的探索，不支持十年全市場結論，也不能排除存活者偏誤。

策略績效以預先固定的費用情境呈現，不代表實際券商成本。除非有完整公司行動、退市報酬、流動性與歷史宇宙證據，排行榜不得解讀為可部署或已驗證的獲利策略。

研究定義與裁決記錄見 [methodology.md](methodology.md)；模型能力與實際執行情況見 [model-execution.md](model-execution.md)，交叉審查結果見 [codex-review.md](codex-review.md)。
