"""
Synthetic Yahoo fundamentals-timeseries / chart payloads for the financial statement history tests.

  python3 tests/financials_synth.py   # rewrites tests/fixtures/financials/*.json

Every number here is made up (round ratios of a made-up revenue line); the payloads only copy the
shape of Yahoo's responses: {"timeseries": {"result": [{"meta": {"type": [...]}, <type>: [point]}]}}
with points {"asOfDate", "periodType", "currencyCode", "reportedValue": {"raw", "fmt"}}.
The two sample-*.json files are build_financials.py output for the frontend (top-level "_fixture").
"""
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "financials")
SYNTHETIC = "SYNTHETIC test data: made-up numbers in the shape of a Yahoo Finance response"
# Fixed clock for the generated samples (and the tests that compare against them)
SAMPLE_NOW = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)

BN = 1e9


def fmt(value):
    return f"{value / BN:.2f}B" if abs(value) >= BN else f"{value:.2f}"


def point(end, value, currency, period_type):
    return {
        "dataId": 20000,
        "asOfDate": end,
        "periodType": period_type,
        "currencyCode": currency,
        "reportedValue": {"raw": value, "fmt": fmt(value)},
    }


def money(value):
    """Whole millions, as Yahoo reports most US / TW statement lines."""
    return int(round(value / 1e6)) * 1000000


def statement(revenue, shares, ratios):
    """One period of made-up statement lines (Yahoo type → value) from revenue and share count."""
    r = revenue
    pretax = r * (ratios["oi"] + ratios.get("other", 0.0))
    net = pretax * (1 - ratios["tax"])
    values = {
        "TotalRevenue": money(r),
        "PretaxIncome": money(pretax),
        "TaxProvision": money(pretax * ratios["tax"]),
        "NetIncomeCommonStockholders": money(net),
        "NetIncome": money(net),
        "DilutedEPS": round(net / shares, 2),
        "BasicEPS": round(net / (shares * 0.99), 2),
        "DilutedAverageShares": int(shares),
        "OperatingCashFlow": money(r * ratios["ocf"]),
        "CapitalExpenditure": -money(r * ratios["capex"]),
        "FreeCashFlow": money(r * (ratios["ocf"] - ratios["capex"])),
        "StockholdersEquity": money(ratios["equity"]),
        "TotalDebt": money(ratios["debt"]),
        "CashCashEquivalentsAndShortTermInvestments": money(ratios["cash"]),
        "CashAndCashEquivalents": money(ratios["cash"] * 0.5),
        "TotalAssets": money(ratios["assets"]),
        "CashDividendsPaid": -money(r * ratios["div"]),
        "RepurchaseOfCapitalStock": -money(r * ratios["buyback"]),
    }
    if "gross" in ratios:
        values["CostOfRevenue"] = money(r * (1 - ratios["gross"]))
        values["GrossProfit"] = money(r) - money(r * (1 - ratios["gross"]))
        values["OperatingIncome"] = money(r * ratios["oi"])
        values["TotalOperatingIncomeAsReported"] = money(r * ratios["oi"])
    if "interest" in ratios:
        values["InterestExpense"] = money(r * ratios["interest"])
    return values


def build(symbol, annual, quarterly, ratios, currency, tweak=None):
    """annual / quarterly: [(end, revenue, shares)] → payload dict (tweak(series) edits in place)."""
    series = {}
    for prefix, rows, period_type in (("annual", annual, "12M"), ("quarterly", quarterly, "3M")):
        for end, revenue, shares in rows:
            for kind, value in statement(revenue, shares, ratios).items():
                series.setdefault(prefix + kind, []).append(point(end, value, currency, period_type))
    if tweak:
        tweak(series)
    result = []
    for kind in sorted(series):
        points = series[kind]
        entry = {"meta": {"symbol": [symbol], "type": [kind]}}
        if points is not None:
            entry["timestamp"] = [1700000000 for item in points if item]
            entry[kind] = points
        result.append(entry)
    return {"_synthetic": SYNTHETIC, "timeseries": {"result": result, "error": None}}


def find(series, kind, end):
    for item in series.get(kind) or []:
        if item and item.get("asOfDate") == end:
            return item
    raise KeyError((kind, end))


# ---------------------------------------------------------------------------------------------
# Companies (made up)
# ---------------------------------------------------------------------------------------------
US_RATIOS = {
    "gross": 0.46, "oi": 0.31, "other": 0.002, "tax": 0.16, "interest": 0.002,
    "ocf": 0.29, "capex": 0.03, "div": 0.037, "buyback": 0.23,
    "equity": 70 * BN, "debt": 100 * BN, "cash": 60 * BN, "assets": 360 * BN,
}
# 52/53-week fiscal year ending late September
US_ANNUAL = [
    ("2022-09-24", 394 * BN, 16.3e9),
    ("2023-09-30", 383 * BN, 15.8e9),
    ("2024-09-28", 391 * BN, 15.4e9),
    ("2025-09-27", 416 * BN, 15.0e9),
]
US_QUARTERLY = [
    ("2024-12-28", 124 * BN, 15.15e9),
    ("2025-03-29", 95 * BN, 15.05e9),
    ("2025-06-28", 94 * BN, 14.95e9),
    ("2025-09-27", 103 * BN, 14.85e9),   # FY2025 quarters add up to the FY2025 annual revenue
    ("2025-12-27", 140 * BN, 14.8e9),
    ("2026-03-28", 110 * BN, 14.75e9),
    ("2026-06-27", 100 * BN, 14.7e9),
]
# What an earlier run (mid 2025) would have received
US_PREV_ANNUAL = [
    ("2021-09-25", 366 * BN, 16.9e9),
    ("2022-09-26", 394 * BN, 16.3e9),    # same period as 2022-09-24 (within 5 days)
    ("2023-09-30", 383 * BN, 15.8e9),
    ("2024-09-28", 391 * BN, 15.4e9),
]
US_PREV_QUARTERLY = [
    ("2024-06-29", 86 * BN, 15.3e9),
    ("2024-09-28", 95 * BN, 15.25e9),
    ("2024-12-28", 124 * BN, 15.15e9),
    ("2025-03-29", 95 * BN, 15.05e9),
    ("2025-06-28", 94 * BN, 14.95e9),
]


def us_full_tweak(series):
    # Net income to common holders missing for FY2022 → NetIncome is used
    find(series, "annualNetIncomeCommonStockholders", "2022-09-24")["reportedValue"]["raw"] = None
    # Cash + short-term investments missing for one quarter → cash & equivalents is used
    cash = series["quarterlyCashCashEquivalentsAndShortTermInvestments"]
    cash[:] = [item for item in cash if item["asOfDate"] != "2024-12-28"]
    # A trailing-twelve-months point inside an annual series, a 12M point inside a quarterly series
    series["annualTotalRevenue"].append(point("2026-06-27", 449 * BN, "USD", "TTM"))
    series["quarterlyTotalRevenue"].append(point("2025-09-27", 416 * BN, "USD", "12M"))
    series["quarterlyTotalRevenue"].append(point("2025-12-27", 260 * BN, "USD", "6M"))
    # A point in another currency, a null entry, a bad date and a string value
    find(series, "quarterlyOperatingCashFlow", "2025-03-29")["currencyCode"] = "EUR"
    series["quarterlyDilutedEPS"].insert(0, None)
    series["quarterlyBasicEPS"].append(point("2025-13-45", 1.0, "USD", "3M"))
    series["quarterlyNetIncome"].append(
        {"asOfDate": "2026-06-27", "periodType": "3M", "currencyCode": "USD", "reportedValue": {"raw": "12"}}
    )
    # A requested type Yahoo has no data for (meta only), and a type that was not requested
    series["annualInterestExpense"] = None
    series["trailingTotalRevenue"] = [point("2026-06-27", 449 * BN, "USD", "TTM")]


def us_restated_tweak(series):
    us_full_tweak(series)
    # FY2024 revenue restated +3% (flagged), FY2023 revenue +1% (within tolerance)
    item = find(series, "annualTotalRevenue", "2024-09-28")
    item["reportedValue"]["raw"] = money(391 * BN * 1.03)
    item = find(series, "annualTotalRevenue", "2023-09-30")
    item["reportedValue"]["raw"] = money(383 * BN * 1.01)


TW_RATIOS = {
    "gross": 0.58, "oi": 0.47, "other": 0.03, "tax": 0.15, "interest": 0.001,
    "ocf": 0.62, "capex": 0.35, "div": 0.13, "buyback": 0.0,
    "equity": 3900 * BN, "debt": 950 * BN, "cash": 2300 * BN, "assets": 6900 * BN,
}
TW_ANNUAL = [
    ("2022-12-31", 2264 * BN, 25.93e9),
    ("2023-12-31", 2162 * BN, 25.93e9),
    ("2024-12-31", 2894 * BN, 25.93e9),
    ("2025-12-31", 3600 * BN, 25.93e9),
]
TW_QUARTERLY = [
    ("2025-03-31", 840 * BN, 25.93e9),
    ("2025-06-30", 900 * BN, 25.93e9),
    ("2025-09-30", 930 * BN, 25.93e9),
    ("2025-12-31", 930 * BN, 25.93e9),
    ("2026-03-31", 1000 * BN, 25.93e9),
    ("2026-06-30", 1050 * BN, 25.93e9),
]


def tw_tweak(series):
    # A duplicate EPS point in USD for the same period: dropped by the currency guard
    series["annualDilutedEPS"].append(point("2025-12-31", 9.99, "USD", "12M"))


def foreign_tweak(series):
    # US-listed company reporting in TWD: annual EPS in TWD (per ordinary share) is kept, quarterly
    # EPS points arriving in USD are dropped; share counts carry a currency code too and are kept
    # whatever it says
    for kind in ("quarterlyDilutedEPS", "quarterlyBasicEPS"):
        for item in series[kind]:
            item["currencyCode"] = "USD"
    for kind in ("annualDilutedAverageShares", "quarterlyDilutedAverageShares"):
        for item in series[kind]:
            item["currencyCode"] = "USD"


BANK_RATIOS = {
    "oi": 0.42, "tax": 0.21, "ocf": 0.10, "capex": 0.0, "div": 0.08, "buyback": 0.10,
    "equity": 340 * BN, "debt": 500 * BN, "cash": 600 * BN, "assets": 4000 * BN,
}
BANK_ANNUAL = [
    ("2022-12-31", 128 * BN, 2.97e9),
    ("2023-12-31", 154 * BN, 2.92e9),
    ("2024-12-31", 170 * BN, 2.86e9),
    ("2025-12-31", 180 * BN, 2.80e9),
]
BANK_QUARTERLY = [
    ("2025-03-31", 45 * BN, 2.82e9),
    ("2025-06-30", 45 * BN, 2.81e9),
    ("2025-09-30", 45 * BN, 2.80e9),
    ("2025-12-31", 45 * BN, 2.79e9),
]


def bank_tweak(series):
    # Requested types without data come back as meta-only entries
    for kind in ("annualGrossProfit", "annualCostOfRevenue", "annualOperatingIncome",
                 "quarterlyGrossProfit", "annualCapitalExpenditure", "quarterlyCapitalExpenditure"):
        series[kind] = None


# Calendar-year reporter whose "quarterly" series are year-to-date running totals
YTD_RATIOS = dict(TW_RATIOS, equity=500 * BN, debt=20 * BN, cash=200 * BN, assets=800 * BN)
YTD_ANNUAL = [
    ("2023-12-31", 433 * BN, 1.6e9),
    ("2024-12-31", 530 * BN, 1.6e9),
    ("2025-12-31", 600 * BN, 1.6e9),
]
YTD_QUARTERLY = [
    ("2025-03-31", 140 * BN, 1.6e9),
    ("2025-06-30", 290 * BN, 1.6e9),
    ("2025-09-30", 445 * BN, 1.6e9),
    ("2025-12-31", 600 * BN, 1.6e9),
    ("2026-03-31", 160 * BN, 1.6e9),
]

# 52/53-week fiscal year ending late January; a 10-for-1 split on 2026-06-10
SPLIT_RATIOS = dict(US_RATIOS, equity=80 * BN, debt=10 * BN, cash=40 * BN, assets=120 * BN)
SPLIT_ANNUAL = [
    ("2023-01-29", 27 * BN, 2.50e9),
    ("2024-01-28", 61 * BN, 2.49e9),
    ("2025-01-26", 130 * BN, 2.47e9),
    ("2026-01-25", 200 * BN, 2.45e9),
]
SPLIT_QUARTERLY = [
    ("2025-04-27", 44 * BN, 2.46e9),
    ("2025-07-27", 47 * BN, 2.455e9),
    ("2025-10-26", 52 * BN, 2.45e9),
    ("2026-01-25", 57 * BN, 2.445e9),
    ("2026-04-26", 60 * BN, 2.44e9),
]
SPLIT_NEW_QUARTER = ("2026-07-26", 66 * BN, 24.35e9)   # first report after the split


def times_ten(rows):
    return [(end, revenue, shares * 10) for end, revenue, shares in rows]


def split_chart(splits):
    events = {}
    for timestamp, numerator, denominator in splits:
        events[str(timestamp)] = {
            "date": timestamp,
            "numerator": numerator,
            "denominator": denominator,
            "splitRatio": f"{numerator:g}:{denominator:g}",
        }
    return {
        "_synthetic": SYNTHETIC,
        "chart": {
            "result": [{
                "meta": {"symbol": "SPLT", "currency": "USD", "dataGranularity": "1mo", "range": "max",
                         "firstTradeDate": 946684800},
                "timestamp": [946684800, 1792108800],
                "events": {"splits": events},
                "indicators": {"quote": [{"close": [100.0]}]},
            }],
            "error": None,
        },
    }


def payloads():
    return {
        "yahoo_us_full.json": build("AAPL", US_ANNUAL, US_QUARTERLY, US_RATIOS, "USD", us_full_tweak),
        "yahoo_us_prev.json": build("AAPL", US_PREV_ANNUAL, US_PREV_QUARTERLY, US_RATIOS, "USD"),
        "yahoo_us_restated.json": build(
            "AAPL", US_ANNUAL, US_QUARTERLY, US_RATIOS, "USD", us_restated_tweak
        ),
        "yahoo_tw_2330.json": build("2330.TW", TW_ANNUAL, TW_QUARTERLY, TW_RATIOS, "TWD", tw_tweak),
        "yahoo_foreign_tsm.json": build("TSM", TW_ANNUAL, TW_QUARTERLY, TW_RATIOS, "TWD", foreign_tweak),
        "yahoo_bank.json": build("JPM", BANK_ANNUAL, BANK_QUARTERLY, BANK_RATIOS, "USD", bank_tweak),
        "yahoo_ytd.json": build("2454.TW", YTD_ANNUAL, YTD_QUARTERLY, YTD_RATIOS, "TWD"),
        "yahoo_split_before.json": build("SPLT", SPLIT_ANNUAL, SPLIT_QUARTERLY, SPLIT_RATIOS, "USD"),
        # Yahoo reports the new quarter on the new share count but has not restated older periods
        "yahoo_split_after_unadjusted.json": build(
            "SPLT", SPLIT_ANNUAL, SPLIT_QUARTERLY[1:] + [SPLIT_NEW_QUARTER], SPLIT_RATIOS, "USD"
        ),
        # Yahoo restated every period it still returns (the oldest year / quarter fell out)
        "yahoo_split_after_adjusted.json": build(
            "SPLT", times_ten(SPLIT_ANNUAL[1:]), times_ten(SPLIT_QUARTERLY[1:]) + [SPLIT_NEW_QUARTER],
            SPLIT_RATIOS, "USD",
        ),
        # 2026-06-10 13:30 UTC 10:1; a 2019 11:10 (stock-dividend-sized) event that is ignored
        "yahoo_chart_split.json": split_chart([(1781098200, 10.0, 1.0), (1560173400, 11.0, 10.0)]),
        "yahoo_empty.json": {
            "_synthetic": SYNTHETIC,
            "timeseries": {
                "result": [
                    {"meta": {"symbol": ["XYZ"], "type": ["annualTotalRevenue"]}},
                    {"meta": {"symbol": ["XYZ"], "type": ["quarterlyNetIncome"]}, "timestamp": None},
                ],
                "error": None,
            },
        },
        "yahoo_garbled.json": {
            "_synthetic": SYNTHETIC,
            "timeseries": {
                "result": [
                    None,
                    5,
                    {"meta": None},
                    {"meta": {"type": "annualTotalRevenue"}, "annualTotalRevenue": [{"asOfDate": "2025-12-31"}]},
                    {"meta": {"type": ["annualTotalRevenue"]}, "annualTotalRevenue": {"asOfDate": "x"}},
                    {"meta": {"type": ["annualNetIncome"]}, "annualNetIncome": [
                        {"asOfDate": 20251231, "periodType": "12M", "reportedValue": {"raw": 5}},
                        {"asOfDate": "2025-12-31", "periodType": "12M", "reportedValue": {"raw": float("nan")}},
                        {"asOfDate": "2025-12-31", "periodType": "12M", "reportedValue": {"raw": True}},
                        {"asOfDate": "2025-12-31", "periodType": "12M", "reportedValue": None},
                        {"asOfDate": "2099-12-31", "periodType": "12M", "reportedValue": {"raw": 5}},
                    ]},
                ],
                "error": None,
            },
        },
    }


def dump_payload(value):
    """One series per line: diff-friendly and much smaller than an indented dump."""
    if "timeseries" not in value:
        return json.dumps(value, indent=1, allow_nan=True) + "\n"
    rows = [json.dumps(item, allow_nan=True) for item in value["timeseries"]["result"]]
    head = json.dumps({"_synthetic": value["_synthetic"]})[:-1]
    return (
        head + ',"timeseries":{"error":null,"result":[\n' + ",\n".join(rows) + "\n]}}\n"
    )


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as file:
        return json.load(file)


def samples():
    """build_financials.py output for the frontend fixtures, from the synthetic payloads above."""
    sys.path.insert(0, os.path.join(ROOT, "atlas", "scripts"))
    import build_financials as fin

    first, _ = fin.update_document(None, load("yahoo_us_prev.json"), "AAPL", datetime(2025, 8, 1, tzinfo=timezone.utc))
    aapl, _ = fin.update_document(first, load("yahoo_us_full.json"), "AAPL", SAMPLE_NOW)
    tsmc, _ = fin.update_document(None, load("yahoo_tw_2330.json"), "2330.TW", SAMPLE_NOW)
    note = (
        "SYNTHETIC sample generated by tests/financials_synth.py: made-up numbers, real contract of "
        "docs/report/financials/<KEY>.json"
    )
    return {
        "sample-AAPL.json": {"_fixture": note, **aapl},
        "sample-2330.TW.json": {"_fixture": note, **tsmc},
    }


def dump_sample(value):
    return json.dumps(value, ensure_ascii=False, indent=1) + "\n"


def main():
    os.makedirs(FIXTURES, exist_ok=True)
    for name, value in payloads().items():
        with open(os.path.join(FIXTURES, name), "w", encoding="utf-8") as file:
            file.write(dump_payload(value))
    if "--payloads-only" not in sys.argv:
        for name, value in samples().items():
            with open(os.path.join(FIXTURES, name), "w", encoding="utf-8") as file:
                file.write(dump_sample(value))
    print(f"wrote {FIXTURES}")


if __name__ == "__main__":
    main()
