# FinMind Taiwan statements trial

Date: 2026-10-16  
Branch: `codex/tw-financials-finmind`, based on `main` at `38235ad`.

## Gate result

The preflight did not produce a valid FinMind-built sample, so the full backfill was not run.

- `--probe 2330,2881,2317` completed: 9 API requests; full output is attached to the draft PR body.
- `--report-dir /tmp/report-check --limit 60` stopped at the default 600-second budget after 15 stocks / 47 requests. The log reported `outcomes {}`, `FinMind statements NOT DONE: 0/1977 stocks fetched`, and a cash-flow verdict of `ambiguous` over 97 company-years.
- The follow-up `--report-dir /tmp/report-check --tickers 2330` had the same plan (`1977 stocks`, all `new`) and again stopped after 15 stocks / 47 requests. With an empty fetch state, `--tickers` does not isolate or prioritize 2330; `plan()` still traverses the sorted universe.
- The 2330 and 2881 files in `/tmp/report-check` compare byte-for-byte equal to the original `docs/report/financials` files. Their values below are existing report values, not output from this FinMind trial.

## Existing-file spot check (not validated by the trial)

The existing 2330 2024 annual values are within the requested 2% tolerance:

| Field | Existing value | Reference | Difference |
| --- | ---: | ---: | ---: |
| revenue | 2,894,307,700,000 | 2,894,307,700,000 | 0% |
| netIncome | 1,158,380,200,000 | 1,173,000,000,000 | -1.25% |
| epsBasic | 45.25 | 45.25 | 0% |
| grossProfit | 1,624,353,600,000 | 1,624,000,000,000 | +0.02% |
| operatingIncome | 1,322,003,100,000 | 1,322,000,000,000 | +0.23% |
| totalAssets | 6,691,764,700,000 | 6,691,000,000,000 | +0.01% |
| operatingCashFlow | 1,826,177,100,000 | 1,826,000,000,000 | +0.01% |
| capex (positive) | 964,981,600,000 | 956,000,000,000 | +0.94% |

The existing 2881 file has no `grossProfit` series and includes `quality.issues: ["financial-sector-format"]`; this file was also unchanged by the trial.

## Probe mapping for the requested fields

| Output field | Dataset | FinMind type / `origin_name` shown by probe | Rule |
| --- | --- | --- | --- |
| revenue | pl | `Revenue` / `營業收入` | `revenue(core)` |
| netIncome | pl | `IncomeAfterTaxes` / `本期淨利（淨損）`; also `IncomeAfterTax` / `本期稅後淨利（淨損）` | `netIncome(core)` |
| epsBasic | pl | `EPS` / `基本每股盈餘` | `epsBasic(core)` |
| grossProfit | pl | `GrossProfit` / `營業毛利（毛損）` | `grossProfit(0)`, `grossProfit(1)` |
| operatingIncome | pl | `OperatingIncome` / `營業利益（損失）` | `operatingIncome(0)` |
| totalAssets | bs | `TotalAssets` / `資產總額` and `資產總計` | `totalAssets(0)` |
| operatingCashFlow | cf | `CashFlowsFromOperatingActivities` / `營業活動之淨現金流入（流出）`; `NetCashInflowFromOperatingActivities` / `營業活動之淨現金流入`; also spelling variants shown in probe | `operatingCashFlow(0)`, `operatingCashFlow(1)` |
| capex | cf | `PropertyAndPlantAndEquipment` / `取得不動產、廠房及設備` | `capex(0)`, `capex(1)`; output is positive |

The probe mapping has entries for these fields. The blocking issue is that the bounded trial did not write any statement files and did not reach 2330; the cash-flow single-quarter / cumulative verdict remains undecided. No mapping changes or full backfill were made.
