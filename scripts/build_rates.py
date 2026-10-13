"""
Builds docs/report/rates.json and docs/report/rates-history.json for the US rates page: the 10Y and 3M
Treasury yields, the Fed target range, the 10Y-Fed and 10Y-3M spreads and the static table of five
long-Treasury ETFs (TLT, EDV, GOVZ, ZROZ, TMF) with duration / convexity from a model ladder.

  python scripts/build_rates.py
  Env: RATES_OUT_DIR (default docs/report), FRED_API_KEY (optional, fallback only)

Sources, per series: FRED API (only when FRED_API_KEY is set) -> FRED fredgraph.csv (no key) -> the
values already in the previous rates-history.json (reported in `missing`). Messages only ever name
the series and the HTTP status: URLs and exception texts are never printed because the API URL
carries api_key. No ETF prices are downloaded: the page only needs the ETFs' static fields and the
model duration / convexity, which come from today's DGS20 / DGS30.

Nothing is written (old files kept, exit 1) when one of DGS3MO / DGS10 / DGS20 / DGS30 is not fresh or
the latest DGS10 is more than 10 days old. build_payloads() is a pure function so the whole payload
can be tested offline (tests/test_rates.py).

Units: yields %, spreads bp (int), durations years, convexity years² (ΔP/P ≈ −D·Δy + ½·C·Δy²,
Δy decimal; an iShares-published 3.6 is 360 here).
"""
import csv
import io
import json
import math
import os
import sys
import time
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rates_analytics as ra  # noqa: E402
from rates_config import ETFS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT_DIR = os.path.join(ROOT, "docs", "report")
RATES_FILE = "rates.json"
HISTORY_FILE = "rates-history.json"
VERSION = 1

# Only the tenors the page reads: 3M (10Y-3M spread), 10Y (headline and spreads), 20Y / 30Y (model ladders).
TENOR_IDS = ["DGS3MO", "DGS10", "DGS20", "DGS30"]
TENORS = ["3M", "10Y", "20Y", "30Y"]
TENOR_YEARS = [0.25, 10, 20, 30]
POLICY_IDS = ["DFEDTARU", "DFEDTARL"]
FRED_IDS = TENOR_IDS + POLICY_IDS
# DGS20 / DGS30 only feed the model; the page charts just these.
HISTORY_IDS = ["DGS3MO", "DGS10", "DFEDTARU", "DFEDTARL"]
FRESH = ("csv", "api")
MAX_STALE_DAYS = 10
HISTORY_START = "2003-01-01"
DAILY_POINTS = 260
# (key, long, short): internally in bp. 10Y-Fed is derived by the page from the 10Y yield and the policy range.
SPREADS = [
    ("10y3m", "DGS10", "DGS3MO"),
]

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
HTTP_TIMEOUT = (10, 20)
FETCH_ATTEMPTS = 2
# A source that failed this many series in a row is skipped for the rest of the run.
BREAKER_LIMIT = 3
# Wall-clock budget for all FRED requests; series left over fall back on the previous file.
FETCH_BUDGET_SECONDS = 420
REQUEST_PAUSE = 0.3
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
SOURCE_LABELS = {"csv": "FRED fredgraph.csv", "api": "FRED API"}

NOTES = {
    "rates": "殖利率來自 FRED（美國財政部 H.15 每日固定期限殖利率，約美東 15:30 取價）；有 FRED_API_KEY 時以 FRED API 優先、免金鑰 CSV 為備援（沒有金鑰就只用 CSV），"
             "都失敗沿用上次檔案的數值並列在 missing。10Y−3M 由殖利率相減，10Y−Fed 預設使用政策目標區間上限。",
    "spreads": "利差畫面以百分點表示。10Y−Fed 預設為 10 年殖利率減聯準會目標區間上限，"
               "可切換下限；並非有效聯邦基金利率。目標區間序列自 2008 年底才有。"
               "10Y−3M 為 10 年殖利率減 3 個月殖利率；倒掛＝差值小於 0。"
               "相隔 10 個交易日內的倒掛視為同一段；pct5y 為近 5 年百分位。倒掛不是衰退時間表。",
    "model": "模型存續期間與凸性以今日 DGS20／DGS30 線性內插、半年複利計算的等面額債券梯形為代理（TLT 與 TMF＝20–30 年平價附息債；"
             "EDV＝20–30 年零息債；ZROZ、GOVZ＝25–30 年零息債）。凸性單位為「年²」（ΔP/P ≈ −D·Δy + ½·C·Δy²，Δy 用小數）；"
             "iShares 網站公布的凸性（例如 3.6）要 ×100 才是這裡的單位。TMF 的模型值是未槓桿的標的，情境計算時再乘 3。",
    "static": "名稱、指數、成立日、費用率、公布存續期間等靜態資料為手動輸入（staticAsOf），全部尚未核對（verified=false），請以發行商網站為準。",
    "tmf": "TMF 為每日重設的 3 倍槓桿商品：長期報酬不等於 3 倍 TLT，存續期間情境只適用單日的瞬間變動。",
    "disclaimer": "僅供研究與教育用途，不構成投資建議。",
}


class InsufficientData(Exception):
    pass


class FetchError(Exception):
    """A sanitized fetch failure: the message names only the series and a status / exception type."""


def round_or_none(value, digits):
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits) + 0.0


# --------------------------------------------------------------------------- FRED


def parse_fred_csv(text, series_id):
    """[(date, value)] from fredgraph.csv; header 'DATE' or 'observation_date'; '.' / empty are missing."""
    text = (text or "").lstrip("﻿")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ValueError("empty csv")
    header = [cell.strip() for cell in rows[0]]
    if len(header) < 2 or header[0].lower() not in ("date", "observation_date") or header[1] != series_id:
        raise ValueError("unexpected csv header")
    points = {}
    for row in rows[1:]:
        if len(row) < 2:
            continue
        point = _observation(row[0], row[1])
        if point:
            points[point[0]] = point[1]
    if not points:
        raise ValueError("no observations")
    return sorted(points.items())


def parse_fred_api(payload):
    """[(date, value)] from a FRED API series/observations JSON body."""
    observations = payload.get("observations") if isinstance(payload, dict) else None
    if not isinstance(observations, list):
        raise ValueError("no observations field")
    points = {}
    for item in observations:
        if isinstance(item, dict):
            point = _observation(item.get("date"), item.get("value"))
            if point:
                points[point[0]] = point[1]
    if not points:
        raise ValueError("no observations")
    return sorted(points.items())


def _observation(day, raw):
    day = (day or "").strip()
    raw = (raw or "").strip()
    try:
        parsed = date.fromisoformat(day)
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or len(day) != 10:
        return None
    return parsed.isoformat(), value


def default_http_get():
    import logging

    import requests

    # urllib3 warnings can quote the request URL (and with it api_key)
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    def http_get(url, params, timeout):
        response = session.get(url, params=params, timeout=timeout)
        return response.status_code, response.text

    return http_get


def make_fetchers(http_get, api_key, sleep=time.sleep, clock=time.monotonic, budget=None):
    """[(origin, fetch(series_id) -> points)] in the order they are tried. fetch raises FetchError.

    The API (when a key is set) goes first: it answers reliably from CI runners, while fredgraph.csv can hang
    there. A source that fails BREAKER_LIMIT series in a row is skipped from then on, and no request starts
    after `budget` seconds (measured from this call) have passed.
    """
    deadline = None if budget is None else clock() + budget
    failures = {}

    def attempt(series_id, label, call, parse):
        if failures.get(label, 0) >= BREAKER_LIMIT:
            raise FetchError(f"{series_id}: {label} skipped after {BREAKER_LIMIT} failed series")
        problem = None
        for number in range(1, FETCH_ATTEMPTS + 1):
            if deadline is not None and clock() > deadline:
                raise FetchError(f"{series_id}: {label} time budget used up")
            sleep(REQUEST_PAUSE if number == 1 else 3 * number)
            try:
                status, body = call()
            except Exception as exc:  # noqa: BLE001 - requests puts the URL (with api_key) in the message
                problem = type(exc).__name__
                print(f"{series_id}: {label} attempt {number} failed ({problem})", flush=True)
                continue
            if status == 200:
                try:
                    points = parse(body)
                except Exception as exc:  # noqa: BLE001 - malformed body; never echo it
                    problem = f"unparseable response ({type(exc).__name__})"
                    print(f"{series_id}: {label} {problem}", flush=True)
                    break
                failures[label] = 0
                return points
            problem = f"HTTP {status}"
            print(f"{series_id}: {label} attempt {number} failed ({problem})", flush=True)
            if status not in (429,) and status < 500:
                break
        failures[label] = failures.get(label, 0) + 1
        if failures[label] == BREAKER_LIMIT:
            print(f"{label}: {BREAKER_LIMIT} series failed in a row, skipping it from now on", flush=True)
        raise FetchError(f"{series_id}: {label} {problem}")

    def fetch_csv(series_id):
        params = {"id": series_id, "cosd": HISTORY_START}
        return attempt(
            series_id, "fredgraph.csv", lambda: http_get(FRED_CSV_URL, params, HTTP_TIMEOUT),
            lambda body: parse_fred_csv(body, series_id),
        )

    def fetch_api(series_id):
        params = {"series_id": series_id, "api_key": api_key, "file_type": "json", "observation_start": HISTORY_START}
        return attempt(
            series_id, "FRED API", lambda: http_get(FRED_API_URL, params, HTTP_TIMEOUT),
            lambda body: parse_fred_api(json.loads(body)),
        )

    fetchers = [("api", fetch_api)] if api_key else []
    fetchers.append(("csv", fetch_csv))
    return fetchers


def load_series(ids, fetchers, previous):
    """({id: points}, {id: "csv"|"api"|"previous"|None}). Every series falls back on its own."""
    series, origin = {}, {}
    for series_id in ids:
        origin[series_id] = None
        for name, fetch in fetchers:
            try:
                series[series_id] = fetch(series_id)
            except FetchError:
                continue
            origin[series_id] = name
            break
        if origin[series_id] is None and previous.get(series_id):
            series[series_id] = list(previous[series_id])
            origin[series_id] = "previous"
            print(f"{series_id}: using values from the previous file")
        elif origin[series_id] is None:
            print(f"{series_id}: no data")
    return series, origin


def series_from_history(history):
    """{id: [(date, value)]} rebuilt from a previous rates-history.json (weekly + daily, daily wins)."""
    out = {}
    if not isinstance(history, dict):
        return out
    merged = {}
    for block in ("weekly", "daily"):
        part = history.get(block) or {}
        dates = part.get("dates") or []
        for series_id, values in (part.get("series") or {}).items():
            target = merged.setdefault(series_id, {})
            for day, value in zip(dates, values or []):
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    target[day] = float(value)
    for series_id, points in merged.items():
        if points:
            out[series_id] = sorted(points.items())
    return out


# --------------------------------------------------------------------------- payloads


def fresh_series(series, origin):
    """Series usable for the payload: anything fetched now or kept from the previous file, from 2003 on."""
    out = {}
    for series_id, points in series.items():
        if origin.get(series_id) is None or not points:
            continue
        out[series_id] = ra.Series([(d, v) for d, v in points if d >= HISTORY_START])
    return out


def check_rates(series, origin, today):
    """Raises InsufficientData when the core of the payload is not fresh enough to publish."""
    stale = [i for i in TENOR_IDS if origin.get(i) not in FRESH or not series.get(i)]
    if stale:
        raise InsufficientData(f"core series not fresh: {' '.join(stale)}")
    as_of = max(d for d, _ in series["DGS10"])
    age = (today - date.fromisoformat(as_of)).days
    if age > MAX_STALE_DAYS:
        raise InsufficientData(f"DGS10 is {age} days old ({as_of})")
    return as_of


def rates_source(origin):
    used = {origin.get(i) for i in FRED_IDS} & set(FRESH)
    if len(used) == 1:
        return SOURCE_LABELS[used.pop()]
    return "mixed" if used else None


def build_curves(data, as_of):
    anchor = data["DGS10"]
    nominal = []
    for key, target in ra.snapshot_targets(as_of):
        snap = ra.snapshot_curve(data, TENOR_IDS, anchor, target)
        if snap is not None:
            nominal.append({"key": key, **snap})
    return {"nominal": nominal}


def build_policy(data):
    upper, lower = data.get("DFEDTARU"), data.get("DFEDTARL")
    if not upper or not lower:
        return None
    day, high = upper.last()
    low = lower.on_or_before(day)
    if low is None:
        return None
    return {"lower": round_or_none(low[1], 3), "upper": round_or_none(high, 3), "date": day}


def build_spreads(data):
    out = []
    for key, long_id, short_id in SPREADS:
        entry = {"key": key, "long": long_id, "short": short_id}
        stats = None
        if data.get(long_id) and data.get(short_id):
            stats = ra.spread_stats(ra.spread_points(data[long_id], data[short_id]))
        if stats is None:
            stats = {"valueBp": None, "chg1mBp": None, "inverted": False, "invertedDays": 0,
                     "lastInversion": None, "pct5y": None}
        entry.update(stats)
        out.append(entry)
    return out


def build_etfs(etfs, data):
    """ETF entries: the static fields plus the model duration / convexity from today's DGS20 / DGS30."""
    dgs20, dgs30 = data.get("DGS20"), data.get("DGS30")
    y20 = dgs20.last()[1] if dgs20 else None
    y30 = dgs30.last()[1] if dgs30 else None
    entries = []
    for etf in etfs:
        entry = {k: v for k, v in etf.items() if k != "modelProxy"}
        entry["model"] = ra.model_duration_convexity(etf["modelProxy"], y20, y30)
        entries.append(entry)
    return entries


def history_block(data, dates, lookback):
    out = {}
    for series_id in HISTORY_IDS:
        series = data.get(series_id)
        values = []
        for day in dates:
            if series is None:
                values.append(None)
                continue
            if lookback:
                found = series.on_or_before(day, lookback)
                values.append(round_or_none(found[1], 3) if found else None)
            else:
                values.append(round_or_none(series.value_at(day), 3) if series.value_at(day) is not None else None)
        out[series_id] = values
    return {"dates": dates, "series": out}


def build_payloads(series, origin, etfs, today, now):
    """Pure: (rates, history) payloads. Raises InsufficientData when the old files must be kept."""
    as_of = check_rates(series, origin, today)
    data = fresh_series(series, origin)
    generated = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    missing = [i for i in FRED_IDS if origin.get(i) not in FRESH]

    rates = {
        "version": VERSION,
        "generatedAt": generated,
        "asOf": as_of,
        "sources": {"rates": rates_source(origin)},
        "missing": missing,
        "tenors": TENORS,
        "tenorYears": TENOR_YEARS,
        "curves": build_curves(data, as_of),
        "policy": build_policy(data),
        "spreads": build_spreads(data),
        "etfs": build_etfs(etfs, data),
        "notes": NOTES,
    }
    dgs10_dates = data["DGS10"].dates
    history = {
        "version": VERSION,
        "generatedAt": generated,
        "asOf": as_of,
        "weekly": history_block(data, ra.weekly_sample(dgs10_dates), ra.SNAPSHOT_LOOKBACK_DAYS),
        "daily": history_block(data, dgs10_dates[-DAILY_POINTS:], 0),
    }
    return rates, history


# --------------------------------------------------------------------------- files and main


def read_json(path):
    try:
        with open(path, encoding="utf-8") as file:
            value = json.load(file)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_atomic(path, value):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as file:
            file.write(body)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return len(body.encode("utf-8"))


def run(out_dir, http_get=None, api_key=None, today=None, now=None, sleep=time.sleep, etfs=None):
    """Fetches everything, builds and writes both files. Returns the process exit code."""
    etfs = ETFS if etfs is None else etfs
    now = now or datetime.now(timezone.utc)
    today = today or now.date()
    rates_path = os.path.join(out_dir, RATES_FILE)
    history_path = os.path.join(out_dir, HISTORY_FILE)
    previous_history = read_json(history_path)

    fetchers = make_fetchers(http_get or default_http_get(), api_key, sleep, budget=FETCH_BUDGET_SECONDS)
    series, origin = load_series(FRED_IDS, fetchers, series_from_history(previous_history))
    try:
        rates, history = build_payloads(series, origin, etfs, today, now)
    except InsufficientData as exc:
        print(f"rates.json not updated: {exc}")
        return 1
    # history first: the page loads rates.json first and must never point at an older history file
    size_history = write_atomic(history_path, history)
    size_rates = write_atomic(rates_path, rates)
    print(
        f"wrote {rates_path} ({size_rates} bytes) and {history_path} ({size_history} bytes); "
        f"asOf {rates['asOf']}, rates {rates['sources']['rates']}, missing {' '.join(rates['missing']) or 'none'}"
    )
    return 0


def main():
    # a step killed by its timeout must still leave its progress in the log
    sys.stdout.reconfigure(line_buffering=True)
    out_dir = os.environ.get("RATES_OUT_DIR") or DEFAULT_OUT_DIR
    api_key = (os.environ.get("FRED_API_KEY") or "").strip() or None
    return run(out_dir, api_key=api_key)


if __name__ == "__main__":
    sys.exit(main())
