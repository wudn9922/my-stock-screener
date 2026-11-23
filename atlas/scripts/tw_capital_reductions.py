"""
Taiwan capital reductions (減資) and the EPS restatement they call for. Standard library only.

A capital reduction to offset losses (減資彌補虧損) cancels shares and returns no cash, so, like a reverse split, the EPS
printed in reports published BEFORE the new share count took effect is on a larger share basis than the EPS after
it: restated EPS = reported EPS ÷ (new shares ÷ old shares) for every such reduction whose trading resumption date
(恢復買賣日) falls after the period's report was due (IAS 33.64, same due dates as tw_stock_dividends.py). A cash
capital reduction (現金減資 / 退還股款: cash returned to the shareholders) and a cash capital increase (現金增資) are
NOT restated: IAS 33 treats them like a buyback / an issue at fair value. The reported value is never overwritten.

Source: FinMind TaiwanStockCapitalReductionReferencePrice (減資恢復買賣參考價, TWSE/TPEx), one row per reduction:
  date                             trading resumption day (the new share count trades from here)
  stock_id
  ClosingPriceonTheLastTradingDay  停止買賣前收盤價格
  PostReductionReferencePrice      恢復買賣參考價
  LimitUp / LimitDown              漲停 / 跌停價格
  OpeningReferencePrice            開盤競價基準 (the reference price rounded to the tick)
  ExrightReferencePrice            除權參考價
  ReasonforCapitalReduction        減資原因 (彌補虧損 / 退還股款 / 現金減資 / ...)
(FinMind SDK docstring of taiwan_stock_capital_reduction_reference_price; tier "Free", so data_id may be omitted to
get every stock in one request.)

Rules (documented, tolerant):
  - kind from the reason: "loss" when it names a loss offset (彌補虧損 / 虧損 / "loss") and no cash return; "cash" when
    it names a cash return (退還股款 / 現金 / 退還 / "cash" / "refund") and no loss offset; "mixed" when it names both;
    "other" otherwise (e.g. 註銷庫藏股, an empty reason). Only "loss" is applied; every other kind is stored for
    provenance and logged when the reason is not recognised.
  - ratio = new shares ÷ old shares. An explicit ratio field in the row (any key containing "ratio", 0 < r < 1) is
    used when present. Otherwise, for a loss offset, TWSE computes 恢復買賣參考價 = 停止買賣前收盤價 ÷ (new ÷ old), so
    ratio = ClosingPriceonTheLastTradingDay ÷ PostReductionReferencePrice (6 decimals; the reference price is printed
    to the cent, so the ratio is good to about 0.1%). A cash reduction's reference price also depends on the cash
    returned, so no ratio is derived for it (it is not applied anyway). A ratio outside (0, 1) → not applied.
"""
from datetime import date

DATASET = "TaiwanStockCapitalReductionReferencePrice"
SOURCE = "FinMind TaiwanStockCapitalReductionReferencePrice"
RULE = ("減資彌補虧損（kind loss）：還原 EPS = 公告 EPS ÷ (新股數 ÷ 舊股數)，恢復買賣日在財報應公告日之後才調整；"
        "比例取資料列的 ratio 欄，否則 = 停止買賣前收盤價 ÷ 恢復買賣參考價。現金減資／退還股款（cash）、"
        "混合（mixed）與其他原因（other）只保留紀錄，不調整（IAS 33）。")

LOSS_MARKERS = ("彌補虧損", "虧損", "LOSS", "DEFICIT")
CASH_MARKERS = ("退還股款", "現金減資", "退還", "現金", "CASH", "REFUND", "RETURN OF CAPITAL")
PRICE_FIELDS = ("ClosingPriceonTheLastTradingDay", "PostReductionReferencePrice", "LimitUp", "LimitDown",
                "OpeningReferencePrice", "ExrightReferencePrice")


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


def classify(reason):
    """減資原因 → "loss" | "cash" | "mixed" | "other"."""
    text = str(reason or "").strip().upper()
    loss = any(marker in text for marker in LOSS_MARKERS)
    cash = any(marker in text for marker in CASH_MARKERS)
    if loss and cash:
        return "mixed"
    return "loss" if loss else "cash" if cash else "other"


def explicit_ratio(row):
    """A ratio field the dataset may carry (none documented today): any key containing "ratio", 0 < r < 1."""
    for key, value in row.items():
        if "ratio" in str(key).lower():
            ratio = number(value)
            if ratio is not None and 0 < ratio < 1:
                return ratio
    return None


def ratio_of(row, kind):
    """new shares ÷ old shares (6 decimals) of a row, or None when it cannot be derived (see the module rules)."""
    ratio = explicit_ratio(row)
    if ratio is None and kind == "loss":
        close, reference = number(row.get("ClosingPriceonTheLastTradingDay")), number(row.get("PostReductionReferencePrice"))
        if close and reference and close > 0 and reference > 0:
            ratio = close / reference
    return round(ratio, 6) if ratio is not None else None


def applies(event):
    """True for a stored event that restates EPS: a loss offset with 0 < ratio < 1 and a date."""
    ratio = number(event.get("ratio")) if isinstance(event, dict) else None
    return (isinstance(event, dict) and event.get("kind") == "loss" and day_of(event.get("date")) is not None
            and ratio is not None and 0 < ratio < 1)


def parse_events(rows, log=None):
    """FinMind rows (one stock) → ascending [{date, ratio, kind, reason, applied, raw}]; rows without a valid date are
    dropped. Unknown reasons and loss offsets without a usable ratio are logged (log(message)) and kept unapplied."""
    events = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        day = day_of(row.get("date"))
        if day is None:
            continue
        reason = str(row.get("ReasonforCapitalReduction") or "").strip()
        kind = classify(reason)
        ratio = ratio_of(row, kind)
        raw = {key: row.get(key) for key in PRICE_FIELDS if key in row}
        raw.update({key: value for key, value in row.items()
                    if "ratio" in str(key).lower() and key not in raw})
        event = {"date": day.isoformat(), "ratio": ratio, "kind": kind, "reason": reason, "raw": raw}
        event["applied"] = applies(event)
        if log is not None:
            stock = row.get("stock_id") or "?"
            if kind == "other" or kind == "mixed":
                log(f"capital reduction {stock} {event['date']}: reason {reason!r} ({kind}), not applied")
            elif kind == "loss" and not event["applied"]:
                log(f"capital reduction {stock} {event['date']}: loss offset without a usable ratio ({ratio}), not applied")
        events[(event["date"], reason)] = event
    return sorted(events.values(), key=lambda e: (e["date"], e["reason"]))


def group_by_stock(rows):
    """All-market rows → {stock_id: [rows]}."""
    grouped = {}
    for row in rows or []:
        if isinstance(row, dict) and row.get("stock_id"):
            grouped.setdefault(str(row["stock_id"]).strip().upper(), []).append(row)
    return grouped


def block(events, checked):
    """The document's top-level "capitalReductions" block (events as stored, source, rule and check day for audit)."""
    return {"source": SOURCE, "checked": checked, "rule": RULE, "events": events}


def events_of(document):
    """A financials document → its stored capital reduction events, or None when the block is absent / malformed."""
    stored = document.get("capitalReductions") if isinstance(document, dict) else None
    events = stored.get("events") if isinstance(stored, dict) else None
    return events if isinstance(events, list) else None


def share_events(document):
    """The reductions that restate EPS, as share-basis events for tw_stock_dividends.factor:
    [{"exDate": resumption date, "shareRatio": new ÷ old, "type": "capitalReduction", "reason": ...}]."""
    return [{"exDate": event["date"], "shareRatio": float(event["ratio"]), "type": "capitalReduction",
             "reason": event.get("reason", "")}
            for event in events_of(document) or [] if applies(event)]
