"""
Synthetic daily prices for the theme rotation tests and the atlas fixtures.

  python3 tests/themes_synth.py      # rewrites atlas/src/report/fixtures/themes.sample.json (US)
  python3 tests/themes_synth.py tw   # rewrites atlas/src/report/fixtures/themes-tw.sample.json (TW)

Deterministic (seeded). A market factor, one factor per theme and idiosyncratic noise give themes
different trends; a few tickers are missing, stale or newly listed so every payload branch shows up.
The Taiwan fixture uses a small hand-written config below (not scripts/themes_tw.py) and is SYNTHETIC:
real tickers and names, invented prices.
"""
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

# Approximate six-month drift (percent) per theme, to spread the heat map.
THEME_DRIFT = {
    "ai-chips": 55, "memory": 95, "semi-equip": 40, "foundry": 28, "analog-auto": -4,
    "optical": 80, "ai-servers": 35, "power-grid": 45, "nuclear": 30,
    "megacap": 14, "cloud-saas": -16, "cyber": 6, "ai-software": 22, "internet-media": 3, "ecommerce": -2,
    "crypto": -22, "fintech": 5,
    "space": 48, "defense": 18, "drones-evtol": 26, "robotics": 12, "quantum": 62,
    "ev-battery": -12, "solar": 33, "gold-miners": 52, "critical-minerals": 38,
    "biotech": 20, "glp1": -8, "medtech": -6, "china-adr": 9,
}
SPECULATIVE = {"nuclear", "space", "quantum", "crypto", "drones-evtol", "ai-software", "ev-battery", "solar"}


def business_days(end, count):
    days = []
    cursor = date.fromisoformat(end)
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return list(reversed(days))


TW_THEME_DRIFT = {"foundry": 30, "ic-design": 12, "packaging": 24, "ai-servers": 46, "cooling": 58, "pcb": -6, "finance": 8, "shipping": -14}
TW_SPECULATIVE = {"cooling", "shipping"}

TW_PARENTS = [
    {"key": "semis", "name": "半導體", "order": 1},
    {"key": "ai-hardware", "name": "AI伺服器與零組件", "order": 2},
    {"key": "finance", "name": "金融", "order": 3},
    {"key": "traditional", "name": "傳產與航運", "order": 4},
]


def _tw_theme(key, name, parent, description, tickers, etf=None):
    return {
        "key": key,
        "name": name,
        "parent": parent,
        "description": description,
        "etf": etf,
        "tickers": [(symbol, label, "合成範例") for symbol, label in tickers],
    }


def tw_config():
    """Small Taiwan theme set for tests and the fixture; independent of scripts/themes_tw.py."""
    return {
        "market": "tw",
        "benchmark": "^TWII",
        "benchmark_name": "加權指數",
        "parents": TW_PARENTS,
        "themes": [
            _tw_theme(
                "foundry", "晶圓代工", "semis",
                "替晶片設計公司把電路圖做成實體晶片的工廠，製程越先進、接單越多，營收越有想像空間。（合成範例資料）",
                [("2330.TW", "台積電"), ("2303.TW", "聯電"), ("5347.TWO", "世界"), ("6770.TW", "力積電"), ("2337.TW", "旺宏")],
            ),
            _tw_theme(
                "ic-design", "IC設計", "semis",
                "只負責設計晶片、把製造交給代工廠的公司，產品涵蓋手機、電視與網通晶片。（合成範例資料）",
                [("2454.TW", "聯發科"), ("3034.TW", "聯詠"), ("2379.TW", "瑞昱"), ("3035.TW", "智原"), ("4966.TWO", "譜瑞-KY")],
            ),
            _tw_theme(
                "packaging", "封測", "semis",
                "晶片做好之後的封裝與測試工廠，先進封裝是近年 AI 晶片的關鍵環節。（合成範例資料）",
                [("3711.TW", "日月光投控"), ("2449.TW", "京元電子"), ("6239.TW", "力成"), ("6147.TWO", "頎邦"), ("8150.TW", "南茂")],
            ),
            _tw_theme(
                "ai-servers", "AI伺服器", "ai-hardware",
                "替雲端公司組裝與設計 AI 伺服器的廠商，訂單跟著資料中心的建置進度走。（合成範例資料）",
                [("2317.TW", "鴻海"), ("2382.TW", "廣達"), ("3231.TW", "緯創"), ("6669.TW", "緯穎"), ("2356.TW", "英業達"), ("2376.TW", "技嘉")],
            ),
            _tw_theme(
                "cooling", "散熱", "ai-hardware",
                "伺服器功耗越高越需要散熱，這些公司做風扇、散熱模組與均熱片。（合成範例資料）",
                [("3017.TW", "奇鋐"), ("3324.TWO", "雙鴻"), ("2421.TW", "建準"), ("3653.TW", "健策"), ("8996.TW", "高力")],
            ),
            _tw_theme(
                "pcb", "PCB與載板", "ai-hardware",
                "電子產品內部的電路板與晶片載板，是幾乎所有電子設備的基礎零件。（合成範例資料）",
                [("2368.TW", "金像電"), ("3037.TW", "欣興"), ("8046.TW", "南電"), ("3189.TW", "景碩"), ("2313.TW", "華通")],
            ),
            _tw_theme(
                "finance", "金控", "finance",
                "銀行、保險與證券集於一身的金融控股公司，獲利受利率與股市行情影響。（合成範例資料）",
                [("2881.TW", "富邦金"), ("2882.TW", "國泰金"), ("2891.TW", "中信金"), ("2886.TW", "兆豐金"), ("2884.TW", "玉山金"), ("2885.TW", "元大金")],
                etf="0055.TW",
            ),
            _tw_theme(
                "shipping", "航運", "traditional",
                "貨櫃與散裝航運公司，運價起伏大，景氣好壞差距明顯。（合成範例資料）",
                [("2603.TW", "長榮"), ("2609.TW", "陽明"), ("2615.TW", "萬海"), ("2605.TW", "新興"), ("2606.TW", "裕民")],
            ),
        ],
    }


def synthetic_prices(
    config,
    end="2026-10-09",
    bars=252,
    seed=22,
    drop=(),
    stale=(),
    new_listings=None,
    drift=None,
    speculative=None,
    bench_level=450.0,
):
    """{symbol: [(date, close)]}. `drop` symbols are absent, `stale` end 6 days early,
    `new_listings` maps symbol -> number of bars. `drift`, `speculative` and `bench_level` default
    to the US set."""
    drift = THEME_DRIFT if drift is None else drift
    speculative = SPECULATIVE if speculative is None else speculative
    rng = random.Random(seed)
    calendar = business_days(end, bars)
    market = [rng.gauss(0.0004, 0.009) for _ in calendar]
    theme_factor = {}
    for theme in config["themes"]:
        daily_drift = (1 + drift.get(theme["key"], 0) * 0.5 / 100) ** (1 / 126) - 1
        vol = 0.016 if theme["key"] in speculative else 0.008
        theme_factor[theme["key"]] = [rng.gauss(daily_drift, vol) for _ in calendar]
    owner = {}
    etfs = {}
    for theme in config["themes"]:
        for symbol, _name, _note in theme["tickers"]:
            owner.setdefault(symbol, theme["key"])
        if theme.get("etf"):
            etfs[theme["etf"]] = theme["key"]

    def path(symbol, factor, beta, idio):
        price = rng.uniform(25, 420)
        out = []
        for index, day in enumerate(calendar):
            change = beta * market[index] + (factor[index] if factor else 0.0) + rng.gauss(0, idio)
            price = max(1.0, price * (1 + change))
            out.append((day, round(price, 2)))
        return out

    prices = {}
    level = bench_level
    spy = []
    for index, day in enumerate(calendar):
        level *= 1 + market[index]
        spy.append((day, round(level, 2)))
    prices[config["benchmark"]] = spy
    for symbol, key in sorted(owner.items()):
        if symbol in drop:
            continue
        sigma = 0.02 if key in speculative else 0.011
        series = path(symbol, theme_factor[key], rng.uniform(0.8, 1.5), sigma)
        if symbol in (new_listings or {}):
            series = series[-new_listings[symbol]:]
        if symbol in stale:
            series = series[:-4]
        prices[symbol] = series
    for etf, key in sorted(etfs.items()):
        prices[etf] = path(etf, theme_factor[key], 1.0, 0.004)
    return prices


def us_fixture_payload():
    """The US sample payload; tests compare it with the committed fixture byte for byte."""
    import build_themes

    config = build_themes.default_config()
    drop = {"CRCL", "HBM", "MP", "USAR", "UUUU", "LAC", "GLXY"}
    prices = synthetic_prices(
        config,
        drop=drop,
        stale={"AAOI"},
        new_listings={"FLY": 60, "SNDK": 180, "ALAB": 200},
    )
    return build_themes.build_payload(prices, config, datetime(2026, 10, 9, 22, 30, tzinfo=timezone.utc))


def tw_fixture_payload():
    """The Taiwan sample payload (synthetic): one thin theme, one stale name, one new listing."""
    import build_themes

    config = tw_config()
    prices = synthetic_prices(
        config,
        end="2026-10-09",
        seed=7,
        drop={"2605.TW", "2606.TW", "2609.TW"},
        stale={"8150.TW"},
        new_listings={"6770.TW": 120},
        drift=TW_THEME_DRIFT,
        speculative=TW_SPECULATIVE,
        bench_level=22000.0,
    )
    payload = build_themes.build_payload(prices, config, datetime(2026, 10, 9, 10, 30, tzinfo=timezone.utc))
    payload["synthetic"] = True
    return payload


def main(market="us"):
    import build_themes

    if market == "tw":
        payload, name = tw_fixture_payload(), "themes-tw.sample.json"
    else:
        payload, name = us_fixture_payload(), "themes.sample.json"
    out = os.path.join(ROOT, "atlas", "src", "report", "fixtures", name)
    size = build_themes.write_atomic(out, payload)
    print(f"wrote {out} ({size} bytes)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "us")
