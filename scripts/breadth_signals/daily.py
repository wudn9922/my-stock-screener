"""每日：接上網站今天算好的美股全市場寬度，判斷各指數的抓低點訊號，輸出 breadth-signals.json。

輸入
    --calibration  breadth-us-calibration.json（calibrate.py 產生，門檻與歷史統計）
    --history      breadth-us-history.csv（長歷史；本程式會把網站最新的寬度接上去並寫回）
    --breadth      網站既有的 docs/report/breadth.json（build_full_market_breadth.py 的輸出）
    --bars-file    （選用）指數日 K pickle {ticker: DataFrame}；不給就用 yfinance 抓近 2 年
輸出
    --out          docs/report/breadth-signals.json

只讀網站的寬度數字，不重算寬度；網站的數字永遠優先（同一天兩邊都有時以網站為準）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import rules as R

SCHEMA_VERSION = 1
CHART_DAYS = 250
RECENT_CLOSED_DAYS = 10     # 出場後幾個交易日內仍顯示結果
MIN_INDEX_BARS = 400        # 兩年日 K 應有約 500 個交易日；不足視為不完整下載
TW_REASON = ("台股全市場寬度的超買超賣訊號在 2004–2026 回測中，2016 年前後兩段方向相反、全期不顯著，"
             "用來交易加權指數、台灣50、中型100 都沒有勝過隨便進場，因此不提供訊號；寬度數字僅供參考。")


# ---------------------------------------------------------------- I/O
def write_json_atomic(path, payload):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def write_csv_atomic(path, df):
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".csv", dir=d)
    os.close(fd)
    try:
        df.to_csv(tmp, float_format="%.1f")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def load_history(path) -> pd.DataFrame:
    h = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    return h[~h.index.duplicated(keep="last")]


def site_us_breadth(breadth: dict) -> pd.DataFrame:
    """網站 breadth.json 的美股歷史（約 120 天）→ DataFrame(pct20, pct60)。"""
    us = (breadth.get("markets") or {}).get("us") or {}
    h = us.get("history") or {}
    dates, m20, m60 = h.get("dates") or [], h.get("ma20Pct") or [], h.get("ma60Pct") or []
    rows = {}
    for i, d in enumerate(dates):
        try:
            day = pd.Timestamp(d)
        except (TypeError, ValueError):
            continue
        rows[day] = {"pct20": _num(m20[i] if i < len(m20) else None), "pct60": _num(m60[i] if i < len(m60) else None)}
    df = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    # asOf 當天以 segments.all 的完整數字為準
    seg = (us.get("segments") or {}).get("all") or {}
    if us.get("asOf") and seg:
        day = pd.Timestamp(us["asOf"])
        df.loc[day, "pct20"] = _num((seg.get("ma20") or {}).get("pct"))
        df.loc[day, "pct60"] = _num((seg.get("ma60") or {}).get("pct"))
        for n in (20, 60):
            df.loc[day, f"total{n}"] = _num((seg.get(f"ma{n}") or {}).get("total"))
        df.loc[day, "count"] = _num(seg.get("count"))
    return df.sort_index()


def _num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return np.nan
    return v if math.isfinite(v) else np.nan


def merge_history(hist: pd.DataFrame, site: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """網站數字覆蓋同日資料並接上新日期；回傳 (合併後歷史, 重疊檢查)。"""
    both = hist.index.intersection(site.index)
    check = {"overlapDays": int(len(both)), "maxAbsDiffMa20": None, "maxAbsDiffMa60": None}
    if len(both):
        for n in (20, 60):
            diff = (hist.loc[both, f"pct{n}"] - site.loc[both, f"pct{n}"]).abs().dropna()
            check[f"maxAbsDiffMa{n}"] = round(float(diff.max()), 1) if len(diff) else None
    merged = hist.copy()
    for col in site.columns:
        if col not in merged:
            merged[col] = np.nan
    merged = merged.reindex(merged.index.union(site.index))
    for col in site.columns:
        v = site[col].dropna()
        merged.loc[v.index, col] = v
    merged.index.name = "date"
    return merged.sort_index(), check


def fetch_bars(tickers, *, as_of=None, archive_dir=None) -> dict[str, pd.DataFrame]:
    from scripts.index_daily import read_index_daily, IndexDailyError
    from .calibrate import clean_bars
    options = {} if archive_dir is None else {"archive_dir": archive_dir}
    out = read_index_daily(tickers, period="2y", min_bars=MIN_INDEX_BARS, as_of=as_of, **options)
    if out.errors:
        raise IndexDailyError("；".join(out.errors.values()))
    for t in tickers:
        out[t] = clean_bars(out[t].dropna(subset=["Close"]))
    return out


def validate_index_bars(bars: dict[str, pd.DataFrame], as_of) -> None:
    """Fail closed on partial, stale, or too-short index downloads before writing either output."""
    missing = [ticker for ticker in R.INDICES if ticker not in bars]
    if missing:
        raise ValueError("指數日 K 缺少：" + ", ".join(missing))
    expected = pd.Timestamp(as_of).date()
    required = ("Open", "High", "Low", "Close")
    for ticker in R.INDICES:
        frame = bars[ticker]
        if not isinstance(frame, pd.DataFrame) or any(column not in frame for column in required):
            raise ValueError(f"{ticker} 指數日 K 欄位不完整")
        valid = frame.sort_index().dropna(subset=list(required))
        if len(valid) < MIN_INDEX_BARS:
            raise ValueError(f"{ticker} 指數日 K 僅 {len(valid)} 根，少於 {MIN_INDEX_BARS} 根，下載不完整")
        latest = pd.Timestamp(valid.index[-1]).date()
        if latest != expected:
            raise ValueError(f"{ticker} 指數日 K 最新日期 {latest} 與寬度日期 {expected} 不同")
        last = valid.iloc[-1][list(required)].to_numpy(dtype=float)
        if not np.isfinite(last).all() or (last <= 0).any():
            raise ValueError(f"{ticker} 指數日 K 最新 OHLC 無效")


# ---------------------------------------------------------------- 判斷
def index_state(hist: pd.DataFrame, bars: pd.DataFrame, p10: float, meta: dict, stats: dict) -> dict:
    swing = meta["swing"]
    f = R.low_signal_frame(hist["pct20"], bars["Close"], p10, swing)
    today = f.index[-1]
    last = f.iloc[-1]
    roll_high = bars["Close"].rolling(R.DRAWDOWN_LOOKBACK, min_periods=R.DRAWDOWN_LOOKBACK).max()
    high60 = float(roll_high.reindex(f.index).ffill(limit=3).iloc[-1]) if len(roll_high.dropna()) else None
    trigger_price = None if high60 is None else round(high60 * (1 - swing), 2)

    trades = R.trades_for(f.iloc[-(R.MAX_HOLD + RECENT_CLOSED_DAYS + R.COOLDOWN + 5):], bars, swing)
    t = trades[-1] if trades else None
    state, trade = "none", None
    if t is not None and t.signal_date == today:
        state = "new"
    elif t is not None and t.outcome == "open":
        state = "active"
    elif bool(last["a"]):
        state = "watch"
    elif t is not None and t.exit_date is not None and t.outcome in ("target", "stop", "timeout"):
        closed_ago = int((bars.index > t.exit_date).sum())
        if closed_ago <= RECENT_CLOSED_DAYS:
            state = t.outcome
    if t is not None and state in ("new", "active", "target", "stop", "timeout"):
        ref = float(last["close"]) if state == "new" else t.entry
        trade = {
            "signalDate": str(t.signal_date.date()),
            "entryDate": None if t.entry_date is None else str(t.entry_date.date()),
            "entry": _r(t.entry), "entryRef": _r(ref),
            "stop": _r(t.stop if t.stop is not None else ref * (1 - swing)),
            "target": _r(t.target if t.target is not None else ref * (1 + swing)),
            "exitDate": None if t.exit_date is None or t.outcome == "open" else str(t.exit_date.date()),
            "exit": None if t.outcome in ("open", "pending") else _r(t.exit),
            "daysHeld": t.days, "maxHold": R.MAX_HOLD,
            "expires": None if t.expires is None else str(t.expires.date()),
            "return": None if t.ret is None else round(t.ret, 4),
        }
    return {
        "symbol": None, "name": meta["name"], "swing": swing, "state": state,
        "close": _r(last["close"]), "drawdown60": None if pd.isna(last["drawdown"]) else round(float(last["drawdown"]), 4),
        "breadthOversold": bool(last["a"]), "indexPulledBack": bool(last["b"]),
        "triggerPrice": trigger_price,
        "trade": trade,
        "stats": {k: stats.get(k) for k in ("trades", "hitRate", "hitRateEarly", "hitRateLate", "baseRate",
                                             "avgReturn", "medianDays", "winnersMaeP90", "nearSwingLowRate")},
        "recentEvents": (stats.get("events") or [])[-5:],
        "markers": [str(d.date()) for d in f.index[f["signal"].to_numpy()][-20:]],
    }


def _r(v, n=2):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(float(v), n)


def build(cal: dict, hist: pd.DataFrame, bars: dict, check: dict, generated_at: str) -> dict:
    th = cal["thresholds"]
    as_of = hist.index[-1]
    p20, p60 = hist["pct20"].iloc[-1], hist["pct60"].iloc[-1]
    readings = {
        "ma20": {"pct": _r(p20, 1), "percentile": _r(R.percentile_rank(p20, hist["pct20"]), 1), "zone": R.zone(_num(p20), th["ma20"])},
        "ma60": {"pct": _r(p60, 1), "percentile": _r(R.percentile_rank(p60, hist["pct60"]), 1), "zone": R.zone(_num(p60), th["ma60"])},
    }
    indices = []
    for t, meta in R.INDICES.items():
        if t not in bars or t not in cal["indices"]:
            continue
        s = index_state(hist, bars[t], th["ma20"]["p10"], meta, cal["indices"][t])
        s["symbol"] = t
        if t in getattr(bars, "sources", {}):
            s["meta"] = bars.sources[t]
        indices.append(s)
    ob = cal.get("overbought", {})
    tail = hist.iloc[-CHART_DAYS:]
    return {
        "version": SCHEMA_VERSION,
        "generatedAt": generated_at,
        "markets": {
            "us": {
                "applicable": True,
                "asOf": str(as_of.date()),
                "readings": readings,
                "thresholds": th,
                "lowSignal": {
                    "rule": (f"站上 MA20 比例 ≤ {th['ma20']['p10']}%（歷史第 10 百分位），且指數距 60 日高點跌幅 ≥ 波段幅度；"
                             f"隔天開盤進場，停損／停利 = ±波段幅度，最長 {R.MAX_HOLD} 個交易日。"),
                    "indices": indices,
                },
                "overbought": {
                    "active": readings["ma20"]["zone"] in ("overbought", "extreme_overbought"),
                    "note": "超買代表趨勢強勢，不是賣出訊號：歷史上超買後先跌一個波段幅度的機率，和隨便哪天差不多。",
                    "dropFirstRate": ob.get("dropFirstRate"), "baseRate": ob.get("baseRate"), "events": ob.get("events"),
                },
                "history": {
                    "dates": [str(d.date()) for d in tail.index],
                    "ma20Pct": [_r(v, 1) for v in tail["pct20"]],
                    "ma60Pct": [_r(v, 1) for v in tail["pct60"]],
                },
                "calibration": {"generatedAt": cal.get("generatedAt"), "sample": cal.get("sample"),
                                "rulesVersion": cal.get("rulesVersion"), "overlapCheck": check},
            },
            "tw": {"applicable": False, "reason": TW_REASON},
        },
    }


def main(argv=None, bars_loader=fetch_bars):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--calibration", required=True)
    ap.add_argument("--history", required=True)
    ap.add_argument("--breadth", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--bars-file")
    ap.add_argument("--archive-dir", default=".cache/kline-archive")
    ap.add_argument("--max-overlap-diff", type=float, default=3.0,
                    help="長歷史與網站重疊日期的寬度差超過這個值（百分點）就失敗，提示需要重新校準")
    a = ap.parse_args(argv)
    try:
        cal = json.load(open(a.calibration, encoding="utf-8"))
        breadth = json.load(open(a.breadth, encoding="utf-8"))
        hist, check = merge_history(load_history(a.history), site_us_breadth(breadth))
        worst = max([v for v in (check["maxAbsDiffMa20"], check["maxAbsDiffMa60"]) if v is not None], default=0)
        if worst > a.max_overlap_diff:
            raise ValueError(f"長歷史與網站寬度差 {worst} 點，超過 {a.max_overlap_diff}；請重新跑 calibrate")
        missing_calibration = [ticker for ticker in R.INDICES if ticker not in (cal.get("indices") or {})]
        if missing_calibration:
            raise ValueError("校準檔缺少指數統計：" + ", ".join(missing_calibration))
        if a.bars_file:
            bars = pd.read_pickle(a.bars_file)
        elif bars_loader is fetch_bars:
            bars = fetch_bars(list(R.INDICES), as_of=hist.index[-1], archive_dir=a.archive_dir)
        else:
            bars = bars_loader(list(R.INDICES))
        validate_index_bars(bars, hist.index[-1])
        payload = build(cal, hist, bars, check, datetime.now(timezone.utc).replace(microsecond=0).isoformat())
        write_csv_atomic(a.history, hist)
        write_json_atomic(a.out, payload)
    except Exception as exc:
        print(f"❌ 寬度訊號未更新，保留原檔：{exc}", flush=True)
        return 1
    us = payload["markets"]["us"]
    print(f"✅ 美股寬度訊號 {us['asOf']}：MA20 {us['readings']['ma20']['pct']}%（{us['readings']['ma20']['zone']}），"
          f"MA60 {us['readings']['ma60']['pct']}%；" +
          "、".join(f"{x['name']} {x['state']}" for x in us["lowSignal"]["indices"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
