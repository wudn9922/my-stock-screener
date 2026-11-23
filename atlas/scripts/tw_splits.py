"""
Taiwan stock splits / reverse splits / par-value changes (股票分割／反分割／面額變更) and the inferred-split fallback.
Standard library only. Mirror of the TS rules in atlas/src/fundamentals/epsBasis.ts (splitEventsOf / inferSplits).

FinMind EPS are as published, so the periods printed before a split (5904.TWO 寶雅: par 10 → 1, Q2 2026 EPS 8.15
on 106.5M shares, Q3 2026 0.95 on 1,069M) sit on the old share basis. The events are applied as split events by the
existing implied-share rule (build_growth.restate_splits / epsBasis.ts splitFactors): a period is divided by the ratio
only where its implied share count (net income ÷ EPS) confirms it is still on the pre-split basis, so a report that
already restated its comparatives is never divided twice. The reported value is never overwritten.

Sources (FinMind SDK DataLoader docstrings and finmind.github.io tutor/TaiwanMarket/Fundamental, read 2026-11-23;
the API itself is not reachable from the build sandbox, so the exact answers are unverified here):
  TaiwanStockSplitPrice (台股分割後參考價, "Free"): date (分割日期), stock_id, type (分割類型: 分割 / 反分割),
    before_price (分割前價格), after_price (分割後價格), max_price, min_price, open_price.
    Docs sample: 0050 2025-06-18 分割 188.65 → 47.16 (1 拆 4); 00632R 2024-12-11 反分割 3.28 → 22.96 (7 併 1).
  TaiwanStockParValueChange (變更面額恢復買賣參考價, "Free", 2020-01-01 ~): date (恢復買賣日), stock_id, stock_name,
    before_close (停止買賣前收盤價格), after_ref_close (恢復買賣參考價), after_ref_max, after_ref_min, after_ref_open.
    Docs sample: 8070 長華 2020-08-17 190 → 19 (par 10 → 1), 6531 愛普 2021-10-18 750 → 375 (10 → 5).
Both are fetched per stock (data_id); rows of another stock_id are ignored in case the server ignores data_id.

Rules (documented, tolerant):
  - ratio = new shares ÷ old shares = price before ÷ price after (a par change from 10 to 1 gives 10 shares per old
    share). Rounded to a whole number n or 1/n (or n.5 / 1/n.5) when within RATIO_SNAP (1%) of it, else 6 decimals.
  - kind: "par" (ParValueChange), "split" (ratio > 1) or "reverse" (ratio < 1) for SplitPrice; a type text that
    contradicts the prices (反分割 with ratio > 1, or 分割 with ratio < 1) is kept unapplied and logged.
  - applied: a valid date and a usable ratio (>= MIN_SPLIT_RATIO or <= 1 / MIN_SPLIT_RATIO, as for Yahoo events).
  - The raw price fields are kept for audit. A Yahoo split event within 7 days of a FinMind event is the same event
    (the FinMind ratio wins).

Inferred splits (fallback, infer_splits): with no event recorded, consecutive quarterly implied share counts (on the
配股／減資-restated basis) that jump by a near-integer factor n >= 2 (or 1/n) are treated as a split, marked inferred
with the evidence, when ALL of these hold (measured on the 1,986 Taiwan files of 2026-11-23: 11 flags, every one a
clean 2/4/10x step; 2006.TW-style constant minority-interest offsets, small issuances and one-quarter blips never fire):
  - both counts from |EPS| >= INFER_MIN_EPS and net income of the EPS's sign; period ends <= INFER_MAX_GAP_DAYS apart;
  - |jump ÷ n − 1| <= INFER_TOLERANCE + 0.005 ÷ |EPS| of each side (EPS are printed to the cent);
  - the old level is stable: the jump's older period and the (up to) 3 before it, at least INFER_MIN_BEFORE periods,
    all within ±INFER_STABLE of their median; the new level too: the newer period and the (up to) 3 after it;
  - revenue continues: median revenue after ÷ before within [1 / INFER_REVENUE, INFER_REVENUE] (a merger or a large
    issuance brings a business with it: 5314.TWO 2025, 20x shares with 5x revenue, is not a split);
  - a jump at the newest period (nothing after it yet: 5904.TWO) also needs positive net income on both sides;
  - no recorded split, no 配股 of >= 5 NT$ (>= 1.5x) and no capital reduction other than a restated loss offset
    between the older period's end and the newer period's report due date.
The inferred event is dated the day after the older period's end (the basis changed somewhere between that day and the
newer period's report); restate_splits then applies it with the same implied-share confirmation.
"""
from datetime import date, timedelta

DATASET_SPLIT = "TaiwanStockSplitPrice"
DATASET_PAR = "TaiwanStockParValueChange"
DATASETS = (DATASET_SPLIT, DATASET_PAR)
SOURCE = "FinMind TaiwanStockSplitPrice + TaiwanStockParValueChange"
RULE = ("股票分割／反分割／面額變更：比例 = 新股數 ÷ 舊股數 = 分割（停止買賣）前價格 ÷ 分割後（恢復買賣）參考價，"
        "差距 1% 內取整數或其倒數；比例 ≥ 1.5 或 ≤ 1/1.5 才調整。期別 EPS 只在其隱含股數（淨利 ÷ EPS）證實仍為分割前股數時"
        "才除以比例，已追溯調整的財報不會再除一次。")

MIN_SPLIT_RATIO = 1.5
RATIO_SNAP = 0.01
SPLIT_FIELDS = ("type", "before_price", "after_price", "max_price", "min_price", "open_price")
PAR_FIELDS = ("stock_name", "before_close", "after_ref_close", "after_ref_max", "after_ref_min", "after_ref_open")

INFER_MIN_RATIO = 2
INFER_TOLERANCE = 0.01
INFER_MIN_EPS = 0.2
INFER_STABLE = 0.15
INFER_MIN_BEFORE = 3
INFER_MAX_GAP_DAYS = 200
INFER_REVENUE = 2.5
INFER_WINDOW = 4
# 配股 of at least this NT$ per share (par 10) is a >= 1.5x share change
BIG_STOCK_DIVIDEND = 5.0


def number(value):
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def day_of(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def snap_ratio(raw):
    """Price ratio → a whole n, 1/n, n.5 or 1/n.5 within RATIO_SNAP, else 6 decimals; None when not positive."""
    if raw is None or raw <= 0:
        return None
    big = raw if raw >= 1 else 1 / raw
    candidates = [round(big), round(big * 2) / 2]
    for candidate in candidates:
        if candidate >= 1 and abs(big / candidate - 1) <= RATIO_SNAP:
            return float(candidate) if raw >= 1 else round(1 / candidate, 6)
    return round(raw, 6)


def usable(ratio):
    return ratio is not None and ratio > 0 and (ratio >= MIN_SPLIT_RATIO or ratio <= 1 / MIN_SPLIT_RATIO)


def _same_stock(row, code):
    stock = str(row.get("stock_id") or "").strip().upper()
    return code is None or not stock or stock == str(code).strip().upper()


def _price_ratio(before, after):
    before, after = number(before), number(after)
    return before / after if before and after and before > 0 and after > 0 else None


def parse_split_rows(rows, code=None, log=None):
    """TaiwanStockSplitPrice rows → [{date, ratio, kind split|reverse, dataset, applied, raw}]."""
    events = []
    for row in rows or []:
        if not isinstance(row, dict) or not _same_stock(row, code):
            continue
        day = day_of(row.get("date"))
        if day is None:
            continue
        raw_ratio = _price_ratio(row.get("before_price"), row.get("after_price"))
        ratio = snap_ratio(raw_ratio)
        kind = "split" if ratio is not None and ratio > 1 else "reverse" if ratio is not None and ratio < 1 else "split"
        text = str(row.get("type") or "")
        stated = "reverse" if "反" in text or "REVERSE" in text.upper() else "split" if text.strip() else None
        consistent = stated is None or stated == kind
        event = {"date": day.isoformat(), "ratio": ratio, "kind": kind, "dataset": DATASET_SPLIT,
                 "applied": usable(ratio) and consistent,
                 "raw": {key: row.get(key) for key in SPLIT_FIELDS if key in row}}
        if log is not None and not event["applied"]:
            log(f"split {row.get('stock_id') or code or '?'} {event['date']}: type {text!r}, ratio {ratio} not applied")
        events.append(event)
    return events


def parse_par_rows(rows, code=None, log=None):
    """TaiwanStockParValueChange rows → [{date, ratio, kind "par", dataset, applied, raw}]."""
    events = []
    for row in rows or []:
        if not isinstance(row, dict) or not _same_stock(row, code):
            continue
        day = day_of(row.get("date"))
        if day is None:
            continue
        ratio = snap_ratio(_price_ratio(row.get("before_close"), row.get("after_ref_close")))
        event = {"date": day.isoformat(), "ratio": ratio, "kind": "par", "dataset": DATASET_PAR,
                 "applied": usable(ratio), "raw": {key: row.get(key) for key in PAR_FIELDS if key in row}}
        if log is not None and not event["applied"]:
            log(f"par value change {row.get('stock_id') or code or '?'} {event['date']}: ratio {ratio} not applied")
        events.append(event)
    return events


def parse_events(split_rows, par_rows, code=None, log=None):
    """Both datasets → ascending events; one event per day (a day in both datasets keeps the par-value row, with the
    split row's raw fields under raw["split"])."""
    by_day = {}
    for event in parse_split_rows(split_rows, code, log):
        by_day[event["date"]] = event
    for event in parse_par_rows(par_rows, code, log):
        other = by_day.get(event["date"])
        if other is not None and other["dataset"] == DATASET_SPLIT:
            event = dict(event, raw=dict(event["raw"], split=other["raw"]))
        by_day[event["date"]] = event
    return [by_day[day] for day in sorted(by_day)]


def block(events, checked):
    """The document's top-level "twSplits" block (events as stored, source, rule and check day for audit)."""
    return {"source": SOURCE, "checked": checked, "rule": RULE, "events": events}


def events_of(document):
    """A financials document → its stored FinMind split events, or None when the block is absent / malformed."""
    stored = document.get("twSplits") if isinstance(document, dict) else None
    events = stored.get("events") if isinstance(stored, dict) else None
    return events if isinstance(events, list) else None


def split_events(document):
    """The applied FinMind events as (day, ratio), oldest first."""
    result = []
    for event in events_of(document) or []:
        if isinstance(event, dict) and event.get("applied") and day_of(event.get("date")) and usable(number(event.get("ratio"))):
            result.append((day_of(event["date"]), float(event["ratio"])))
    return sorted(result)


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def infer_splits(points, blockers):
    """
    points: quarterly periods, any order, as dicts {"day": date, "eps": EPS on the 配股／減資-restated basis, "ni": net
    income, "rev": revenue, "due": report due date}; blockers: dates of recorded share events (splits, big 配股,
    unrestated capital reductions). → [{"day", "ratio", "evidence": {...}}] for the jumps the module rules accept.
    """
    usable_points = []
    for point in sorted(points, key=lambda p: p["day"]):
        eps, income = point.get("eps"), point.get("ni")
        if eps is None or income is None or income == 0 or abs(eps) < INFER_MIN_EPS:
            continue
        shares = income / eps
        if shares > 0:
            usable_points.append(dict(point, shares=shares))
    found = []
    for index in range(1, len(usable_points)):
        older, newer = usable_points[index - 1], usable_points[index]
        if (newer["day"] - older["day"]).days > INFER_MAX_GAP_DAYS:
            continue
        jump = newer["shares"] / older["shares"]
        size = jump if jump >= 1 else 1 / jump
        whole = round(size)
        if whole < INFER_MIN_RATIO:
            continue
        if abs(size / whole - 1) > INFER_TOLERANCE + 0.005 / abs(older["eps"]) + 0.005 / abs(newer["eps"]):
            continue
        before = usable_points[max(0, index - INFER_WINDOW):index]
        after = usable_points[index:index + INFER_WINDOW]
        if len(before) < INFER_MIN_BEFORE:
            continue
        level_before, level_after = _median([p["shares"] for p in before]), _median([p["shares"] for p in after])
        if any(abs(p["shares"] / level_before - 1) > INFER_STABLE for p in before):
            continue
        if any(abs(p["shares"] / level_after - 1) > INFER_STABLE for p in after):
            continue
        revenue_before = [p["rev"] for p in before if p.get("rev")]
        revenue_after = [p["rev"] for p in after if p.get("rev")]
        if not revenue_before or not revenue_after or _median(revenue_before) <= 0:
            continue
        revenue = _median(revenue_after) / _median(revenue_before)
        if not 1 / INFER_REVENUE <= revenue <= INFER_REVENUE:
            continue
        if len(after) < 2 and not (older["ni"] > 0 and newer["ni"] > 0):
            continue
        due = newer.get("due") or newer["day"]
        if any(older["day"] < day <= due for day in blockers):
            continue
        ratio = float(whole) if jump >= 1 else round(1 / whole, 6)
        found.append({"day": older["day"] + timedelta(days=1), "ratio": ratio, "evidence": {
            "before": older["day"].isoformat(), "after": newer["day"].isoformat(),
            "sharesBefore": round(older["shares"]), "sharesAfter": round(newer["shares"]),
            "periodsBefore": len(before), "periodsAfter": len(after)}})
    return found
