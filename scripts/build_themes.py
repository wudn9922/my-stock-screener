"""
Builds docs/report/themes.json: US theme rotation (等權題材報酬、相對 SPY、寬度、動能排名).

  python scripts/build_themes.py
  Env: THEMES_OUT_PATH (default docs/report/themes.json)

Runs in GitHub Actions after the daily report (Yahoo Finance via yfinance, 1 year of daily bars for
every constituent, the reference ETFs and SPY). build_payload() is a pure function over already
downloaded prices so it can be tested offline. Failure keeps the previous themes.json (exit 1).

Prices are plain `{symbol: [(YYYY-MM-DD, close), ...]}` lists in ascending date order.
"""
import json
import os
import statistics
import sys
import time
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from themes_us import BENCHMARK, PARENTS, THEMES  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "docs", "report", "themes.json")

PERIOD_BARS = {"d1": 1, "w1": 5, "m1": 21, "m3": 63, "m6": 126}
PERIOD_KEYS = ["d1", "w1", "m1", "m3", "m6", "ytd"]
SERIES_POINTS = 126
HIGH_WINDOW = 252
MA_WINDOW = 50
# A constituent whose last bar is this many days older than SPY's last bar is treated as stale.
STALE_DAYS = 3
# Theme needs at least this many valid constituents to be ranked.
MIN_VALID = 3
# Below this share of tickers with data the old file is kept.
MIN_COVERAGE = 0.6
BATCH_SIZE = 40
# Momentum score weights: w1, m1, m3 and 3-month strength relative to SPY.
MOMENTUM_WEIGHTS = {"w1": 0.2, "m1": 0.25, "m3": 0.3, "rs3m": 0.25}


class InsufficientData(Exception):
    pass


def default_config():
    return {"parents": PARENTS, "themes": THEMES, "benchmark": BENCHMARK}


def round2(value):
    if value is None:
        return None
    return round(float(value), 2) + 0.0


def parse_date(text):
    return date.fromisoformat(text)


def align(bars, calendar, last_date):
    """Closes on the benchmark calendar: forward-filled after the first bar, None before it."""
    by_date = {day: close for day, close in bars if day <= last_date}
    out = []
    current = None
    for day in calendar:
        if day in by_date:
            current = by_date[day]
        out.append(current)
    return out


def period_return(values, bars_back):
    if len(values) <= bars_back:
        return None
    start, end = values[-1 - bars_back], values[-1]
    if start is None or end is None or start <= 0:
        return None
    return (end / start - 1) * 100


def ytd_return(values, calendar):
    year_start = f"{calendar[-1][:4]}-01-01"
    base = None
    for index in range(len(calendar) - 1, -1, -1):
        if calendar[index] < year_start:
            base = values[index]
            break
    end = values[-1]
    if base is None or end is None or base <= 0:
        return None
    return (end / base - 1) * 100


def all_returns(values, calendar):
    result = {key: period_return(values, bars) for key, bars in PERIOD_BARS.items()}
    result["ytd"] = ytd_return(values, calendar)
    return result


def relative_strength(theme_return, bench_return):
    if theme_return is None or bench_return is None:
        return None
    return ((1 + theme_return / 100) / (1 + bench_return / 100) - 1) * 100


def mean(values):
    return sum(values) / len(values) if values else None


def constituent_metrics(values, calendar, bench_returns):
    returns = all_returns(values, calendar)
    window = values[-MA_WINDOW:]
    above = None
    if len(window) == MA_WINDOW and all(v is not None for v in window):
        above = values[-1] > sum(window) / MA_WINDOW
    history = [v for v in values[-HIGH_WINDOW:] if v is not None]
    from_high = None
    if len(history) >= 60 and values[-1] is not None:
        from_high = min(0.0, (values[-1] / max(history) - 1) * 100)
    return {
        "close": values[-1],
        "returns": returns,
        "rs3m": relative_strength(returns["m3"], bench_returns["m3"]),
        "aboveMa50": above,
        "fromHi52": from_high,
    }


def equal_weight_series(members, point_count):
    """Daily-rebalanced equal-weight index (start 100) over the last point_count aligned closes."""
    series = [100.0]
    for offset in range(-point_count + 1, 0):
        daily = []
        for values in members:
            before, after = values[offset - 1], values[offset]
            if before is not None and after is not None and before > 0:
                daily.append(after / before - 1)
        series.append(series[-1] * (1 + (sum(daily) / len(daily) if daily else 0.0)))
    return series


def momentum_score(returns, rs_m3):
    parts = {"w1": returns["w1"], "m1": returns["m1"], "m3": returns["m3"], "rs3m": rs_m3}
    if any(value is None for value in parts.values()):
        return None
    return sum(MOMENTUM_WEIGHTS[key] * parts[key] for key in MOMENTUM_WEIGHTS)


def rounded_returns(returns):
    return {key: round2(returns.get(key)) for key in PERIOD_KEYS}


def build_payload(prices, config, today):
    """Theme rotation payload from downloaded prices. Raises InsufficientData instead of guessing."""
    bench_symbol = config.get("benchmark", BENCHMARK)
    bench_bars = prices.get(bench_symbol) or []
    if len(bench_bars) < PERIOD_BARS["m6"] + 1:
        raise InsufficientData(f"{bench_symbol} history too short ({len(bench_bars)} bars)")
    calendar = [day for day, _ in bench_bars]
    last_date = calendar[-1]
    last_day = parse_date(last_date)

    constituent_symbols = sorted({t[0] for theme in config["themes"] for t in theme["tickers"]})
    with_data = [s for s in constituent_symbols if prices.get(s)]
    coverage = len(with_data) / len(constituent_symbols) if constituent_symbols else 0.0
    if coverage < MIN_COVERAGE:
        raise InsufficientData(
            f"only {len(with_data)}/{len(constituent_symbols)} constituents have data ({coverage:.0%})"
        )

    bench_values = align(bench_bars, calendar, last_date)
    bench_returns = all_returns(bench_values, calendar)
    dates = calendar[-SERIES_POINTS:]

    def usable(symbol):
        bars = prices.get(symbol) or []
        if not bars:
            return None, "missing"
        if (last_day - parse_date(bars[-1][0])).days > STALE_DAYS:
            return None, "stale"
        values = align(bars, calendar, last_date)
        return (values if values[-1] is not None else None), "missing"

    cache = {}
    skipped = {"missing": [], "stale": []}
    for symbol in constituent_symbols:
        values, reason = usable(symbol)
        cache[symbol] = values
        if values is None:
            skipped[reason].append(symbol)

    themes = []
    for theme in config["themes"]:
        members = []
        for symbol, name, note in theme["tickers"]:
            values = cache.get(symbol)
            if values is None:
                continue
            metrics = constituent_metrics(values, calendar, bench_returns)
            members.append((symbol, name, note, values, metrics))
        per_period = {key: [m[4]["returns"][key] for m in members if m[4]["returns"][key] is not None] for key in PERIOD_KEYS}
        mean_returns = {key: mean(per_period[key]) for key in PERIOD_KEYS}
        median_returns = {key: (statistics.median(per_period[key]) if per_period[key] else None) for key in PERIOD_KEYS}
        rs = {
            "m1": relative_strength(mean_returns["m1"], bench_returns["m1"]),
            "m3": relative_strength(mean_returns["m3"], bench_returns["m3"]),
        }
        flags = [m[4]["aboveMa50"] for m in members if m[4]["aboveMa50"] is not None]
        breadth = (sum(1 for f in flags if f) / len(flags) * 100) if flags else None
        valid_count = len(members)
        momentum = momentum_score(mean_returns, rs["m3"]) if valid_count >= MIN_VALID else None
        etf = None
        if theme.get("etf"):
            etf_values, _ = usable(theme["etf"])
            if etf_values is not None:
                etf = {"symbol": theme["etf"], "returns": rounded_returns(all_returns(etf_values, calendar))}
        series = equal_weight_series([m[3] for m in members], SERIES_POINTS) if members else None
        ordered = sorted(
            members,
            key=lambda m: (m[4]["returns"]["m1"] is None, -(m[4]["returns"]["m1"] or 0.0)),
        )
        themes.append(
            {
                "key": theme["key"],
                "name": theme["name"],
                "parent": theme["parent"],
                "description": theme["description"],
                "etf": etf,
                "count": len(theme["tickers"]),
                "validCount": valid_count,
                "returns": rounded_returns(mean_returns),
                "median": rounded_returns(median_returns),
                "rs": {"m1": round2(rs["m1"]), "m3": round2(rs["m3"])},
                "breadth50": round2(breadth),
                "momentum": round2(momentum),
                "rank": None,
                "series": [round2(v) for v in series] if series else [],
                "constituents": [
                    {
                        "symbol": symbol,
                        "name": name,
                        "note": note,
                        "close": round(metrics["close"], 2),
                        "returns": rounded_returns(metrics["returns"]),
                        "rs3m": round2(metrics["rs3m"]),
                        "aboveMa50": metrics["aboveMa50"],
                        "fromHi52": round2(metrics["fromHi52"]),
                    }
                    for symbol, name, note, _values, metrics in ordered
                ],
            }
        )

    ranked = sorted((t for t in themes if t["momentum"] is not None), key=lambda t: -t["momentum"])
    for position, theme in enumerate(ranked, start=1):
        theme["rank"] = position
    unranked = [t for t in themes if t["rank"] is None]
    themes = ranked + unranked

    bench_series = equal_weight_series([bench_values], SERIES_POINTS)
    generated = today.astimezone(timezone.utc) if today.tzinfo else today.replace(tzinfo=timezone.utc)
    return {
        "version": 1,
        "generatedAt": generated.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "asOf": last_date,
        "dates": dates,
        "benchmark": {
            "symbol": bench_symbol,
            "returns": rounded_returns(bench_returns),
            "series": [round2(v) for v in bench_series],
        },
        "parents": [dict(parent) for parent in config["parents"]],
        "themes": themes,
        "skipped": {key: sorted(value) for key, value in skipped.items()},
        "coverage": round(coverage, 3),
    }


# --------------------------------------------------------------------------- download


def extract_bars(downloaded, symbol, single):
    """[(YYYY-MM-DD, close)] for one symbol from a yfinance frame (any column layout)."""
    if downloaded is None or getattr(downloaded, "empty", True):
        return []
    columns = downloaded.columns
    try:
        if getattr(columns, "nlevels", 1) > 1:
            if symbol in set(columns.get_level_values(0)):
                series = downloaded[symbol]["Close"]
            elif symbol in set(columns.get_level_values(1)):
                series = downloaded["Close"][symbol]
            else:
                return []
        elif single:
            series = downloaded["Close"]
        else:
            return []
    except KeyError:
        return []
    bars = []
    for index, value in series.dropna().items():
        close = float(value)
        if close > 0 and close == close:
            bars.append((index.strftime("%Y-%m-%d"), close))
    bars.sort()
    return bars


def download_prices(symbols, downloader=None, sleep=time.sleep):
    """Downloads 1y of daily closes in batches; one retry round for symbols that came back empty."""
    if downloader is None:
        import yfinance as yf

        def downloader(batch):
            return yf.download(
                batch,
                period="1y",
                interval="1d",
                progress=False,
                threads=False,
                auto_adjust=False,
                actions=False,
                group_by="ticker",
                timeout=30,
            )

    prices = {}

    def run(pending):
        for start in range(0, len(pending), BATCH_SIZE):
            batch = pending[start : start + BATCH_SIZE]
            downloaded = None
            for attempt in range(1, 4):
                try:
                    downloaded = downloader(batch)
                    break
                except Exception as exc:  # noqa: BLE001 - network errors vary by library version
                    print(f"batch {start // BATCH_SIZE + 1} attempt {attempt} failed: {type(exc).__name__}: {exc}")
                    sleep(5 * attempt)
            for symbol in batch:
                bars = extract_bars(downloaded, symbol, single=len(batch) == 1)
                if bars:
                    prices[symbol] = bars

    run(list(symbols))
    missing = [s for s in symbols if s not in prices]
    if missing:
        print(f"retrying {len(missing)} symbols without data: {' '.join(missing)}")
        run(missing)
    return prices


def write_atomic(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(path + ".tmp", "w", encoding="utf-8") as file:
        file.write(body)
    os.replace(path + ".tmp", path)
    return len(body.encode("utf-8"))


def all_symbols(config):
    symbols = {config.get("benchmark", BENCHMARK)}
    for theme in config["themes"]:
        symbols.update(t[0] for t in theme["tickers"])
        if theme.get("etf"):
            symbols.add(theme["etf"])
    return sorted(symbols)


def main():
    config = default_config()
    out_path = os.environ.get("THEMES_OUT_PATH") or DEFAULT_OUT
    symbols = all_symbols(config)
    print(f"downloading {len(symbols)} symbols")
    prices = download_prices(symbols)
    try:
        payload = build_payload(prices, config, datetime.now(timezone.utc))
    except InsufficientData as exc:
        print(f"themes.json not updated: {exc}")
        return 1
    skipped = payload["skipped"]
    if skipped["missing"] or skipped["stale"]:
        print(f"skipped missing={' '.join(skipped['missing'])} stale={' '.join(skipped['stale'])}")
    size = write_atomic(out_path, payload)
    print(f"wrote {out_path} ({size} bytes, asOf {payload['asOf']}, {len(payload['themes'])} themes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
