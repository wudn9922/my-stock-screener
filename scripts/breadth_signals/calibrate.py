"""校準：用長歷史算出門檻與每個指數的訊號命中率，輸出
    data/breadth-us-calibration.json   門檻、規則、各指數歷史統計（網站每日讀它）
    data/breadth-us-history.csv        每日寬度長歷史（網站每日在後面接上新的一天）

建議每季重跑一次（或規則參數改動時）。美股約 5,300 檔、2003 年至今，下載約 20–30 分鐘。

用法：
    python -m breadth_signals.calibrate --out-dir data                    # 下載＋計算
    python -m breadth_signals.calibrate --prices cache/us_cv.pkl ...      # 用已下載的價格（pickle：{"close": df, "volume": df}）
    python -m breadth_signals.calibrate --universe-file symbols.json ...  # 指定股票清單（JSON 陣列）

股票清單預設與網站相同：在 repo 內執行時使用 scripts/universe_sources.parse_us_listed
（NasdaqTrader nasdaqlisted + otherlisted，含 ADR、排除 ETF／SPAC／權證／特別股…）。
價格：Yahoo 日 K（auto_adjust=False 的 Close 已含分割調整，與網站快取一致）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import rules as R
from .core import barrier_base_rate, breadth_history, zigzag_lows

PRICE_START = "2003-01-01"
SPLIT_DATE = "2016-01-01"     # 前後段：檢查規則在兩段時期是否都成立
NASDAQ_URLS = ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
               "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt")


# ---------------------------------------------------------------- 資料
def site_universe() -> list[str]:
    """與網站相同的美股全市場名單。需在 repo 根目錄執行（可 import scripts.universe_sources）。"""
    texts = []
    for url in NASDAQ_URLS:
        with urllib.request.urlopen(url, timeout=60) as r:
            texts.append(r.read().decode("utf-8", "ignore"))
    try:
        sys.path.insert(0, os.getcwd())
        from scripts.universe_sources import parse_us_listed
    except ImportError as exc:  # 不在 repo 內：請用 --universe-file
        raise SystemExit(f"找不到 scripts.universe_sources（請在 my-stock-screener 根目錄執行，或用 --universe-file）：{exc}")
    return sorted(parse_us_listed(texts[0], texts[1], {}))


def download(symbols, start=PRICE_START, batch=150, retries=5, adjusted=False):
    import yfinance as yf
    closes, vols = [], []
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        for attempt in range(retries):
            try:
                d = yf.download(chunk, start=start, progress=False, auto_adjust=adjusted, threads=True, group_by="column")
                c, v = d["Close"], d["Volume"]
                if isinstance(c, pd.Series):
                    c, v = c.to_frame(chunk[0]), v.to_frame(chunk[0])
                closes.append(c); vols.append(v)
                break
            except Exception as exc:
                print(f"  重試 {i}: {exc}", file=sys.stderr)
                time.sleep(15 * (attempt + 1))
        print(f"  {min(i + batch, len(symbols))}/{len(symbols)}", flush=True)
    c = pd.concat(closes, axis=1, sort=True); v = pd.concat(vols, axis=1, sort=True)
    c, v = c.loc[:, ~c.columns.duplicated()], v.loc[:, ~v.columns.duplicated()]
    c.index = v.index = pd.to_datetime(c.index).tz_localize(None)
    return c, v


def index_bars(tickers, start=PRICE_START) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    d = yf.download(list(tickers), start=start, progress=False, auto_adjust=False, group_by="column")
    out = {}
    for t in tickers:
        df = pd.DataFrame({k: d[k][t] for k in ("Open", "High", "Low", "Close")}).dropna(subset=["Close"])
        df.index = pd.to_datetime(df.index).tz_localize(None)
        out[t] = clean_bars(df)
    return out


def clean_bars(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Open"] = df["Open"].where(df["Open"] > 0, df["Close"])
    df["High"] = df[["High", "Open", "Close"]].max(axis=1)
    df["Low"] = df[["Low", "Open", "Close"]].min(axis=1)
    return df


# ---------------------------------------------------------------- 統計
def _rate(trades, start=None, end=None):
    t = [x for x in trades if x.outcome in ("target", "stop", "timeout")
         and (start is None or x.signal_date >= pd.Timestamp(start)) and (end is None or x.signal_date < pd.Timestamp(end))]
    if not t:
        return {"trades": 0, "hitRate": None}
    return {"trades": len(t), "hitRate": round(float(np.mean([x.outcome == "target" for x in t])), 4)}


def index_stats(hist: pd.DataFrame, bars: pd.DataFrame, p10: float, swing: float) -> dict:
    frame = R.low_signal_frame(hist["pct20"], bars["Close"], p10, swing)
    trades = R.trades_for(frame, bars, swing)
    done = [t for t in trades if t.outcome in ("target", "stop", "timeout")]
    first_day = max(hist.index[0], bars.index[0])
    full, early, late = _rate(trades), _rate(trades, None, SPLIT_DATE), _rate(trades, SPLIT_DATE, None)
    wins = [t for t in done if t.outcome == "target"]
    # 補充：訊號落在 ZigZag 波段低點前後 10 個交易日內的比例
    lows = zigzag_lows(bars["Close"], swing)
    pos = {d: i for i, d in enumerate(bars.index)}
    near = []
    for t in done:
        i = pos.get(t.signal_date)
        if i is None:
            continue
        near.append(any(abs(pos[l] - i) <= 10 for l in lows if l in pos))
    return {
        "trades": full["trades"],
        "hitRate": full["hitRate"],
        "hitRateEarly": early["hitRate"], "tradesEarly": early["trades"],
        "hitRateLate": late["hitRate"], "tradesLate": late["trades"],
        "baseRate": round(barrier_base_rate(bars[bars.index >= first_day], swing, R.MAX_HOLD), 4),
        "avgReturn": round(float(np.mean([t.ret for t in done])), 4) if done else None,
        "medianDays": int(np.median([t.days for t in done])) if done else None,
        "winnersMaeP90": round(float(np.percentile([t.mae for t in wins], 10)), 4) if len(wins) >= 5 else None,
        "nearSwingLowRate": round(float(np.mean(near)), 4) if near else None,
        "events": [{"signal": str(t.signal_date.date()), "entry": str(t.entry_date.date()) if t.entry_date is not None else None,
                    "exit": str(t.exit_date.date()) if t.exit_date is not None else None,
                    "outcome": t.outcome, "ret": None if t.ret is None else round(t.ret, 4), "days": t.days}
                   for t in trades],
    }


def overbought_stats(hist, bars, p95, swing) -> dict:
    """超買（MA20 首次 ≥ 第 95 百分位）後，「先跌一個波段幅度」的比例 vs 任意一天。"""
    c = bars["Close"].reindex(hist.index).ffill(limit=3)
    cond = (hist["pct20"] >= p95).fillna(False)
    days = hist.index[(cond & ~cond.shift(1, fill_value=False)).to_numpy()]
    H, L, d = bars["High"].to_numpy(float), bars["Low"].to_numpy(float), bars.index

    def drop_first(i):
        e = bars["Close"].iloc[i]
        lo = np.flatnonzero(L[i + 1:i + 1 + R.MAX_HOLD] <= e * (1 - swing))
        hi = np.flatnonzero(H[i + 1:i + 1 + R.MAX_HOLD] >= e * (1 + swing))
        return (lo[0] if len(lo) else 10 ** 9) < (hi[0] if len(hi) else 10 ** 9)

    idx = [int(np.searchsorted(d.values, np.datetime64(x))) for x in days]
    idx = [i for i in idx if i + R.MAX_HOLD < len(d)]
    allidx = [i for i in range(len(d) - R.MAX_HOLD - 1) if d[i] >= hist.index[0]]
    return {"events": len(idx), "dropFirstRate": round(float(np.mean([drop_first(i) for i in idx])), 4) if idx else None,
            "baseRate": round(float(np.mean([drop_first(i) for i in allidx[::2]])), 4)}


def calibrate(hist: pd.DataFrame, bars: dict[str, pd.DataFrame]) -> dict:
    th = {}
    for n in (20, 60):
        x = hist[f"pct{n}"].dropna()
        th[f"ma{n}"] = {f"p{int(q * 100)}": round(float(x.quantile(q)), 1) for q in R.ZONE_QUANTILES}
    p10, p95 = th["ma20"]["p10"], th["ma20"]["p95"]
    idx = {}
    for t, meta in R.INDICES.items():
        if t in bars:
            idx[t] = {"name": meta["name"], "swing": meta["swing"], **index_stats(hist, bars[t], p10, meta["swing"])}
    ob = overbought_stats(hist, bars["^GSPC"], p95, R.INDICES["^GSPC"]["swing"])
    return {
        "version": 1, "rulesVersion": R.RULES_VERSION,
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "sample": {"start": str(hist.index[0].date()), "end": str(hist.index[-1].date()), "days": int(len(hist)),
                   "split": SPLIT_DATE, "universe": "NasdaqTrader 美股普通股與 ADR（與網站 universe 相同規則）"},
        "thresholds": th,
        "rule": {"oversoldQuantile": R.OVERSOLD_Q, "drawdownLookback": R.DRAWDOWN_LOOKBACK,
                 "cooldown": R.COOLDOWN, "maxHold": R.MAX_HOLD},
        "indices": idx,
        "overbought": {"index": "^GSPC", "quantile": 0.95, **ob},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--prices", help="已下載的價格 pickle：{'close': DataFrame, 'volume': DataFrame}")
    ap.add_argument("--universe-file", help="股票清單 JSON 陣列（不在 repo 內執行時使用）")
    ap.add_argument("--save-prices", help="把下載的價格存成 pickle，下次可用 --prices")
    a = ap.parse_args(argv)

    if a.prices:
        p = pd.read_pickle(a.prices)
        close, volume = p["close"], p["volume"]
    else:
        syms = json.load(open(a.universe_file)) if a.universe_file else site_universe()
        print(f"股票 {len(syms)} 檔，下載中⋯", flush=True)
        close, volume = download(syms)
        if a.save_prices:
            pd.to_pickle({"close": close, "volume": volume}, a.save_prices)
    print("計算寬度長歷史⋯", flush=True)
    hist = breadth_history(close, volume)
    hist = hist[hist["total60"] > 0]
    print("下載指數⋯", flush=True)
    bars = index_bars(R.INDICES)
    cal = calibrate(hist, bars)
    os.makedirs(a.out_dir, exist_ok=True)
    hist_out = hist[["pct20", "pct60", "total20", "total60", "count"]].copy()
    hist_out.index.name = "date"
    hist_out.to_csv(os.path.join(a.out_dir, "breadth-us-history.csv"), float_format="%.1f")
    with open(os.path.join(a.out_dir, "breadth-us-calibration.json"), "w", encoding="utf-8") as f:
        json.dump(cal, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: cal[k] for k in ("sample", "thresholds")}, ensure_ascii=False, indent=1))
    for t, s in cal["indices"].items():
        print(f"{s['name']}: {s['trades']} 筆，抓到低點 {s['hitRate']:.0%}（前段 {s['hitRateEarly']:.0%}／後段 {s['hitRateLate']:.0%}），"
              f"隨便進場 {s['baseRate']:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
