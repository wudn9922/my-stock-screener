"""Causal daily OHLCV research. No new quotes, interpolation, currency mixing or hidden score."""

from __future__ import annotations
from statistics import mean
import math


def positive(x):
    return isinstance(x, (float, int)) and math.isfinite(x) and x > 0


def avg(values):
    return mean(values) if values else None


def ratio(n, d):
    return n / d * 100 if d else None


def change(a, b):
    return (a / b - 1) * 100 if positive(a) and positive(b) else None


def volume_features(rows):
    """RVOL excludes current day. Non-trading rows break streaks; gaps/actions invalidate return.

    Cache OHLC is split adjusted, dividends are not adjusted. Unlabelled >40% jumps
    are conservatively excluded as suspected adjustment mismatch, never auto-repaired.
    """
    out, prior, prev = [], [], None
    for row in sorted(rows, key=lambda r: r["date"]):
        valid = (
            positive(row.get("close"))
            and positive(row.get("volume"))
            and all(positive(row.get(k)) for k in ("open", "high", "low"))
        )
        valid = (
            valid
            and row["high"] >= max(row["open"], row["close"], row["low"])
            and row["low"] <= min(row["open"], row["close"])
        )
        action = bool(row.get("action"))
        ret = change(row.get("close"), prev.get("close")) if prev else None
        if action or (ret is not None and abs(ret) > 40):
            # Unknown share adjustment cannot safely compare pre/post-action volume.
            prior.clear()
            valid = False
        # A missing/zero session cannot create a daily return across a suspension.
        usable = (
            valid
            and prev is not None
            and prev.get("valid")
            and not action
            and ret is not None
            and abs(ret) <= 40
        )
        prior20 = prior[-20:]
        base = avg([r["volume"] for r in prior20]) if len(prior20) == 20 else None
        amount = (
            row.get("amount")
            if positive(row.get("amount"))
            else row["close"] * row["volume"] if valid else None
        )
        f = dict(
            date=row["date"],
            officialVolume=row.get("officialVolume"),
            close=row.get("close"),
            volume=row.get("volume"),
            valid=bool(valid),
            returnPct=ret if usable else None,
            priorAvg20=base,
            rvol=row["volume"] / base if valid and base else None,
            amount=amount,
            amountBasis=(
                "official"
                if positive(row.get("amount"))
                else "close-times-volume-proxy"
            ),
            rangePct=(
                (row["high"] - row["low"]) / prev["close"] * 100 if usable else None
            ),
            missingReason=(
                None
                if usable
                else "insufficient-prior-session/zero-volume/missing-OHLC/corporate-action-or-large-jump"
            ),
        )
        out.append(f)
        if valid:
            prior.append(row)
        prev = dict(row, valid=valid)
    return out


def structure(features, window):
    """Directional volume compares adjacent sessions, independently of RVOL20."""
    sample = features[-window:]
    usable = [
        (r, features[index - 1])
        for index, r in enumerate(features)
        if index >= max(1, len(features) - window)
        and r["returnPct"] is not None and r["valid"]
        and features[index - 1]["valid"]
        and positive(r["volume"]) and positive(features[index - 1]["volume"])
    ]
    up = [(r, prev) for r, prev in usable if r["returnPct"] > 0]
    down = [(r, prev) for r, prev in usable if r["returnPct"] < 0]
    out = dict(
        window=window,
        observations=len(usable),
        lowConfidence=len(usable) < max(5, math.ceil(window * 0.8)),
    )
    for name, days, predicate in [
        ("upShrink", up, lambda r, prev: r["volume"] < prev["volume"]),
        ("upExpand", up, lambda r, prev: r["volume"] > prev["volume"]),
        ("downExpand", down, lambda r, prev: r["volume"] > prev["volume"]),
        ("downShrink", down, lambda r, prev: r["volume"] < prev["volume"]),
    ]:
        n = sum(predicate(r, prev) for r, prev in days)
        out[name] = dict(numerator=n, denominator=len(days), pct=ratio(n, len(days)))
    continuous = len(features) > window and all(
        r["valid"] and r["returnPct"] is not None for r in sample
    )
    out["priceChangePct"] = (
        change(sample[-1]["close"], features[-window - 1]["close"])
        if continuous
        else None
    )
    out["volumeChangePct"] = (
        change(sample[-1]["volume"], features[-window - 1]["volume"])
        if continuous
        else None
    )
    return out


def volume_stats(rows):
    f = volume_features(rows)
    if not f:
        return None
    valid = [r for r in f if r["valid"]]
    latest = f[-1]
    result = dict(
        asOf=latest["date"],
        latest=latest,
        windows={str(w): structure(f, w) for w in (5, 10, 20)},
        features=f[-21:],
        volumeAverages={},
        amountAverages={},
        volumeTotals={},
        amountTotals={},
    )
    for w in (5, 20, 60):
        sample = f[-w:]
        good = [r for r in sample if r["valid"]]
        result["volumeAverages"][str(w)] = dict(
            value=avg([r["volume"] for r in good]), observations=len(good), requested=w
        )
        if w in (5, 20):
            result["amountAverages"][str(w)] = dict(
                value=avg([r["amount"] for r in good]),
                officialObservations=sum(r["amountBasis"] == "official" for r in good),
                observations=len(good),
                requested=w,
            )
            result["volumeTotals"][str(w)] = (
                sum(r["volume"] for r in good) if good else None
            )
            result["amountTotals"][str(w)] = (
                sum(r["amount"] for r in good) if good else None
            )
    prior = [r for r in f[:-1][-252:] if r["valid"]]
    result["volumePercentile"] = (
        ratio(sum(r["volume"] <= latest["volume"] for r in prior), len(prior))
        if latest["valid"]
        else None
    )
    recent = valid[-60:]
    result["maxVolumeDate"] = (
        max(recent, key=lambda r: r["volume"])["date"] if recent else None
    )
    sample = f[-20:]
    result["zeroVolumeDays"] = sum(r["volume"] == 0 for r in sample)
    result["validDayRatio"] = ratio(sum(r["valid"] for r in sample), len(sample))
    amihud = [
        abs(r["returnPct"] / 100) / r["amount"] * 1e6
        for r in sample
        if r["returnPct"] is not None and positive(r["amount"])
    ]
    result["amihud20"] = avg(amihud)
    result["amihudObservations"] = len(amihud)
    result["amihudUnit"] = (
        "daily absolute return / daily amount × 1,000,000 (per million local currency)"
    )
    for name, predicate in [
        ("upShrinkStreak", lambda r: r["returnPct"] > 0 and r["rvol"] < 1),
        ("downExpandStreak", lambda r: r["returnPct"] < 0 and r["rvol"] > 1),
    ]:
        count = 0
        for r in reversed(f):
            if r["returnPct"] is None or r["rvol"] is None or not predicate(r):
                break
            count += 1
        result[name] = count
    result["lastAbnormalDate"] = next(
        (
            r["date"]
            for r in reversed(f)
            if r["rvol"] is not None and (r["rvol"] >= 2 or r["rvol"] <= 0.5)
        ),
        None,
    )
    result["risks"] = [
        label
        for flag, label in [
            (len(f) < 61, "歷史不足 61 個交易日"),
            (result["validDayRatio"] < 80, "有效交易日不足／可能停牌"),
            (
                (result["amountAverages"]["20"]["value"] or 0) < 1e6,
                "低成交金額（本幣門檻）",
            ),
            (not latest["valid"], "最新資料無成交或無效"),
        ]
        if flag
    ]
    return result


def enrich_turnover_snapshot(snapshot, quotes):
    """Refresh cash-only metrics from dated official inputs, preserving canonical OHLCV signals.

    The published 21 features contain all observations required by ADV5/20 and
    Amihud20, so this also works after the full-market CSV cache is evicted.
    """
    result = dict(snapshot)
    features = [dict(row) for row in snapshot["features"]]
    for row in features:
        quote = quotes.get(row["date"])
        if quote:
            row.update(
                amount=quote["amount"],
                amountBasis="official",
                officialVolume=quote["volume"],
            )
    result["features"] = features
    result["latest"] = features[-1]
    result["amountAverages"] = dict(snapshot["amountAverages"])
    result["amountTotals"] = dict(snapshot["amountTotals"])
    for window in (5, 20):
        good = [row for row in features[-window:] if row["valid"]]
        result["amountAverages"][str(window)] = dict(
            value=avg([r["amount"] for r in good]),
            observations=len(good),
            requested=window,
            officialObservations=sum(r["amountBasis"] == "official" for r in good),
        )
        result["amountTotals"][str(window)] = (
            sum(r["amount"] for r in good) if good else None
        )
    amihud = [
        abs(r["returnPct"] / 100) / r["amount"] * 1e6
        for r in features[-20:]
        if r["returnPct"] is not None and positive(r["amount"])
    ]
    result["amihud20"], result["amihudObservations"] = avg(amihud), len(amihud)
    risks = [r for r in snapshot["risks"] if r != "低成交金額（本幣門檻）"]
    if (result["amountAverages"]["20"]["value"] or 0) < 1e6:
        risks.append("低成交金額（本幣門檻）")
    result["risks"] = risks
    return result


def institutional_summary(rows, market_dates=None):
    rows = sorted(rows, key=lambda r: r["date"])
    out = {}
    for role in ("foreign", "trust", "dealer"):
        windows = {}
        for w in (1, 5, 10, 20, 60):
            dates = (market_dates or [r["date"] for r in rows])[-w:]
            subset = [r for r in rows if r["date"] in dates and r.get(role) is not None]
            windows[str(w)] = dict(
                net=sum(r[role]["net"] for r in subset) if subset else None,
                observations=len(subset),
                requested=w,
                complete=len(subset) == w,
            )
        streak, sign = 0, 0
        by_date = {r["date"]: r for r in rows}
        streak_dates = [
            d for d in (market_dates or list(by_date)) if rows and d <= rows[-1]["date"]
        ]
        for d in reversed(streak_dates):
            v = by_date.get(d, {}).get(role)
            direction = 1 if v and v["net"] > 0 else -1 if v and v["net"] < 0 else 0
            if not direction or (sign and direction != sign):
                break
            sign = direction
            streak += 1
        out[role] = dict(windows=windows, streak=streak * sign)
    return out


def cost_estimate(flows, prices, window, role, market_dates=None):
    """Positive NET buys weight market VWAP, never inventory or actual institutional fill prices."""
    dates = (market_dates or [r["date"] for r in flows])[-window:]
    prices = {r["date"]: r for r in prices}
    valid = []
    for flow in flows:
        p = prices.get(flow["date"])
        if (
            flow["date"] not in dates
            or not p
            or not positive(p.get("close"))
            or not positive(p.get("volume"))
            or flow.get(role) is None
        ):
            continue
        p = dict(p)
        quote = flow.get("market")
        if quote and positive(quote.get("volume")) and positive(quote.get("amount")):
            # Exclude mismatched share/price adjustment bases instead of mixing split vintages.
            if (
                not positive(quote.get("close"))
                or abs(p["close"] / quote["close"] - 1) > 0.005
            ):
                continue
            p.update(volume=quote["volume"], amount=quote["amount"])
        if p.get("action"):
            continue
        valid.append((flow, p))
    buys = [(flow[role]["net"], p) for flow, p in valid if flow[role]["net"] > 0]
    weight = sum(w for w, _ in buys)
    avg_price = lambda p: (
        p["amount"] / p["volume"] if positive(p.get("amount")) else p["close"]
    )
    ref = sum(w * avg_price(p) for w, p in buys) / weight if weight else None
    closes = [p["close"] for _, p in buys]
    vwaps = [avg_price(p) for _, p in buys]
    current = max(prices.values(), key=lambda p: p["date"]) if prices else None
    return dict(
        window=window,
        observations=len(valid),
        positiveDays=len(buys),
        coveragePct=ratio(len(valid), window),
        currentAsOf=current["date"] if current else None,
        currentClose=current["close"] if current else None,
        reference=ref,
        closeLow=min(closes) if closes else None,
        closeHigh=max(closes) if closes else None,
        low=min(vwaps) if vwaps else None,
        high=max(vwaps) if vwaps else None,
        distancePct=(
            change(current["close"], ref)
            if current and not current.get("action")
            else None
        ),
        priceBasis=(
            "official-market-VWAP"
            if buys and all(positive(p.get("amount")) for _, p in buys)
            else "market-close-proxy"
        ),
        days=[
            dict(
                date=p["date"],
                net=w,
                close=p["close"],
                marketAverage=avg_price(p),
                basis=(
                    "official-market-VWAP"
                    if positive(p.get("amount"))
                    else "market-close-proxy"
                ),
            )
            for w, p in buys
        ],
    )
