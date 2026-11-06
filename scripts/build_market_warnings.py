#!/usr/bin/env python3
"""Causal market risk warnings. Read-only prices/cache; only derived outputs are written."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import sys
import time

import exchange_calendars as xcals
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_breakouts as bo
import build_full_market_breakouts as full
from build_breadth import write_json_atomic
from market_warnings_log import append_observations
from swing_cohort import COUNTING_MODEL, included, swing_attempts
from market_sessions import UNKNOWN_VOLUME_DATES, market_prices
from breakout_outcomes import BARRIER_PCT, CLOSE_BUFFER_ATR, GATE_ATR_PERIOD, OBSERVATION_DAYS
from swing_breakouts import ATR_PERIOD, REVERSAL_ATR
from index_daily import read_index_daily, IndexDailyError, DEFAULT_ARCHIVE

ROOT = Path(__file__).resolve().parents[1]
PARAMS = json.loads(Path(__file__).with_name("market_warnings_params.json").read_text())
PARAMS.update(closeBufferAtr=CLOSE_BUFFER_ATR, atrPeriod=GATE_ATR_PERIOD,
              observationDays=OBSERVATION_DAYS, barrier=BARRIER_PCT,
              swingAtrPeriod=ATR_PERIOD, swingReversalAtr=REVERSAL_ATR)
EVIDENCE = json.loads(Path(__file__).with_name("market_warnings_evidence.json").read_text())


@lru_cache(maxsize=8)
def exchange(market, year):
    return xcals.get_calendar("XTAI" if market == "tw" else "XNYS",
                              start=f"{year - 25}-01-01", end=f"{year + 1}-12-31")


def completed_calendar(market, now, start):
    cal = exchange(market, now.year)
    end = now.astimezone(cal.tz).date()
    # Cached rows older than the calendar's first session (e.g. 2000) are outside every
    # warning window; clamp instead of letting exchange_calendars reject the whole run.
    first = pd.Timestamp(cal.first_session).tz_localize(None) if pd.Timestamp(cal.first_session).tzinfo else pd.Timestamp(cal.first_session)
    days = cal.sessions_in_range(max(pd.Timestamp(start), first), pd.Timestamp(end))
    return pd.DatetimeIndex([day.tz_localize(None) for day in days
                             if cal.session_close(day).to_pydatetime() <= now])


def volume_series(bars, calendar, params=PARAMS):
    """Reindex BEFORE comparisons: missing/invalid sessions never get bridged."""
    bars = bars.reindex(calendar)
    prices = bars[list(bo.PRICE_COLUMNS)]
    valid = pd.Series(np.isfinite(prices).all(axis=1), index=calendar) & (prices > 0).all(axis=1)
    valid &= (bars.High >= prices.max(axis=1)) & (bars.Low <= prices.min(axis=1)) & (bars.Volume > 0)
    change = bars.Close.div(bars.Close.shift(1)).sub(1)
    valid &= change.isna() | (change.abs() <= params["maxReturn"] + 1e-12)
    adjacent = valid & valid.shift(1, fill_value=False)
    up, down = adjacent & (change > 0), adjacent & (change < 0)
    out = pd.DataFrame(index=calendar)
    for window in params["windows"]:
        for name, mask, direction in (("up_vol_up_ratio", up, bars.Volume > bars.Volume.shift(1)),
                                       ("down_vol_down_ratio", down, bars.Volume < bars.Volume.shift(1))):
            denominator = mask.astype(int).rolling(window, min_periods=window).sum()
            numerator = (mask & direction).astype(int).rolling(window, min_periods=window).sum()
            out[f"{name}_{window}"] = numerator / denominator.replace(0, np.nan)
    out["avg60"] = (out.up_vol_up_ratio_60 + out.down_vol_down_ratio_60) / 2
    out["median756"] = out.avg60.shift(1).rolling(params["medianWindow"], min_periods=params["medianMinValid"]).median()
    out["vol60_weak"] = (out.avg60 < out.median756 - params["volumeGap"]).where(out.median756.notna() & out.avg60.notna())
    return out


def leader_membership(close, volume, market, params=PARAMS):
    cfg = params["markets"][market]
    # Endpoint return over market sessions. Both endpoints must exist; neither a
    # forward fill nor today's return can enter today's selection.
    returns = close.div(close.shift(params["leaderReturnDays"])).sub(1).shift(1)
    discontinuity = close.pct_change(fill_method=None).abs() > params["stockJumpLimit"] + 1e-9
    returns = returns.where(discontinuity.rolling(params["leaderReturnDays"]).max().shift(1) == 0)
    amount = (close * volume).rolling(params["liquidityDays"], min_periods=params["liquidityDays"]).mean().shift(1)
    eligible = (amount >= cfg["minAmount"]) & (close.shift(1) >= cfg["minPrice"]) & returns.notna()
    ranks = returns.where(eligible).rank(axis=1, ascending=False, method="first")
    count = np.ceil(eligible.sum(axis=1) * cfg["leaderShare"])
    return ranks.le(count, axis=0) & eligible


def stock_matrices(frames, calendar):
    price_valid = pd.DataFrame({s: ((f[list(bo.PRICE_COLUMNS)] > 0).all(axis=1) &
                                    np.isfinite(f[list(bo.PRICE_COLUMNS)]).all(axis=1))
                                for s, f in frames.items()}).reindex(calendar).fillna(False).astype(bool)
    close = pd.DataFrame({s: f.Close for s, f in frames.items()}).reindex(calendar).where(price_valid)
    volumes = {}
    for symbol, bars in frames.items():
        unknown = pd.to_datetime(list(bars.attrs.get(UNKNOWN_VOLUME_DATES, ())))
        volumes[symbol] = bars.Volume.mask(bars.index.isin(unknown))
    volume = pd.DataFrame(volumes).reindex(calendar).where(price_valid)
    volume = volume.where(volume >= 0)
    # A known zero contributes zero to the 20-session liquidity mean. Unknown
    # volume remains NaN; neither is an active breakout/coverage observation.
    return close, volume, price_valid & (volume > 0)


def matured_rates(events, leaders, calendar, window, params=PARAMS):
    """At observation t use its t-1 leader list and events t-(W+4)..t-5."""
    positions = {day: i for i, day in enumerate(calendar)}
    columns = {symbol: i for i, symbol in enumerate(leaders.columns)}
    membership = leaders.to_numpy(dtype=bool)
    by_day = {}
    for symbol, attempt in events:
        if not included(attempt) or attempt["outcome"]["status"] != "complete":
            continue
        day = attempt["date"]
        win = int(attempt["outcome"]["wins"]["hold5"])
        by_day.setdefault(positions[day], []).append((columns[symbol], win))
    # A bounded per-stock rolling cohort avoids a sessions × stocks × windows
    # allocation. Membership is evaluated at the observation date, not event date.
    counts, wins = np.zeros(len(columns)), np.zeros(len(columns))
    totals = np.full((len(calendar), 4), np.nan)
    for t in range(len(calendar)):
        end = t - params["observationDays"]
        for column, win in by_day.get(end, ()):
            counts[column] += 1
            wins[column] += win
        for column, win in by_day.get(end - window, ()):
            counts[column] -= 1
            wins[column] -= win
        if end >= window - 1:
            mask = membership[t]
            totals[t] = counts.sum(), wins.sum(), counts[mask].sum(), wins[mask].sum()
    observed = pd.DataFrame(totals, index=calendar, columns=["n", "wins", "leader_n", "leader_wins"])
    out = pd.DataFrame(index=calendar)
    for name, prefix in (("market_success", ""), ("leader_success", "leader_")):
        n, wins = observed[f"{prefix}n"], observed[f"{prefix}wins"]
        raw = (wins / n).where(n >= params["minBreakouts"])
        out[name] = raw.rolling(params["smoothDays"], min_periods=params["smoothDays"]).mean()
        out[f"{name}_n"] = n
    return out


def breakout_counts(events, valid, listed, params=PARAMS):
    counts = pd.Series(0., index=valid.index)
    for _, attempt in events:
        if included(attempt):
            counts.loc[attempt["date"]] += 1
    denominator = valid.astype(int).rolling(params["countWindow"], min_periods=params["countWindow"]).max().sum(axis=1)
    sufficient = valid.sum(axis=1) / listed >= params["minCoverage"]
    return (counts.rolling(params["countWindow"], min_periods=params["countWindow"]).sum() / denominator * 1000).where(
        sufficient.astype(int).rolling(params["countWindow"], min_periods=params["countWindow"]).min() == 1)


def past_quantile(values, params=PARAMS):
    return values.shift(1).rolling(params["quantileWindow"], min_periods=params["quantileMinValid"]).quantile(params["countQuantile"])


def correction_segments(close, threshold=PARAMS["correctionDrop"]):
    """Retrospective percentage zigzag, NEVER used by warning calculation."""
    close = close.dropna()
    if close.empty:
        return []
    peak = trough = close.index[0]
    falling = False
    segments = []
    for day, value in close.items():
        if not falling:
            if value >= close.loc[peak]:
                peak = day
            elif value <= close.loc[peak] * (1 - threshold):
                falling, trough = True, day
        else:
            if value <= close.loc[trough]:
                trough = day
            elif value >= close.loc[trough] * (1 + threshold):
                segments.append({"start": str(peak.date()), "end": str(trough.date()),
                                 "drawdown": float(close.loc[trough] / close.loc[peak] - 1), "confirmed": True})
                falling, peak = False, day
    if falling:
        segments.append({"start": str(peak.date()), "end": str(trough.date()),
                         "drawdown": float(close.loc[trough] / close.loc[peak] - 1), "confirmed": False})
    return segments


def scalar(value):
    if pd.isna(value):
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return round(float(value), 7)


def records(frame, start):
    return [{"date": str(day.date()), **{name: scalar(v) for name, v in row.items()}}
            for day, row in frame.loc[start:].iterrows()]


def warning_flags(row, market):
    keys = ("vol60_weak", "leader_lag", "breadth_divergence") if market == "tw" else ("leader_lag", "breadth_divergence")
    # Unknown is not a green signal. A partial count is accompanied by completeness.
    return {"warning_level": sum(row.get(key) is True for key in keys),
            "warning_complete": all(row.get(key) is not None for key in keys)}


def build_market(spec, indices, market, now, params=PARAMS, previous=None):
    cfg = params["markets"][market]
    frames = spec["frames"]
    if not frames:
        raise ValueError(f"{market} 沒有完整名單日線")
    start = min(min(frame.index) for frame in frames.values())
    start = min(start, *(min(indices[symbol].index) for symbol in cfg["indices"]))
    calendar = completed_calendar(market, now, start)
    # Anchor to the cache's common completed data date, not the execution date.
    clipped = {s: f.loc[f.index.isin(calendar)] for s, f in frames.items()}
    clipped = {s: f for s, f in clipped.items() if not f.empty}
    clipped, closed = market_prices(clipped)
    calendar = calendar[~calendar.strftime("%Y-%m-%d").isin(closed)]
    if not clipped:
        raise ValueError(f"{market} 沒有已收盤有效日線")
    as_of = bo.as_of_date(clipped)
    calendar = calendar[calendar <= as_of]
    close, volume, valid = stock_matrices(clipped, calendar)
    evaluated = int(valid.iloc[-1].sum())
    if evaluated / spec["listed"] < params["minCoverage"] or evaluated < params["minEvaluated"]:
        raise ValueError(f"{market.upper()} 全市場有效日線 {evaluated}/{spec['listed']}，覆蓋不足，保留舊檔")
    leaders = leader_membership(close, volume, market, params)
    events = []
    started = time.monotonic()
    for i, (symbol, bars) in enumerate(clipped.items(), 1):
        good = bars.loc[valid[symbol].reindex(bars.index, fill_value=False)]
        # Reset causally at each discontinuity; a FUTURE split cannot erase past events.
        jumps = good.Close.pct_change().abs() > params["stockJumpLimit"] + 1e-9
        for _, segment in good.groupby(jumps.cumsum()):
            if len(segment) > params["swingAtrPeriod"]:
                events.extend((symbol, a) for a in swing_attempts(segment, calendar))
        if i % 250 == 0 or i == len(clipped):
            print(f"Market warnings {market}: {i}/{len(clipped)} symbols, {len(events)} attempts, {time.monotonic()-started:.1f}s", flush=True)
    successes = matured_rates(events, leaders, calendar, cfg["successWindow"], params)
    # Match the persisted series precision so equal past/current counts do not
    # change sides of a strict quantile comparison after a JSON round trip.
    count = breakout_counts(events, valid, spec["listed"], params).round(7)
    # Retain already-calculated dated observations after the rolling price cache
    # drops their input. This lets 400/756-session thresholds mature prospectively.
    saved = (previous or {}).get("series", [])
    old_counts = pd.Series({pd.Timestamp(r["date"]): r["breakout_count_per_1000"] for r in saved}, dtype=float)
    observed_days = valid.index[valid.any(axis=1)]
    # Once old prices roll out, keep the previously observed left edge rather
    # than recomputing it with an incomplete wave/return warmup.
    warmup = params["leaderReturnDays"] + cfg["successWindow"] + params["observationDays"] + params["smoothDays"]
    cutoff = observed_days[min(warmup, len(observed_days) - 1)]
    count.loc[count.index < cutoff] = old_counts.reindex(count.index).combine_first(count).loc[count.index < cutoff]
    count = count.combine_first(old_counts).reindex(calendar)
    p30 = past_quantile(count, params)
    primary = indices[cfg["indices"][0]].reindex(calendar).Close
    if pd.isna(primary.iloc[-1]) or primary.iloc[-1] <= 0:
        raise IndexDailyError(f"{market} 指數缺少 {as_of.date()} 完成日線")
    # As with the existing chart SMA, use actual completed index closes; don't
    # fabricate a close for an omitted source date (e.g. an emergency closure).
    observed_close = primary.dropna()
    observed_ma = observed_close.rolling(params["trendDays"]).mean()
    ma, prior_ma = observed_ma.reindex(calendar), observed_ma.shift(params["trendSlopeDays"]).reindex(calendar)
    uptrend = (primary > ma) & (ma > prior_ma)
    out = successes.copy()
    out["breakout_count_per_1000"], out["count_p30"] = count, p30
    out["uptrend"] = uptrend.where(prior_ma.notna() & primary.notna())
    high = observed_close.rolling(params["highWindow"]).max().reindex(calendar)
    out["breadth_divergence"] = ((primary >= high * params["nearHigh"]) & (count < p30)).where(high.notna() & p30.notna() & count.notna())
    if saved:
        old = pd.DataFrame(saved).set_index("date")
        old.index = pd.to_datetime(old.index)
        for column in ("leader_success", "market_success", "leader_success_n", "market_success_n"):
            mask = out.index < cutoff
            out.loc[mask, column] = old[column].reindex(out.index).combine_first(out[column]).loc[mask]
    out["leader_lag"] = (uptrend & (out.leader_success < out.market_success - params["leaderGap"])).where(out.leader_success.notna() & out.market_success.notna() & out.uptrend.notna())
    history_start = as_of - pd.DateOffset(years=params["historyYears"])
    index_out = {}
    for symbol in cfg["indices"]:
        bars = indices[symbol].reindex(calendar).copy()
        proxy = params["indexVolumeProxies"].get(symbol)
        if proxy:
            bars["Volume"] = indices[proxy].reindex(calendar).Volume
        values = volume_series(bars, calendar, params)
        index_out[symbol] = {"proxy": proxy, "validated": market == "tw" and symbol == "^TWII",
                             "series": records(values, history_start),
                             "latest": records(values.tail(1), history_start)[-1],
                             "corrections": correction_segments(bars.Close, params["correctionDrop"])}
        sources = getattr(indices, "sources", {})
        if symbol in sources:
            index_out[symbol]["meta"] = {"price": sources[symbol], "volume": sources.get(proxy or symbol)}
    primary_latest = index_out[cfg["indices"][0]]["series"]
    primary_by_day = {r["date"]: r for r in primary_latest}
    series = records(out, history_start)
    for row in series:
        v = primary_by_day[row["date"]]
        row.update(avg60=v["avg60"], median756=v["median756"], vol60_weak=v["vol60_weak"] if market == "tw" else None)
        row.update(warning_flags(row, market))
    return {"asOf": str(as_of.date()), "universeCount": spec["listed"], "evaluated": evaluated,
            "listingAsOf": spec["listingAsOf"], "series": series, "latest": series[-1], "indices": index_out,
            "limitations": ["名單是目前上市股票，含存活者偏差；歷史也使用這份現有名單，並非當時上市成分。",
                             "個股歷史依快取實際覆蓋；已產生的突破家數沿用日期累積，三年分位數至少400個值，歷史不足保留缺值，不推定正常。",
                             "缺乏上一個已確認高點的第一次高點不納入；日線無法推斷成交先後與可執行價格。"]}


def build_payload(inputs, indices, now, previous=None):
    previous = previous or {}
    if previous.get("params") != {**PARAMS, "swingCountingModel": COUNTING_MODEL}:
        previous = {}
    markets, updated = {}, 0
    for key, cfg in PARAMS["markets"].items():
        prior = previous.get("markets", {}).get(key)
        try:
            needed = cfg["indices"] + [PARAMS["indexVolumeProxies"][s] for s in cfg["indices"] if s in PARAMS["indexVolumeProxies"]]
            missing = [getattr(indices, "errors", {}).get(s, f"{s} 指數日線未取得") for s in needed if s not in indices]
            if missing:
                raise IndexDailyError("；".join(missing))
            old_market = prior if prior and "asOf" in prior else None
            markets[key] = build_market(inputs[key], indices, key, now, previous=old_market)
            markets[key]["update"] = {"status": "updated", "checkedAt": now.isoformat(),
                                       "generatedAt": now.isoformat(), "reason": None}
            updated += 1
        except IndexDailyError as exc:
            reason = str(exc)
            if prior and "asOf" in prior:
                markets[key] = copy.deepcopy(prior)
                generated = (prior.get("update") or {}).get("generatedAt") or previous["generatedAt"]
                markets[key]["update"] = {"status": "retained", "checkedAt": now.isoformat(),
                                           "generatedAt": generated, "reason": reason}
            else:
                markets[key] = {"update": {"status": "unavailable", "checkedAt": now.isoformat(),
                                            "generatedAt": None, "reason": reason}}
            print(f"Market warnings {key.upper()} 未更新：{reason}；" +
                  (f"保留 {prior['asOf']} 已發布資料" if prior and "asOf" in prior else "尚無可保留的已發布資料"), flush=True)
    if not updated:
        raise IndexDailyError("兩個市場的指數日線都未取得，保留整份舊檔")
    return {"version": 1, "generatedAt": now.isoformat(),
            "params": {**PARAMS, "swingCountingModel": COUNTING_MODEL}, "evidence": EVIDENCE, "markets": markets}


def fetch_indices(*, now=None, archive_dir=DEFAULT_ARCHIVE):
    # Same Yahoo daily OHLC source as main.download_index_history / IndexStatus;
    # never import main's account/report side effects or adjust by dividends.
    symbols = [s for cfg in PARAMS["markets"].values() for s in cfg["indices"]] + list(PARAMS["indexVolumeProxies"].values())
    return read_index_daily(symbols, period="7y", min_bars=60, now=now, archive_dir=archive_dir)


def main(argv=None, inputs_loader=full.load_inputs, indices_loader=fetch_indices, now=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", default=full.DEFAULT_CACHE)
    parser.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE))
    parser.add_argument("--out", default=str(ROOT / "docs/report/market-warnings.json"))
    parser.add_argument("--log", default=str(ROOT / "docs/report/market-warnings-log.csv"))
    args = parser.parse_args(argv)
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    started = time.monotonic()
    try:
        print("Market warnings: reading complete universe cache", flush=True)
        inputs = inputs_loader(args.cache_dir, now.date().isoformat())
        print(f"Market warnings: inputs loaded in {time.monotonic()-started:.1f}s; reading index daily bars", flush=True)
        try:
            previous = json.loads(Path(args.out).read_text())
        except (OSError, ValueError):
            previous = {}
        indices = fetch_indices(now=now, archive_dir=args.archive_dir) if indices_loader is fetch_indices else indices_loader()
        payload = build_payload(inputs, indices, now, previous)
        # Validate the entire payload before appending any forward observations.
        json.dumps(payload, allow_nan=False)
        append_observations(args.log, payload)
        write_json_atomic(args.out, payload)
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        print(f"❌ 多頭末期警示未更新，保留原檔：{exc}", flush=True)
        return 1
    print(f"Market warnings completed in {time.monotonic()-started:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
