#!/usr/bin/env python3
"""Decide whether the expensive full-market entry-success report is due.

Only reads breakouts.json (after generated restore); never rewrites a report.
Times and calendar-day ages are evaluated in Asia/Taipei, not runner UTC.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
REPORT = Path("docs/report/breakouts.json")
MAX_AGE_DAYS = 7
EVENTS_WITH_SATURDAY_RUN = {"schedule", "workflow_run"}
MODES = {"auto", "force", "skip"}


@dataclass(frozen=True)
class ScheduleDecision:
    run: bool
    last_as_of: str
    next_date: date
    reason: str


def parse_day(value: object) -> date:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError("Missing or invalid asOf")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Noncanonical asOf")
    return parsed


def load_dates(path: Path) -> tuple[dict[str, date], date | None] | None:
    """Missing/unreadable/invalid market data must trigger a catch-up run."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        markets = raw["markets"]
        dates = {key: parse_day(markets[key]["asOf"]) for key in ("tw", "us")}
        generated = raw.get("generatedAt")
        generated_day = None
        if isinstance(generated, str):
            try:
                timestamp = datetime.fromisoformat(generated.replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                generated_day = timestamp.astimezone(TAIPEI).date()
            except ValueError:
                pass  # asOf remains usable even if the optional timestamp is malformed
        return dates, generated_day
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def decide(report: Path, now: datetime, event_name: str, mode: str = "auto") -> ScheduleDecision:
    if mode not in MODES:
        raise ValueError("entry_success must be auto, force or skip")
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    today = now.astimezone(TAIPEI).date()
    stored = load_dates(report)
    dates, generated_day = stored if stored is not None else (None, None)
    last = ("／".join(f"{key.upper()} {dates[key]}" for key in ("tw", "us"))
            if dates else "無法讀取")
    invalid = dates is None or any(day > today for day in dates.values())
    aged = dates is not None and any((today - day).days >= MAX_AGE_DAYS for day in dates.values())
    saturday = event_name in EVENTS_WITH_SATURDAY_RUN and today.weekday() == 5
    # A second Saturday run with an already-published Saturday result is not
    # another weekly run; a genuinely stale market still takes precedence.
    weekly = saturday and generated_day != today
    if mode == "skip":
        run, reason = False, "manual skip"
    elif mode == "force":
        run, reason = True, "manual force"
    elif invalid:
        run, reason = True, "missing or unreadable market asOf"
    elif aged:
        run, reason = True, "one or both market asOf dates are at least seven calendar days old"
    elif weekly:
        run, reason = True, "Taipei Saturday scheduled/workflow_run"
    else:
        run, reason = False, "weekly report is not due"
    until_sat = (5 - today.weekday()) % 7
    if until_sat == 0:
        until_sat = 7
    next_due = today + timedelta(days=until_sat)
    if dates:
        next_due = min(next_due, min(dates.values()) + timedelta(days=MAX_AGE_DAYS))
    if next_due <= today:
        next_due = today + timedelta(days=1)
    return ScheduleDecision(run, last, next_due, reason)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--mode", choices=sorted(MODES), default=os.getenv("ENTRY_SUCCESS_MODE") or "auto")
    parser.add_argument("--event", default=os.getenv("GITHUB_EVENT_NAME", "workflow_dispatch"))
    parser.add_argument("--now", help="ISO UTC timestamp for offline replay only")
    args = parser.parse_args(argv)
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else datetime.now(timezone.utc)
    result = decide(args.report, now, args.event, args.mode)
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"run={'true' if result.run else 'false'}\n")
    if not result.run:
        summary = f"進場成功率本次略過（上次 asOf {result.last_as_of}，下次預計 {result.next_date}）"
        print(summary)
        target = os.getenv("GITHUB_STEP_SUMMARY")
        if target:
            with open(target, "a", encoding="utf-8") as stream:
                stream.write(summary + "\n")
    else:
        print(f"進場成功率本次執行：{result.reason}（上次 asOf {result.last_as_of}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
