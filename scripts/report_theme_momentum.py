"""Offline Markdown audit of theme weights and missing-price accounting.

  python scripts/report_theme_momentum.py
  python scripts/report_theme_momentum.py --market tw --prices /path/to/prices.json

Optional prices JSON is {Yahoo symbol: [[YYYY-MM-DD, close], ...]}, as consumed by
build_themes.build_payload(). No network, generated-data refresh, parameter tuning, or OOS
performance claim. Without raw prices the report deliberately omits numerical gap diagnostics.
"""
import argparse
import json
from datetime import datetime, timezone

import build_themes


def effective_m3_terms(benchmark_m3):
    """Coefficient and common intercept with returns expressed in percentage points."""
    denominator = 1 + benchmark_m3 / 100
    if denominator <= 0:
        raise ValueError("benchmark 3-month return must exceed -100%")
    weights = build_themes.MOMENTUM_WEIGHTS
    return (weights["m3"] + weights["rs3m"] / denominator,
            -weights["rs3m"] * benchmark_m3 / denominator)


def render_report(prices=None, config=None):
    config = config or build_themes.default_config()
    weights = build_themes.MOMENTUM_WEIGHTS
    lines = [
        "# 題材動能與缺價處理審查",
        "",
        "決定：保留現有三個月動能偏重與預設權重。這是本次維持基準的決策，"
        "不是對歷史設計意圖或投資績效的推定；rs3m 不視為同市場內的獨立排名訊號。",
        "",
        f"權重維持 w1={weights['w1']:.2f}、m1={weights['m1']:.2f}、"
        f"m3={weights['m3']:.2f}、rs3m={weights['rs3m']:.2f}。",
        "",
        "以百分點表示題材三個月報酬 m 與同市場基準三個月報酬 b：",
        "",
        "`rs3m = (m - b) / (1 + b/100)`",
        "",
        "`score = 0.20*w1 + 0.25*m1 + [0.30 + 0.25/(1+b/100)]*m - 0.25*b/(1+b/100)`",
        "",
        "同一市場、同一日的 b 相同且大於 -100%，所以 rs3m 與 m3 的單項排名完全等價；"
        "最後一項對所有題材相同。兩個名目權重相加為 0.55，但精確的 m3 係數是 "
        "`0.30 + 0.25/(1+b/100)`，只有 b=0 時才等於 0.55。直接將它改成固定 0.55，"
        "可能改變與 w1、m1 合成後的排名。顯示分數四捨五入也可能造成同分。",
        "",
        "缺價政策：最多沿用 3 個基準交易日，超過即為缺值；最新資料仍受既有 3 個日曆日"
        "新鮮度門檻限制，週末與假日不會放寬該門檻。此上限是保守的資料政策，未以績效調參。",
        "",
        "當日等權平均只納入前、後兩個交易日都有真實收盤的成分股。缺價當日與恢復首日"
        "均排除該股，避免用零報酬稀釋平均，亦避免把跨日漲跌集中算成恢復當日報酬。"
        "這代表跨缺口報酬不可識別，並非漲跌消失；該曲線不是缺價股票的實際持倉損益。"
        "區間報酬要求兩端真實報價；50 日均線判斷要求完整 50 日真實收盤。",
        "",
        "保留中間所有基準交易日，只移除尾端多數成分股尚無報價的日期。當整個題材當日"
        "都沒有可用日報酬時，曲線沿用前值以保持舊前端相容，並以 dataQuality 的 "
        "seriesUnavailableDates 與 seriesReturnContributors 標示，不能解讀為真實零波動。",
        "",
    ]
    if prices is None:
        lines += [
            "未提供逐日原始價格：不推算缺口數、波動改善或排名績效。可用 --prices 提供離線價格檔重現資料品質統計。",
            "",
        ]
    else:
        bench_symbol = config.get("benchmark", build_themes.BENCHMARK)
        bench_bars = prices.get(bench_symbol) or []
        if not bench_bars:
            raise ValueError(f"missing benchmark {bench_symbol}")
        now = datetime.fromisoformat(bench_bars[-1][0]).replace(tzinfo=timezone.utc)
        payload = build_themes.build_payload(prices, config, now)
        # Use the original closes, never the payload's rounded return, for the formula audit.
        benchmark = dict(bench_bars)
        index = next(i for i, (day, _) in enumerate(bench_bars) if day == payload["asOf"])
        b = (benchmark[payload["asOf"]] / bench_bars[index - build_themes.PERIOD_BARS["m3"]][1] - 1) * 100
        coefficient, common = effective_m3_terms(b)
        lines += [
            f"輸入資料日期：{payload['asOf']}；基準：{bench_symbol}；"
            f"b={b:.6f}%；精確 m3 係數={coefficient:.6f}，共同截距={common:.6f}。",
            "",
            f"最新可用成分股覆蓋率：{payload['coverage']:.1%}；"
            f"完全缺資料 {len(payload['skipped']['missing'])} 檔，過期 {len(payload['skipped']['stale'])} 檔。",
            "",
            "| 題材 | 最新可用成分股 | 曲線內沿用觀測 | 超限缺值 | 無可用日報酬日期數 |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for theme in payload["themes"]:
            quality = theme.get("dataQuality", {})
            lines.append(f"| {theme['key']} | {theme['validCount']}/{theme['count']} | "
                         f"{quality.get('carriedObservations', 0)} | {quality.get('expiredObservations', 0)} | "
                         f"{len(quality.get('seriesUnavailableDates', []))} |")
        lines += [
            "",
            "缺口統計限於曲線最近 126 點與最新仍可用的成分股；上市前缺值不算沿用，"
            "同股若屬多個題材會各自計入。完全缺資料與最新過期成分股已排除，"
            "因此表中零缺口不代表全市場資料完整。",
            "",
        ]
    lines += [
        "驗證邊界：目前題材名單是靜態現行名單，沒有逐日成分變更、下市股、"
        "歷史可得性時間戳與可交易成本資料。本報告只審查代數與缺價會計，不是樣本外績效驗證；"
        "尚無足夠證據主張權重較佳、策略已採納或缺價修正提高預測力。",
        "",
    ]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=build_themes.MARKETS, default="us")
    parser.add_argument("--prices", help="offline symbol -> dated close arrays JSON; omitted means policy-only report")
    args = parser.parse_args(argv)
    try:
        prices = None
        if args.prices:
            with open(args.prices, encoding="utf-8") as handle:
                prices = json.load(handle)
        print(render_report(prices, build_themes.load_config(args.market)))
    except (OSError, ValueError, build_themes.InsufficientData, build_themes.ConfigError) as exc:
        parser.exit(1, f"theme audit failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
