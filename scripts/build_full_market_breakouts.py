#!/usr/bin/env python3
"""全市場進場成功率：只讀 universe-full 的完整名單與日線快取，不連網、不退回 S&P 500。

共用 build_breakouts 的訊號與成熟樣本計算；成功＝進場後至少經過 3 個交易日，資料日收盤高於進場價。
波段突破以 max(開盤價, 已確認波段高點) 作理論停買價格；箱型與20EMA以訊號日收盤進場。
資料缺漏、未成熟與沒有訊號都不當成失敗。
名單過期、快取不足或有效日線涵蓋率不足時保留既有報表，回傳失敗。
"""

import argparse
import os
import sys
from datetime import datetime, timezone

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


def params_meta():
    params = bo.params_meta()
    params["strategies"] = [spec for spec in params["strategies"] if spec["key"] in ACTIVE_STRATEGIES]
    params["strategies"].sort(key=lambda spec: ACTIVE_STRATEGIES.index(spec["key"]))
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


def build_payload(inputs, generated_at):
    payload = bo.build_breakouts_payload(inputs, generated_at)
    payload["params"] = params_meta()
    for key, market in payload["markets"].items():
        spec = inputs[key]
        loaded = len(spec["frames"])
        market.update(
            scope="full", universeCount=spec["listed"],
            coverage={"loaded": loaded, "missing": spec["listed"] - loaded},
            listingAsOf=spec["listingAsOf"], listingSource=spec["listingSource"],
        )
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
        write_json_atomic(args.out, payload)
    except (OSError, ValueError, TypeError) as exc:
        print(f"❌ 全市場進場成功率未更新，保留原檔：{exc}")
        return 1
    for key, market in payload["markets"].items():
        print(f"✅ {key.upper()} 全市場進場成功率：{market['asOf']}，有效日線 {market['evaluated']}/{market['universeCount']} 檔，"
              f"缺漏 {market['coverage']['missing']}、未成交／停更 {market['stale']} 檔")
        for window_key, window in market["windows"].items():
            for strategy in ACTIVE_STRATEGIES:
                stats = window["strategies"][strategy]
                print(f"   {window_key} {strategy}: 成功 {stats['wins']}/{stats['maturedN']}，成功率 {stats['winRate']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
