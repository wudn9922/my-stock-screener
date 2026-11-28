#!/usr/bin/env python3
"""突破達成率偏低提醒 → docs/report/breakout-warning.json（只讀永久 K 線封存，不寫任何價格）。

每天重算「全市場突破條件達成率」（與市場頁卡片同口徑：參考日前 21 個交易日的訊號中，
已有完整 5 日結果者的「五日守住」成功率，至少 15 筆），再看它在自己近兩年（504 個交易日、
至少 252 個有效值）中的百分位；低於第 10 百分位＝偏低提醒。

只發布回測有警示意味的組合（2021–2026 封存資料、以獨立事件和隨機日比較）：
  美股：突破波段前高、突破 20EMA；台股：突破 20EMA（台股波段前高與箱型歷史上沒有警示效果）。
歷史統計同樣只用當時可得資料：每段提醒（相隔不到 20 個交易日的視為同一段）的第一天，
之後 40 個交易日內最低價是否較當日收盤跌 7% 以上，並與任一天的同一比例對照。
提醒代表「風險升高、下跌可能較深」，不是買賣訊號；提醒後平均報酬並未較差。

名單是封存內目前的普通股（含存活者偏差）；封存在每日排程後才追加，所以資料日通常比市場頁晚一個交易日。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from urllib.parse import unquote

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_breakouts as bo
from breakout_outcomes import GATE_ATR_PERIOD, OBSERVATION_DAYS, evaluate_attempt
from swing_breakouts import wilder_atr
from swing_cohort import included, swing_attempts
from build_breadth import write_json_atomic
from output_guard import OutputRejected, require, retain_count, unique_keys

ROOT = Path(__file__).resolve().parents[1]
VERSION = 1
PARAMS = {
    "windowDays": 21, "observationDays": OBSERVATION_DAYS, "minSignals": 15, "rule": "hold5",
    "percentileWindow": 504, "percentileMinValid": 252, "lowPercentile": 10,
    "horizonDays": 40, "drawdown": 0.07, "episodeGapDays": 20, "jumpLimit": bo.JUMP_LIMIT,
}
METHODS = {"swing": "突破波段前高", "ema20": "突破20EMA"}
MARKETS = {
    "tw": {"index": "^TWII", "indexName": "加權指數", "methods": ["ema20"],
           "note": "台股只採用突破20EMA：2021–2026 回測中，波段前高與箱型的偏低提醒和隨機日沒有差別。"},
    "us": {"index": "^GSPC", "indexName": "S&P 500", "methods": ["swing", "ema20"], "note": None},
}
EMA_SPEC = [s for s in bo.STRATEGIES if s["key"] == "ema20"]
MIN_STOCKS = 200


def archive_symbols(root, market, kind):
    out = []
    for path in sorted((Path(root) / "index" / market).glob("*.json")):
        try:
            meta = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if meta.get("kind") == kind:
            out.append(unquote(path.stem))
    return out


def load_bars(root, market, symbol):
    """封存的年度 CSV → OHLCV DataFrame（不補值；週末列丟掉）。"""
    frames = []
    for path in sorted((Path(root) / market / symbol).glob("*.csv")):
        frames.append(pd.read_csv(path, index_col=0, parse_dates=True))
    if not frames:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df[df.index.day_of_week < 5].astype(float)


def stock_events(bars, calendar, market, methods):
    """一檔股票的已完成突破 → [(訊號日, 方法, 五日守住是否成功)]。異常跳價處重新起算，不用未來資料。"""
    bars = bars[(bars.Volume > 0) & (bars.Close > 0) & (bars.High > 0) & (bars.Low > 0)].copy()
    if len(bars) < 60:
        return []
    bars["High"] = np.maximum(bars.High, bars.Close)
    bars["Low"] = np.minimum(bars.Low, bars.Close)
    days = [d for d in calendar if bars.index[0] <= d <= bars.index[-1]]
    positions = {d: i for i, d in enumerate(days)}
    out = []
    jumps = bars.Close.pct_change().abs() > PARAMS["jumpLimit"] + 1e-9
    for _, seg in bars.groupby(jumps.cumsum()):
        if len(seg) <= 40:
            continue
        if "swing" in methods:
            for a in swing_attempts(seg, days):
                if included(a) and a["outcome"]["status"] == "complete":
                    out.append((a["date"], "swing", bool(a["outcome"]["wins"]["hold5"])))
        if "ema20" in methods:
            fresh, level, known = bo.strategy_signals(seg, bo.BOX_MAX_WIDTH[market], EMA_SPEC)["ema20"]
            atr = wilder_atr(seg, GATE_ATR_PERIOD)
            for i in np.flatnonzero((fresh & known).to_numpy()):
                o = evaluate_attempt(seg, int(i), float(level.iloc[i]), atr, days, positions)
                if o["status"] == "complete":
                    out.append((seg.index[i], "ema20", bool(o["wins"]["hold5"]) and not o.get("commonFailure")))
    return out


def rate_series(events, calendar, method):
    """卡片口徑：參考日 t 的訊號日落在 [t-20, t-5]（已滿 5 日）→ (成功率, 筆數)。"""
    index = pd.DatetimeIndex(calendar)
    pos = {d: i for i, d in enumerate(index)}
    n, w = np.zeros(len(index)), np.zeros(len(index))
    for day, m, win in events:
        i = pos.get(pd.Timestamp(day))
        if m == method and i is not None:
            n[i] += 1
            w[i] += win
    span = PARAMS["windowDays"] - PARAMS["observationDays"]
    nn = pd.Series(n, index=index).rolling(span).sum().shift(PARAMS["observationDays"])
    ww = pd.Series(w, index=index).rolling(span).sum().shift(PARAMS["observationDays"])
    return (ww / nn).where(nn >= PARAMS["minSignals"]), nn


def trailing_percentile(values):
    """與自己過去 504 個交易日比，嚴格低於今天的比例（%），有效值不足 252 → NaN。"""
    out = np.full(len(values), np.nan)
    v = values.to_numpy(float)
    win, need = PARAMS["percentileWindow"], PARAMS["percentileMinValid"]
    for i in range(len(v)):
        if not np.isfinite(v[i]):
            continue
        past = v[max(0, i - win + 1):i]
        past = past[np.isfinite(past)]
        if len(past) + 1 >= need:
            out[i] = float((past < v[i]).mean() * 100) if len(past) else np.nan
    return pd.Series(out, index=values.index)


def forward_drawdown(close, low):
    """t 日收盤之後 40 個交易日內的最低價跌幅；視窗未滿 → NaN。"""
    h = PARAMS["horizonDays"]
    c, lo = close.to_numpy(float), low.to_numpy(float)
    out = np.full(len(c), np.nan)
    for i in range(len(c) - h):
        window = lo[i + 1:i + h + 1]
        if np.isfinite(c[i]) and c[i] > 0 and np.isfinite(window).all():
            out[i] = float(window.min() / c[i] - 1)
    return pd.Series(out, index=close.index)


def episode_starts(flags):
    """偏低提醒每段的第一天；距上一段開始不到 20 個交易日的併入同一段。"""
    starts, last = [], None
    values = flags.to_numpy(bool)
    for i, on in enumerate(values):
        if on and (i == 0 or not values[i - 1]):
            if last is None or i - last >= PARAMS["episodeGapDays"]:
                starts.append(i)
            last = i
    return starts


def method_payload(rate, n, pct, dd, label):
    low = pct <= PARAMS["lowPercentile"]
    hit = dd <= -PARAMS["drawdown"]
    starts = episode_starts(low.fillna(False))
    episodes = [{"date": rate.index[i].strftime("%Y-%m-%d"), "rate": round(float(rate.iloc[i]), 4),
                 "pct": round(float(pct.iloc[i]), 1),
                 "drawdown": None if np.isnan(dd.iloc[i]) else round(float(dd.iloc[i]), 4),
                 "hit": None if np.isnan(dd.iloc[i]) else bool(hit.iloc[i])} for i in starts]
    done = [e for e in episodes if e["hit"] is not None]
    base = dd[pct.notna() & dd.notna()]
    first = rate.first_valid_index()
    rows = []
    for day in rate.index[rate.index >= first] if first is not None else []:
        r, p = rate.loc[day], pct.loc[day]
        rows.append({"date": day.strftime("%Y-%m-%d"), "rate": None if np.isnan(r) else round(float(r), 4),
                     "n": int(n.loc[day]) if np.isfinite(n.loc[day]) else 0,
                     "pct": None if np.isnan(p) else round(float(p), 1), "low": bool(p <= PARAMS["lowPercentile"])})
    streak = 0
    for row in reversed(rows):
        if not row["low"]:
            break
        streak += 1
    latest = rows[-1] if rows else None
    return {"label": label, "rows": rows, "latest": dict(latest, lowDays=streak) if latest else None,
            "stats": {"episodes": len(done), "hits": sum(e["hit"] for e in done),
                      "avgDrawdown": round(float(np.mean([e["drawdown"] for e in done])), 4) if done else None,
                      "baseHitRate": round(float((base <= -PARAMS["drawdown"]).mean()), 4) if len(base) else None,
                      "baseAvgDrawdown": round(float(base.mean()), 4) if len(base) else None,
                      "pending": len(episodes) - len(done), "list": episodes}}


def _work(args):
    root, market, symbol, calendar, methods = args
    try:
        bars = load_bars(root, market, symbol)
        if bars.empty:
            raise ValueError('empty archived bars')
        return stock_events(bars, calendar, market, methods)
    except Exception as exc:  # noqa: BLE001 - one broken file must not stop the market
        print(f"⚠️ {symbol}: {type(exc).__name__}: {exc}")
        return None


def build_market(root, market, workers=None):
    cfg = MARKETS[market]
    index = load_bars(root, market, cfg["index"])
    if index.empty:
        raise ValueError(f"{market} 封存沒有 {cfg['index']}")
    calendar = list(index.index)
    symbols = archive_symbols(root, market, "stock")
    if len(symbols) < MIN_STOCKS:
        raise ValueError(f"{market} 封存只有 {len(symbols)} 檔股票")
    unique_keys(symbols, f'{market} archive stocks')
    jobs = [(root, market, s, calendar, cfg["methods"]) for s in symbols]
    if workers == 1:
        results = list(map(_work, jobs))
    else:
        from multiprocessing import Pool
        with Pool(workers or os.cpu_count() or 1) as pool:
            results = pool.map(_work, jobs, chunksize=8)
    failed = sum(r is None for r in results)
    events = [e for r in results if r for e in r]
    dd = forward_drawdown(index.Close, index.Low)
    methods = {}
    for method in cfg["methods"]:
        rate, n = rate_series(events, calendar, method)
        methods[method] = method_payload(rate, n, trailing_percentile(rate), dd, METHODS[method])
    first = min((m["rows"][0]["date"] for m in methods.values() if m["rows"]), default=None)
    closes = [{"date": d.strftime("%Y-%m-%d"), "close": round(float(c), 2)}
              for d, c in index.Close.items() if first and d.strftime("%Y-%m-%d") >= first]
    return {"asOf": calendar[-1].strftime("%Y-%m-%d"), "index": {"symbol": cfg["index"], "name": cfg["indexName"]},
            "stocks": len(symbols), "evaluated": len(symbols) - failed, "failedStocks": failed, "note": cfg["note"], "methods": methods, "closes": closes}


def validate_market(market, current, previous):
    label = f"{market} breakout-warning {current['asOf']}"
    evaluated, failed = current["evaluated"], current["failedStocks"]
    total = current["stocks"]
    require(evaluated + failed == total and evaluated >= 0 and failed >= 0 and total > 0,
            label, f"invalid evaluated/failed counts {evaluated}/{failed}/{total}")
    require(failed * 10 <= total, label, f"failedStocks {failed}/{total} exceeds 10%")
    # Legacy snapshots already record stocks and failedStocks, so they supply a baseline.
    prior = previous.get("evaluated", previous.get("stocks", 0) - previous.get("failedStocks", 0))
    retain_count(evaluated, prior, label, min_share=.85)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", default=str(ROOT / ".cache/kline-archive"))
    parser.add_argument("--out", default=str(ROOT / "docs/report/breakout-warning.json"))
    parser.add_argument("--markets", default="tw,us")
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args(argv)
    previous = {}
    if os.path.exists(args.out):
        try:
            previous = json.loads(Path(args.out).read_text()).get("markets", {})
        except (OSError, ValueError):
            previous = {}
    markets, errors, rejected = dict(previous), [], []
    for market in [m.strip() for m in args.markets.split(",") if m.strip()]:
        try:
            candidate = build_market(args.archive_dir, market, args.workers)
            validate_market(market, candidate, previous.get(market, {}))
            markets[market] = candidate
            print(f"{market}: asOf {candidate['asOf']}, {candidate['evaluated']} evaluated, {candidate['failedStocks']} failed")
        except OutputRejected as exc:
            rejected.append(str(exc))
        except Exception as exc:  # noqa: BLE001 - keep the other market and the previous file
            errors.append(f"{market}: {type(exc).__name__}: {exc}")
            if market in previous:
                markets[market] = previous[market]
    if rejected:
        print('⚠️ ' + '; '.join(rejected + errors))
        return 1
    if not any(m in markets for m in MARKETS) or len(errors) == len([m for m in args.markets.split(',') if m.strip()]):
        print("⚠️ 兩個市場都失敗，保留舊檔：" + "; ".join(errors))
        return 1
    write_json_atomic(args.out, {"version": VERSION, "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "params": PARAMS, "markets": markets})
    for err in errors:
        print("⚠️ " + err)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
