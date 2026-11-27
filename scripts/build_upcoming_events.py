#!/usr/bin/env python3
"""Offline public event aggregation. Never reads account records."""
import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import statistics
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from scripts.output_guard import require, finite, retain_count
from scripts.market_sessions import exchange_sessions, verified_closures, read_policy
from scripts.kline_archive import symbol_rows, LOG
from scripts.build_screener_history import write
import kline_integrity as ki

LABEL = "upcoming events"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def iso(value):
    require(isinstance(value, str) and date.fromisoformat(value).isoformat() == value, LABEL, "invalid date")
    return value


def aware(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(stamp.tzinfo is not None, LABEL, "missing source timezone")
    return stamp


def event(symbol, market, kind, day, detail, source, **extra):
    return dict(symbol=symbol, market=market, kind=kind, date=iso(day), detail=detail, source=source, **extra)


def earnings_moves(history, bars, sessions, today):
    """Only observed, non-estimated bmo/amc dates. No assumed date-only session."""
    results = []
    for stamp, session in sorted(history.items(), reverse=True):
        iso(stamp)
        require(session in ("amc", "bmo"), LABEL, "invalid historical earnings session")
        if stamp >= today:
            continue
        candidates = [d for d in sessions if d > stamp or (d == stamp and session == "bmo")]
        if not candidates:
            continue
        reaction = candidates[0]
        i = sessions.index(reaction)
        if reaction >= today or i == 0:
            continue
        prior = sessions[i - 1]
        if prior not in bars or reaction not in bars:
            continue  # absence is unknown, not zero
        previous, current = bars[prior], bars[reaction]
        gap = abs(current[0] / previous[3] - 1) * 100
        daily = abs(current[3] / previous[3] - 1) * 100
        results.append((gap, daily, reaction))
        if len(results) == 8:
            break
    return dict(samples=len(results),
                gapPct=round(statistics.mean(r[0] for r in results), 3) if results else None,
                dayPct=round(statistics.mean(r[1] for r in results), 3) if results else None,
                first=results[-1][2] if results else None, last=results[0][2] if results else None)


def build(root, archive, now=None):
    now = now or datetime.now(timezone.utc)
    report = root / "docs/report"
    output = report / "upcoming-events.json"
    previous = load(output) if output.exists() else {}
    today = {m: now.astimezone(ZoneInfo(z)).date().isoformat()
             for m, z in (("TW", "Asia/Taipei"), ("US", "America/New_York"))}
    end = {m: (date.fromisoformat(d) + timedelta(days=14)).isoformat() for m, d in today.items()}
    rows = []
    coverage = {}
    earnings = load(root / "docs/atlas/valuation/us-events.json")
    require(earnings["version"] == 1 and earnings["market"] == "US", LABEL, "earnings identity")
    require(timedelta(0) <= now - aware(earnings["generatedAt"]) <= timedelta(days=4), LABEL, "earnings stale")
    items = earnings["items"]
    retain_count(len(items), previous.get("coverage", {}).get("earnings", 0), LABEL + " earnings", min_share=.5)
    coverage["earnings"] = len(items)
    history = earnings.get("history", {})
    require(isinstance(history, dict), LABEL, "earnings history invalid")
    journal = archive / LOG
    require(journal.is_file(), LABEL, "archive journal missing")
    blocked = defaultdict(set)
    for row in ki.read_log(journal)["quarantined"]:
        blocked[row["symbol"]].add(row["date"])
    policy = read_policy(report / "market-closures.json")
    closed = set(verified_closures("us")) | set(policy.get("us", {}))
    moves = {}
    for symbol, past in sorted(history.items()):
        require(isinstance(symbol, str) and not symbol.endswith((".TW", ".TWO")), LABEL, "invalid US history symbol")
        require(isinstance(past, dict), LABEL, "invalid earnings history")
        if not past:
            continue
        first = (date.fromisoformat(min(past)) - timedelta(days=14)).isoformat()
        sessions = sorted(d for d in exchange_sessions("us", first, today["US"])
                          if first <= d <= today["US"] and d not in closed)
        years = {str(y) for y in range(date.fromisoformat(first).year, now.year + 1)}
        bars = {r[0]: tuple(float(v) for v in r[1:5]) for r in symbol_rows(archive, symbol, years=years)
                if r[0] not in blocked[symbol] and r[5] != "0"}
        moves[symbol] = earnings_moves(past, bars, sessions, today["US"])
    # Aggregate floor, not per-symbol monotonicity: a later quarantine can legitimately
    # remove one symbol's sample, and a strict per-symbol check would then reject every
    # future run against the same retained snapshot. A broken archive still trips this.
    retain_count(sum(m["samples"] for m in moves.values()),
                 sum(m["samples"] for m in previous.get("moves", {}).values()),
                 LABEL + " earnings samples", min_share=.8, minimum=0)
    for symbol, item in items.items():
        stamp = iso(item["earningsDate"])
        require(item.get("session") in (None, "bmo", "amc") and isinstance(item.get("estimate"), bool),
                LABEL, "invalid earnings record")
        detail = {"bmo": "盤前", "amc": "盤後"}.get(item.get("session"), "時間未確認")
        if item["estimate"]:
            detail += " · 預估"
        if item.get("window"):
            detail += " · " + "～".join(iso(d) for d in item["window"])
        rows.append(event(symbol, "US", "earnings", stamp, detail, "Yahoo Finance"))
    calendar = load(report / "market-calendar.json")
    require(calendar["version"] == 1 and iso(calendar["validFrom"]) <= min(today.values())
            and iso(calendar["validTo"]) >= max(end.values()), LABEL, "calendar coverage")
    require(timedelta(0) <= now - aware(calendar["generatedAt"]) <= timedelta(days=4), LABEL, "calendar stale")
    for market in today:
        part = calendar["markets"][market]
        sessions = part["sessions"]
        require(len(sessions) > 100, LABEL, "calendar sessions missing")
        for stamp in part["holidays"]:
            require(iso(stamp) not in sessions and date.fromisoformat(stamp).weekday() < 5, LABEL, "invalid holiday")
            rows.append(event(None, market, "holiday", stamp, "交易所休市", "exchange_calendars"))
        for stamp in part["earlyCloses"]:
            require(iso(stamp) in sessions and sessions[stamp]["earlyClose"], LABEL, "invalid early close")
            rows.append(event(None, market, "early-close", stamp, "提早收盤", "exchange_calendars"))
    alerts = load(report / "research/alerts.json")
    require(alerts["version"] == 1 and timedelta(0) <= now - aware(alerts["generatedAt"]) <= timedelta(days=7),
            LABEL, "alerts invalid or stale")
    require(all(alerts["sources"].get(f"{m}:{k}", {}).get("status") == "ok"
                for m in ("TW", "TWO") for k in ("notice", "disposal")), LABEL, "official alert source unavailable")
    coverage["alerts"] = len(alerts["events"])
    retain_count(coverage["alerts"], previous.get("coverage", {}).get("alerts", 0),
                 LABEL + " alerts", min_share=.5, minimum=0)
    for item in alerts["events"]:
        require(item["kind"] in ("notice", "disposal") and item["market"] in ("TW", "TWO"), LABEL, "invalid alert record")
        if item["kind"] == "disposal":
            for field, kind, detail in (("start", "disposal-start", "公告處置開始"),
                                        ("end", "disposal-end", "公告處置結束日（可能展延）")):
                if item.get(field):
                    rows.append(event(item["symbol"], "TW", kind, item[field], detail, "TWSE/TPEx"))
        elif item["kind"] == "notice":
            rows.append(event(item["symbol"], "TW", "notice", item["date"], "注意股公告", "TWSE/TPEx"))
    # Read only the public financials records; no per-user selection is sent to a server.
    count = 0
    cash_count = 0
    for path in sorted((report / "financials").glob("*.json")):
        symbol = path.stem
        if not symbol.endswith((".TW", ".TWO")):
            continue
        document = load(path)
        block = document.get("stockDividends")
        if not isinstance(block, dict):
            continue
        count += 1
        for item in block["events"]:
            if item.get("exDate"):
                require(finite(item["perShare"]) and item["perShare"] > 0, LABEL, "invalid stock dividend")
                rows.append(event(symbol, "TW", "stock-dividend", item["exDate"],
                                  f"配股 NT$ {item['perShare']:g}/股（面額）", "FinMind"))
        if "cashEvents" in block:
            cash_count += 1
            for item in block["cashEvents"]:
                require(finite(item["perShare"]) and item["perShare"] > 0, LABEL, "invalid cash dividend")
                rows.append(event(symbol, "TW", "cash-dividend", item["exDate"],
                                  f"現金股利 NT$ {item['perShare']:g}/股", "FinMind"))
    retain_count(count, previous.get("coverage", {}).get("dividends", 0), LABEL + " dividends", min_share=.8)
    retain_count(cash_count, previous.get("coverage", {}).get("cashDividends", 0),
                 LABEL + " cash dividends", min_share=.8, minimum=0)
    coverage.update(dividends=count, cashDividends=cash_count)
    fed = load(root / "config/fomc-decisions.json")
    require(fed["version"] == 1 and iso(fed["validThrough"]) >= end["US"], LABEL, "FOMC calendar needs review")
    for stamp in fed["dates"]:
        rows.append(event(None, "US", "fomc", stamp, "預定利率決議（美東，日程可能調整）", fed["source"]))
    # Keep a slightly wider public window so client local dates and delayed deployments remain usable.
    result = {tuple(row[k] for k in ("symbol", "market", "kind", "date", "detail")): row
              for row in rows if (date.fromisoformat(today[row["market"]]) - timedelta(days=1)).isoformat()
              <= row["date"] <= (date.fromisoformat(end[row["market"]]) + timedelta(days=7)).isoformat()}
    payload = dict(version=1, generatedAt=now.isoformat(), coverage=coverage,
                   events=sorted(result.values(), key=lambda r: (r["date"], r["market"], r["symbol"] or "", r["kind"])),
                   moves=moves)
    json.dumps(payload, allow_nan=False)
    write(output, payload)
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--archive-dir", type=Path, default=ROOT / ".cache/kline-archive")
    args = parser.parse_args()
    build(args.root, args.archive_dir)
