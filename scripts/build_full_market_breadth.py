#!/usr/bin/env python3
"""全市場寬度：唯讀完整市場名單與日 K 快取，不退回 S&P 500 或 data/ 部分名單。"""

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_breadth as breadth  # noqa: E402
import build_full_market_breakouts as full  # noqa: E402


def build_payload(inputs, generated_at):
    # Use exactly the entry-success universe, including small caps and TPEx.
    specs = {
        key: {**spec, "segment_of": {
            symbol: "tpex" if symbol.endswith(".TWO") else "twse"
            for symbol in spec["frames"]
        } if key == "tw" else None}
        for key, spec in inputs.items()
    }
    payload = breadth.build_breadth_payload(specs, generated_at)
    for key, market in payload["markets"].items():
        spec = inputs[key]
        loaded = len(spec["frames"])
        market.update(
            scope="full", universeCount=spec["listed"],
            coverage={"loaded": loaded, "missing": spec["listed"] - loaded,
                      "notTraded": loaded - market["segments"]["all"]["count"]},
            listingAsOf=spec["listingAsOf"], listingSource=spec["listingSource"],
        )
    return payload


def insufficient_markets(payload):
    problems = []
    for key in ("tw", "us"):
        market = payload["markets"].get(key)
        if not market:
            problems.append(f"{key.upper()} 全市場沒有可評估日線")
            continue
        count, listed = market["segments"]["all"]["count"], market["universeCount"]
        if not listed or count / listed < full.MIN_COVERAGE:
            problems.append(f"{key.upper()} 全市場有效日線 {count}/{listed}，未達 {full.MIN_COVERAGE:.0%}")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", default=os.environ.get("UNIVERSE_CACHE_DIR") or full.DEFAULT_CACHE)
    parser.add_argument("--out", default=breadth.DEFAULT_OUT)
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        payload = build_payload(full.load_inputs(args.cache_dir, now.date().isoformat()), now.isoformat())
        problems = insufficient_markets(payload)
        if problems:
            raise ValueError("；".join(problems))
        breadth.write_json_atomic(args.out, payload)
    except (OSError, ValueError, TypeError) as exc:
        print(f"❌ 全市場寬度未更新，保留原檔：{exc}", flush=True)
        return 1
    for key, market in payload["markets"].items():
        print(f"✅ {key.upper()} 全市場寬度：{market['asOf']}，"
              f"有效 {market['segments']['all']['count']}/{market['universeCount']} 檔，"
              f"快取缺漏 {market['coverage']['missing']} 檔", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
