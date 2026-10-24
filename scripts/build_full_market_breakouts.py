#!/usr/bin/env python3
"""全市場進場成功率：只讀 universe-full 的完整名單與日線快取，不連網、不退回 S&P 500。

共用 build_breakouts 的訊號與完整 5 日觀察樣本，分別保留四種成功判定口徑。
波段突破以 max(開盤價, 已確認波段高點) 作理論停買價格；箱型與20EMA以訊號日收盤進場。
資料缺漏、未成熟與沒有訊號都不當成失敗。
名單過期、快取不足或有效日線涵蓋率不足時保留既有報表，回傳失敗。
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_breakouts as bo  # noqa: E402
import universe_cache as uc  # noqa: E402
from build_breadth import load_daily_bars, write_json_atomic  # noqa: E402

MIN_LIST = {"TWSE": 800, "TPEx": 500, "US": 3000}
MIN_COVERAGE = 0.9
LIST_MAX_AGE_DAYS = 14
LABELS = {
    "tw": "全市場台股上市＋上櫃普通股",
    "us": "全市場美股普通股與 ADR（NYSE／NASDAQ／NYSE American 等）",
}
DEFAULT_CACHE = os.path.join(bo.REPO_ROOT, ".cache", "universe-full")
ACTIVE_STRATEGIES = (bo.SWING_KEY, "box20", "ema20")
ARCHIVE_INTERVAL_DAYS = 20
MONTHLY_HISTORY_MONTHS = 6
MONTHLY_STRATEGIES = tuple(spec for spec in bo.STRATEGIES if spec["key"] in ACTIVE_STRATEGIES)


def params_meta():
    params = bo.params_meta()
    params["strategies"] = [spec for spec in params["strategies"] if spec["key"] in ACTIVE_STRATEGIES]
    params["strategies"].sort(key=lambda spec: ACTIVE_STRATEGIES.index(spec["key"]))
    params["windows"] = [window for window in params["windows"] if window["key"] == "1m"]
    params["monthlyHistory"] = {"intervalMonths": 1, "months": MONTHLY_HISTORY_MONTHS, "defaultVisibleMonths": 5}
    params["entry"] = "波段前高突破以 max(開盤價, 波段高點) 作理論停買進場價；箱型與20EMA以訊號日收盤進場"
    return params


def load_inputs(cache_dir, today):
    """完整名單是分母；只讀取已完成冷啟動的股票，不以現有 CSV 檔名縮小名單。"""
    lists = uc.read_json(os.path.join(cache_dir, "lists.json"), {}) or {}
    state = uc.load_state(cache_dir)
    inputs = {}
    for key, exchanges in (("tw", ("TWSE", "TPEx")), ("us", ("US",))):
        listing = {}
        dates = []
        sources = []
        for exchange in exchanges:
            part = lists.get(exchange) or {}
            items = part.get("items")
            try:
                age = (datetime.fromisoformat(today).date() - datetime.fromisoformat(part["fetchedAt"][:10]).date()).days
            except (KeyError, TypeError, ValueError):
                age = LIST_MAX_AGE_DAYS + 1
            if not isinstance(items, dict) or len(items) < MIN_LIST[exchange] or not 0 <= age <= LIST_MAX_AGE_DAYS:
                raise ValueError(f"{exchange} 全市場名單不足或已過期；不使用部分股票名單取代")
            listing.update(items)
            dates.append(part["fetchedAt"][:10])
            sources.append(str(part.get("source") or exchange))
        frames = {}
        for symbol in sorted(listing):
            if not isinstance(symbol, str) or os.path.basename(symbol) != symbol or "\\" in symbol:
                raise ValueError("全市場名單含無效股票代號")
            entry = state["symbols"].get(symbol)
            if not uc.is_complete(entry):
                continue
            bars = load_daily_bars(uc.bars_path(cache_dir, symbol), bo.PRICE_COLUMNS)
            if bars is not None:
                frames[symbol] = bars
        inputs[key] = {
            "frames": frames, "universe": LABELS[key], "listed": len(listing),
            "listingAsOf": min(dates), "listingSource": "、".join(sources),
        }
    return inputs



def monthly_history(spec, market_key, latest, months=MONTHLY_HISTORY_MONTHS):
    """Recalculate each month as it could be measured on that date.

    Calendar targets are anchored to the latest available session (not generatedAt).
    Holidays move backwards to the last available market session. Every calculation
    truncates bars at that session, so later prices never mature an earlier signal.
    The current listing is retained as denominator and its survivorship bias is
    disclosed by the UI; historical coverage gaps produce no percentage.
    """
    reference = pd.Timestamp(latest["asOf"])
    frames = {symbol: bars[bars.index.day_of_week < 5] for symbol, bars in spec["frames"].items()}
    frames = {symbol: bars for symbol, bars in frames.items() if not bars.empty}
    present = bo.present_matrix(frames) if frames else None
    points = []
    for offset in range(months, -1, -1):
        target = reference - pd.DateOffset(months=offset)
        calendar = bo.trading_days(present, target) if present is not None else []
        as_of = calendar[-1] if len(calendar) else None
        point = {"targetDate": target.strftime("%Y-%m-%d"), "asOf": None,
                 "evaluated": 0, "universeCount": spec["listed"],
                 "status": "unavailable", "strategies": {}}
        if as_of is not None:
            # Truncate before passing to the engine as an additional no-lookahead
            # boundary (including calendar construction and anomaly detection).
            clipped = {symbol: bars.loc[bars.index <= as_of] for symbol, bars in frames.items()}
            clipped = {symbol: bars for symbol, bars in clipped.items() if not bars.empty}
            measured = latest if offset == 0 else bo.build_market(clipped, spec["universe"], market_key, as_of=as_of,
                strategy_specs=MONTHLY_STRATEGIES, window_specs=(("1m", 21, "近1月"),))
            if measured:
                point.update(asOf=as_of.strftime("%Y-%m-%d"), evaluated=measured["evaluated"])
                sufficient = spec["listed"] > 0 and measured["evaluated"] / spec["listed"] >= MIN_COVERAGE
                point["status"] = "ok" if sufficient else "insufficient"
                if sufficient:
                    point["strategies"] = {key: value for key, value in measured["windows"].get("1m", {}).get("strategies", {}).items()
                                           if key in ACTIVE_STRATEGIES}
        points.append(point)
    return {"intervalMonths": 1, "referenceDate": reference.strftime("%Y-%m-%d"), "points": points}


def build_payload(inputs, generated_at):
    payload = bo.build_breakouts_payload(inputs, generated_at)
    # The report covers different exchange calendars. Retain each market's asOf;
    # name the combined snapshot after its newest data date, never generatedAt.
    payload["asOf"] = max((market["asOf"] for market in payload["markets"].values()), default=None)
    payload["params"] = params_meta()
    for key, market in payload["markets"].items():
        spec = inputs[key]
        loaded = len(spec["frames"])
        market.update(
            scope="full", universeCount=spec["listed"],
            coverage={"loaded": loaded, "missing": spec["listed"] - loaded},
            listingAsOf=spec["listingAsOf"], listingSource=spec["listingSource"],
        )
        market["monthly"] = monthly_history(spec, key, market) if spec["frames"] else None
        for window in market["windows"].values():
            window["strategies"] = {name: window["strategies"][name] for name in ACTIVE_STRATEGIES}
    return payload


def insufficient_markets(payload):
    problems = bo.insufficient_markets(payload)
    for key, market in payload["markets"].items():
        listed = market["universeCount"]
        coverage = market["evaluated"] / listed if listed else 0
        if coverage < MIN_COVERAGE:
            problems.append(f"{key.upper()} 全市場有效日線 {market['evaluated']}/{listed}（{coverage:.1%}），未達 {MIN_COVERAGE:.0%}")
    return problems


def archive_snapshot(report_path, as_of):
    """Keep one immutable byte-for-byte report every 20 calendar days.

    A temporary file + exclusive hard link publishes complete JSON without ever
    replacing an existing archive. Only staging files are removed, never archives.
    """
    report_path = Path(report_path)
    report_date = date.fromisoformat(as_of)
    if report_date.isoformat() != as_of:
        raise ValueError("Snapshot asOf must be YYYY-MM-DD")
    archive_dir = report_path.parent / "breakouts-archive"
    dates = [date.fromisoformat(p.stem) for p in archive_dir.glob("*.json")]
    if dates and (report_date - max(dates)).days < ARCHIVE_INTERVAL_DAYS:
        return None
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / f"{as_of}.json"
    descriptor, staging = tempfile.mkstemp(prefix=".snapshot-", suffix=".tmp", dir=archive_dir)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(report_path.read_bytes())
        try:
            os.link(staging, target)
        except FileExistsError:
            return None
    finally:
        os.unlink(staging)
    return target


def write_archive_index(report_path):
    """Discover saved reports without changing their immutable bytes.

    Keep the mutable catalog beside the latest report, outside the append-only
    archive directory. A date is listed only when the corresponding JSON report
    has that same data date and an explicitly full-market payload.
    """
    report_path = Path(report_path)
    snapshots = []
    for path in sorted((report_path.parent / "breakouts-archive").glob("*.json"), reverse=True):
        try:
            as_of = date.fromisoformat(path.stem).isoformat()
            if path.stem != as_of:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            markets = {key: market["asOf"] for key, market in payload["markets"].items()
                       if key in LABELS and market.get("scope") == "full"
                       and date.fromisoformat(market["asOf"]).isoformat() == market["asOf"]}
            if payload.get("asOf") != as_of or not markets:
                continue
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
        snapshots.append({"asOf": as_of, "generatedAt": payload.get("generatedAt"), "markets": markets})
    target = report_path.parent / "breakouts-history.json"
    write_json_atomic(str(target), {"version": 1, "intervalDays": ARCHIVE_INTERVAL_DAYS, "snapshots": snapshots})
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", default=os.environ.get("UNIVERSE_CACHE_DIR") or DEFAULT_CACHE)
    parser.add_argument("--out", default=bo.DEFAULT_OUT)
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        inputs = load_inputs(args.cache_dir, now.date().isoformat())
        payload = build_payload(inputs, now.isoformat())
        problems = insufficient_markets(payload)
        if problems:
            raise ValueError("；".join(problems))
    except (OSError, ValueError, TypeError) as exc:
        print(f"❌ 全市場進場成功率未更新，保留原檔：{exc}")
        # Existing saved results remain discoverable even if today's coverage
        # gate prevents publishing a new report.
        try:
            write_archive_index(args.out)
        except (OSError, ValueError, TypeError) as index_exc:
            print(f"❌ 歷史結果清單未更新：{index_exc}")
        return 1
    try:
        write_json_atomic(args.out, payload)
        snapshot = archive_snapshot(args.out, payload["asOf"])
        if snapshot:
            print(f"✅ 已保留 20 日勝率快照：{snapshot}")
        write_archive_index(args.out)
    except (OSError, ValueError, TypeError) as exc:
        print(f"❌ 全市場進場成功率／快照發布失敗（既有快照不變）：{exc}")
        return 1
    for key, market in payload["markets"].items():
        print(f"✅ {key.upper()} 全市場進場成功率：{market['asOf']}，有效日線 {market['evaluated']}/{market['universeCount']} 檔，"
              f"缺漏 {market['coverage']['missing']}、未成交／停更 {market['stale']} 檔")
        for window_key, window in market["windows"].items():
            for strategy in ACTIVE_STRATEGIES:
                stats = window["strategies"][strategy]
                comparison = stats['comparison']
                print(f"   {window_key} {strategy}: 嘗試 {comparison['n']}，完整 {comparison['completeN']}，等待 {comparison['pendingN']}，缺日 {comparison['missingN']}，當日失敗 {comparison['commonFailedN']}")
                for rule, value in comparison['rules'].items():
                    print(f"      {rule}: {value['wins']}/{value['evaluatedN']}，成功率 {value['winRate']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
