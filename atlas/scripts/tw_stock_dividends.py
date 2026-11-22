"""
Taiwan stock dividends (配股: 盈餘轉增資 / 資本公積轉增資) and the EPS restatement they call for. Standard library only.

A stock dividend of D NT$ per share (par value 10) adds D / 10 new shares per old share on the ex-rights date, so the
EPS printed in reports published BEFORE that date is on a smaller share basis than the EPS after it. The
retroactively restated ("追溯調整") EPS of a period is  reported EPS ÷ Π (1 + D_i / 10)  over every stock dividend
whose ex-rights date falls after the period's report was due (before that, the report already used the new
share count, IAS 33.64). The reported value is never overwritten: callers keep both.

Source: FinMind TaiwanStockDividend (one row per dividend resolution): StockEarningsDistribution +
StockStatutorySurplus = stock dividend NT$ per share, StockExDividendTradingDate = ex-rights date. Cash-only rows
(factor 1) are not kept. A row with a stock dividend but no ex-rights date yet is kept as "pending" and not applied.
Capital reductions to offset losses (減資彌補虧損) are restated the same way (tw_capital_reductions.py): events_of()
adds them as share-basis events {"exDate", "shareRatio": new ÷ old shares}, and factor() multiplies by the ratio.
Not handled (documented limits): cash capital reductions (現金減資) and cash capital increases (現金增資) change the
share count too but are not restated (IAS 33); par value is assumed 10 (TW default).
"""
import tw_capital_reductions as reductions
from datetime import date, timedelta

PAR = 10.0
DATASET = "TaiwanStockDividend"
SOURCE = "FinMind TaiwanStockDividend"
# report due dates after the period end (same as src/fundamentals/peRiver.ts PUBLISH_DELAY_DAYS.TW)
DELAY_DAYS = {"quarter": 45, "secondQuarter": 60, "yearEnd": 90}


def number(value):
    if isinstance(value, bool) or value is None or value == "":
        return 0.0
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if result == result and abs(result) != float("inf") else 0.0


def day_of(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def parse_events(rows):
    """FinMind rows → ascending list of {"exDate": ISO | None, "perShare": NT$, "earnings", "surplus", "year"} for
    the rows with a stock dividend > 0 (an empty answer or cash-only rows → [])."""
    events = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        earnings, surplus = number(row.get("StockEarningsDistribution")), number(row.get("StockStatutorySurplus"))
        if earnings < 0 or surplus < 0 or earnings + surplus <= 0:
            continue
        ex = day_of(row.get("StockExDividendTradingDate"))
        event = {"exDate": ex.isoformat() if ex else None, "perShare": round(earnings + surplus, 6),
                 "earnings": round(earnings, 6), "surplus": round(surplus, 6),
                 "year": str(row.get("year") or "")}
        events[(event["exDate"], event["year"])] = event
    return sorted(events.values(), key=lambda e: (e["exDate"] or "9999", e["year"]))


def delay_days(end):
    """Days after a period end before its report is due: Q4 / annual 90, Q2 60, Q1 / Q3 45."""
    month = end.month
    return DELAY_DAYS["yearEnd"] if month == 12 else DELAY_DAYS["secondQuarter"] if month == 6 else DELAY_DAYS["quarter"]


def factor(end, events, as_of=None, annual=False):
    """Retroactive factor of the period ending `end` (ISO or date): → {"factor": float, "events": [applied events]},
    or None when `events` is None (dividend data missing: never guess). Events count when their ex-rights date is
    after the report due date (end + delay; an annual period always end + 90) and, with `as_of`, not after that day
    (share basis of that day)."""
    if events is None:
        return None
    end_day = end if isinstance(end, date) else day_of(end)
    if end_day is None:
        return None
    due = end_day + timedelta(days=DELAY_DAYS["yearEnd"] if annual else delay_days(end_day))
    limit = day_of(as_of) if as_of is not None else None
    applied = []
    total = 1.0
    for event in events:
        ex = day_of(event.get("exDate"))
        if ex is None or ex <= due or (limit is not None and ex > limit):
            continue
        applied.append(event)
        if "shareRatio" in event:
            ratio = number(event.get("shareRatio"))
            total *= ratio if 0 < ratio < 1 else 1.0          # a loss-offset capital reduction (new ÷ old shares)
        else:
            total *= 1.0 + number(event.get("perShare")) / PAR
    return {"factor": round(total, 6), "events": applied}


def adjust(value, end, events, as_of=None, annual=False):
    """Reported EPS → (restated EPS, factor info); (None, None) when the EPS or the dividend data is missing."""
    info = factor(end, events, as_of, annual)
    if info is None or value is None:
        return None, info
    return round(value / info["factor"], 4) + 0.0, info


def block(events, checked):
    """The document's top-level "stockDividends" block (events as stored, source and check day for audit)."""
    return {"source": SOURCE, "par": PAR, "checked": checked, "events": events}


def events_of(document):
    """A financials document → its stock dividend events plus the loss-offset capital reductions of its
    "capitalReductions" block (tw_capital_reductions.share_events), ascending; None when the stockDividends block is
    absent / malformed (no share-basis data: never guess)."""
    stored = document.get("stockDividends") if isinstance(document, dict) else None
    events = stored.get("events") if isinstance(stored, dict) else None
    if not isinstance(events, list):
        return None
    extra = reductions.share_events(document)
    if not extra:
        return events
    return sorted(events + extra, key=lambda e: str(e.get("exDate") or "9999"))
