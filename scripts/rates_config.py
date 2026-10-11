"""
Static data for the long-Treasury ETFs on the US rates research page (scripts/build_rates.py).

Every number here was typed in by hand from issuer pages as remembered on STATIC_AS_OF; none of it was
checked against the live pages, so every entry carries verified=False and the UI shows "待核對".
Update the numbers, staticAsOf and verified together after checking the issuer page.

Units: expenseRatio in percent per year, effectiveDuration in years (issuer-published, unlevered),
inception as YYYY-MM-DD. Convexity is deliberately NOT stored: it is computed from the model ladder
(scripts/rates_analytics.py) because published convexity figures are low-confidence and come in a
different unit (iShares shows e.g. 3.6, which is 360 years² in this project's unit).

UNCERTAIN NUMBERS (check these first):
- TLT  effectiveDuration 16.5: changes daily on the issuer page; recent range roughly 16-17 years.
- EDV  expenseRatio 0.05: may still be 0.06 (Vanguard fee change date not confirmed).
- EDV  effectiveDuration 24.5: approximate (range roughly 24-25 years).
- ZROZ effectiveDuration 26.5: approximate (range roughly 26-27 years); source URL path not confirmed.
- GOVZ inception 2020-09-22: only the month (September 2020) is fairly certain.
- GOVZ expenseRatio 0.10: not confirmed; effectiveDuration 26.5 approximate; source URL product id
  not confirmed.
- TMF  expenseRatio 1.01: net vs gross not confirmed (gross roughly 1.0-1.1%); the swap financing cost
  is not part of the expense ratio. Index switched to ICE U.S. Treasury 20+ Year (date not confirmed).
- TMF  effectiveDuration: not published as a single fund number; left null (the model value of the
  underlying index is used, scenarios apply leverage 3 separately).
- holdings descriptions are qualitative only.
"""

STATIC_AS_OF = "2026-10-11"

# Model proxies (keys of rates_analytics.MODEL_PROXIES): equal-par ladders priced off today's
# DGS20/DGS30 with linear interpolation and semiannual compounding.
ETFS = [
    {
        "symbol": "TLT",
        "name": "iShares 20+ Year Treasury Bond ETF",
        "issuer": "iShares (BlackRock)",
        "index": "ICE U.S. Treasury 20+ Year Index",
        "inception": "2002-07-22",
        "leverage": 1,
        "expenseRatio": 0.15,
        "expenseNote": "管理費 0.15%（待核對）",
        "effectiveDuration": 16.5,
        "maturityRange": "20 年以上",
        "holdings": "剩餘年限 20 年以上的美國公債（附息）",
        "staticAsOf": STATIC_AS_OF,
        "staticSource": "https://www.ishares.com/us/products/239454/ishares-20-year-treasury-bond-etf",
        "verified": False,
        "modelProxy": "par-20-30",
    },
    {
        "symbol": "EDV",
        "name": "Vanguard Extended Duration Treasury ETF",
        "issuer": "Vanguard",
        "index": "Bloomberg US Treasury STRIPS 20-30 Year Equal Par Bond Index",
        "inception": "2007-12-06",
        "leverage": 1,
        "expenseRatio": 0.05,
        "expenseNote": "0.05%（也可能仍是 0.06%，待核對）",
        "effectiveDuration": 24.5,
        "maturityRange": "20–30 年",
        "holdings": "20–30 年美國公債本金與利息分離零息債（STRIPS），等面額",
        "staticAsOf": STATIC_AS_OF,
        "staticSource": "https://investor.vanguard.com/investment-products/etfs/profile/edv",
        "verified": False,
        "modelProxy": "zero-20-30",
    },
    {
        "symbol": "GOVZ",
        "name": "iShares 25+ Year Treasury STRIPS Bond ETF",
        "issuer": "iShares (BlackRock)",
        "index": "ICE BofA Long US Treasury Principal STRIPS Index",
        "inception": "2020-09-22",
        "leverage": 1,
        "expenseRatio": 0.10,
        "expenseNote": "0.10%（費用率與成立日待核對）",
        "effectiveDuration": 26.5,
        "maturityRange": "25 年以上",
        "holdings": "剩餘年限 25 年以上的美國公債本金零息債（Principal STRIPS）",
        "staticAsOf": STATIC_AS_OF,
        "staticSource": "https://www.ishares.com/us/products/317316/ishares-25-year-treasury-strips-bond-etf",
        "verified": False,
        "modelProxy": "zero-25-30",
    },
    {
        "symbol": "ZROZ",
        "name": "PIMCO 25+ Year Zero Coupon U.S. Treasury Index ETF",
        "issuer": "PIMCO",
        "index": "ICE BofA Long US Treasury Principal STRIPS Index",
        "inception": "2009-10-30",
        "leverage": 1,
        "expenseRatio": 0.15,
        "expenseNote": "0.15%（待核對）",
        "effectiveDuration": 26.5,
        "maturityRange": "25 年以上",
        "holdings": "剩餘年限 25 年以上的美國公債本金零息債（Principal STRIPS）",
        "staticAsOf": STATIC_AS_OF,
        "staticSource": "https://www.pimco.com/us/en/investments/etf/25-year-zero-coupon-us-treasury-index-exchange-traded-fund/etf-shares",
        "verified": False,
        "modelProxy": "zero-25-30",
    },
    {
        "symbol": "TMF",
        "name": "Direxion Daily 20+ Year Treasury Bull 3X Shares",
        "issuer": "Direxion",
        "index": "ICE U.S. Treasury 20+ Year Index（每日 3 倍）",
        "inception": "2009-04-16",
        "leverage": 3,
        "expenseRatio": 1.01,
        "expenseNote": "約 1.0–1.1%（毛／淨費用率待核對）；交換合約的融資成本不含在費用率內",
        "effectiveDuration": None,
        "maturityRange": "20 年以上（透過交換合約取得 3 倍曝險）",
        "holdings": "美國公債、TLT 等 ETF 與總報酬交換合約；每日重設 3 倍槓桿",
        "staticAsOf": STATIC_AS_OF,
        "staticSource": "https://www.direxion.com/product/daily-20-year-treasury-bull-bear-3x-etfs",
        "verified": False,
        "modelProxy": "par-20-30",
    },
]


def validate_config(etfs, proxies=None):
    """Returns a list of problems (empty when the static ETF table is usable)."""
    import re
    from datetime import date

    def valid_date(text):
        try:
            date.fromisoformat(text)
            return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", text))
        except (TypeError, ValueError):
            return False

    problems = []
    seen = set()
    for etf in etfs:
        symbol = etf.get("symbol")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z]{2,5}", symbol):
            problems.append(f"{symbol}: bad symbol")
        if symbol in seen:
            problems.append(f"{symbol}: duplicate symbol")
        seen.add(symbol)
        for key in ("name", "issuer", "index", "expenseNote", "maturityRange", "holdings"):
            if not isinstance(etf.get(key), str) or not etf[key].strip():
                problems.append(f"{symbol}: missing {key}")
        if etf.get("leverage") not in (1, 3):
            problems.append(f"{symbol}: leverage must be 1 or 3")
        ratio = etf.get("expenseRatio")
        if not isinstance(ratio, (int, float)) or not 0 <= ratio <= 2:
            problems.append(f"{symbol}: expenseRatio outside 0-2%")
        duration = etf.get("effectiveDuration")
        if duration is not None and (not isinstance(duration, (int, float)) or not 5 <= duration <= 60):
            problems.append(f"{symbol}: effectiveDuration outside 5-60 years")
        if duration is None and etf.get("leverage") == 1:
            problems.append(f"{symbol}: unlevered ETF needs effectiveDuration")
        for key in ("inception", "staticAsOf"):
            if not valid_date(etf.get(key)):
                problems.append(f"{symbol}: bad {key}")
        source = etf.get("staticSource")
        if not isinstance(source, str) or not re.fullmatch(r"https://[A-Za-z0-9.-]+\.[a-z]{2,}/\S*", source):
            problems.append(f"{symbol}: staticSource must be an https URL")
        if etf.get("verified") is not False:
            problems.append(f"{symbol}: verified must stay False until checked by hand")
        if proxies is not None and etf.get("modelProxy") not in proxies:
            problems.append(f"{symbol}: unknown modelProxy")
    return problems
