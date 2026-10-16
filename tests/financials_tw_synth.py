"""
Synthetic FinMind statements for atlas/scripts/build_financials_tw_finmind.py (no network): one truth per stock
(quarterly singles of the income statement, cash flow and balance sheet), published in the three styles FinMind
might use (single quarters, year-to-date, "Q1-Q3 single, Q4 full year"), in the long-table layout FinMind returns
(date / stock_id / type / value / origin_name). The origin names are MOPS wording; the types are FinMind codes.
"""
import random

ENDS = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
SHARES = 1e6              # shares of the made-up company: EPS = net income / shares (unrounded, as the share basis needs)

PL_ROWS = (
    ("Revenue", "營業收入", "rev"),
    ("CostOfGoodsSold", "營業成本", "cost"),
    ("GrossProfit", "營業毛利（毛損）", "gross"),
    ("OperatingIncome", "營業利益（損失）", "oi"),
    ("PreTaxIncome", "稅前淨利（淨損）", "pretax"),
    ("TAX", "所得稅費用（利益）", "tax"),
    ("TotalNonoperatingIncomeAndExpense", "營業外收入及支出", "nonop"),
    ("IncomeAfterTaxes", "本期淨利（淨損）", "ni_all"),
    ("EquityAttributableToOwnersOfParent", "淨利（淨損）歸屬於母公司業主", "ni"),
    ("EPS", "基本每股盈餘（元）", "eps"),
    ("NoncontrollingInterests", "淨利（淨損）歸屬於非控制權益", None),
)
BS_ROWS = (
    ("TotalAssets", "資產總額", "assets"),
    ("EquityAttributableToOwnersOfParent", "歸屬於母公司業主之權益合計", "equity"),
    ("CashAndCashEquivalents", "現金及約當現金", "cash"),
    ("ShortTermBorrowings", "短期借款", "short"),
    ("LongTermBorrowings", "長期借款", "long"),
    ("BondsPayable", "應付公司債", "bonds"),
    ("Inventories", "存貨", None),
)
CF_ROWS = (
    ("NetCashInflowFromOperatingActivities", "營業活動之淨現金流入（流出）", "ocf"),
    ("AcquisitionOfPropertyPlantAndEquipment", "取得不動產、廠房及設備", "capex"),
    ("CashDividendsPaid", "發放現金股利", "div"),
    ("InterestReceived", "收取之利息", None),
)
FLOWS = ("rev", "cost", "gross", "oi", "pretax", "tax", "nonop", "ni", "ocf", "capex", "div")


def truth(seed, years=range(2015, 2026), extra=((2026, 1), (2026, 2)), finance=False):
    """Quarterly singles {(year, quarter): dict}: growing, seasonal, profitable, positive operating cash flow."""
    rng = random.Random(seed)
    level, margin = rng.uniform(1e6, 5e7), rng.uniform(0.08, 0.25)
    result = {}
    for year, quarter in [(y, q) for y in years for q in (1, 2, 3, 4)] + list(extra):
        level *= rng.uniform(0.97, 1.08)
        rev = round(level * (0.9 + 0.1 * quarter))
        cost = 0.0 if finance else round(rev * rng.uniform(0.55, 0.8))
        gross = 0.0 if finance else rev - cost
        oi = round((gross if not finance else rev) * margin * rng.uniform(0.8, 1.2))
        nonop = round(rng.uniform(-0.02, 0.02) * rev)
        pretax = oi + nonop
        tax = round(max(pretax, 0) * 0.2)
        ni = pretax - tax
        ocf = round(ni * rng.uniform(0.6, 1.4)) + 1
        result[(year, quarter)] = {
            "rev": rev, "cost": cost, "gross": gross, "oi": oi, "pretax": pretax, "tax": tax, "nonop": nonop,
            "ni": ni, "ocf": ocf, "capex": round(rev * rng.uniform(0.02, 0.1)),
            "div": round(ni * 0.5) if quarter in (2, 3) else 0,
            "assets": round(level * 40), "equity": round(level * 25), "cash": round(level * 6),
            "short": round(level * 2), "long": round(level * 3), "bonds": round(level * 1.5),
        }
    return result


def cumulative(quarters, year, quarter, key):
    return sum(quarters[(year, q)][key] for q in range(1, quarter + 1))


def publish(quarters, mode):
    """Single-quarter truth → {(year, quarter): {field: published}} as the statements of style `mode` show them."""
    result = {}
    for (year, quarter), row in quarters.items():
        published = {}
        for key in FLOWS:
            if mode == "quarter" or (mode == "q4annual" and quarter < 4):
                published[key] = row[key]
            else:
                if (year, quarter) and all((year, q) in quarters for q in range(1, quarter + 1)):
                    published[key] = cumulative(quarters, year, quarter, key)
                else:
                    continue
        for key in ("assets", "equity", "cash", "short", "long", "bonds"):
            published[key] = row[key]
        published["eps"] = ((published["ni"]) / SHARES) if mode != "quarter" else row["ni"] / SHARES
        result[(year, quarter)] = published
    return result


def rows_of(code, published, cash_flow_sign=-1):
    """Published values of one stock → {"pl": rows, "bs": rows, "cf": rows} in FinMind's long layout."""
    out = {"pl": [], "bs": [], "cf": []}
    for (year, quarter), values in sorted(published.items()):
        day = f"{year}-{ENDS[quarter]}"
        for type_name, origin, key in PL_ROWS:
            if key is None:
                out["pl"].append(row(day, code, type_name, round(values["ni"] * 0.02, 2), origin))
                continue
            if key == "ni_all":
                value = values["ni"]
            elif key == "eps":
                value = round(values["eps"], 6)
            else:
                if key not in values:
                    continue
                value = values[key]
            out["pl"].append(row(day, code, type_name, value, origin))
        for type_name, origin, key in BS_ROWS:
            if key is None:
                out["bs"].append(row(day, code, type_name, 123456, origin))
            else:
                value = {"assets": values["assets"], "equity": values["equity"], "cash": values["cash"],
                         "short": values["short"], "long": values["long"], "bonds": values["bonds"]}[key]
                out["bs"].append(row(day, code, type_name, value, origin))
        for type_name, origin, key in CF_ROWS:
            if key is None:
                out["cf"].append(row(day, code, type_name, 42, origin))
                continue
            if key not in values:
                continue
            value = values[key]
            if key in ("capex", "div"):
                value = cash_flow_sign * value
            out["cf"].append(row(day, code, type_name, value, origin))
    return out


def row(day, code, type_name, value, origin):
    return {"date": day, "stock_id": code, "type": type_name, "value": value, "origin_name": origin}


def statements(code, mode, seed=None, finance=False, years=range(2015, 2026)):
    """One stock's three datasets in the given style → {"pl": rows, "bs": rows, "cf": rows}."""
    quarters = truth(int(code) if seed is None else seed, years=years, finance=finance)
    return rows_of(code, publish(quarters, mode)), quarters
