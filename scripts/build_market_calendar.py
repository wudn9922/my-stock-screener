"""Read exchange-calendars; publish only a disposable TW/US session calendar.

No prices, credentials, archive records or notifications are read or written.
The --is-session mode gates public US extended-quote jobs by New York's date,
including after-hours jobs whose UTC date has already advanced.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

MARKETS = {
    "TW": ("XTAI", "Asia/Taipei", 13 * 60 + 30),
    "US": ("XNYS", "America/New_York", 16 * 60),
}


def utc_now(text=None):
    value = datetime.fromisoformat(text.replace("Z", "+00:00")) if text else datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("--now requires a timezone")
    return value.astimezone(timezone.utc)


def build(now):
    start = now.date() - timedelta(days=30)
    end = now.date() + timedelta(days=366)
    result = {"version": 1, "generatedAt": now.astimezone(timezone.utc).isoformat(),
              "validFrom": start.isoformat(), "validTo": end.isoformat(), "markets": {}}
    for market, (name, timezone_name, normal_close) in MARKETS.items():
        calendar = xcals.get_calendar(name, start=start - timedelta(days=7), end=end + timedelta(days=7))
        sessions = {}
        for label in calendar.sessions:
            day = label.date()
            if not start <= day <= end:
                continue
            opened, closed = calendar.session_open(label), calendar.session_close(label)
            local_close = closed.tz_convert(timezone_name)
            sessions[day.isoformat()] = {"open": int(opened.timestamp()), "close": int(closed.timestamp()),
                                        "earlyClose": local_close.hour * 60 + local_close.minute < normal_close}
        holidays = []
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            if day.weekday() < 5 and day.isoformat() not in sessions:
                holidays.append(day.isoformat())
        result["markets"][market] = {"calendar": name, "timezone": timezone_name, "sessions": sessions,
                                     "holidays": holidays,
                                     "earlyCloses": [day for day, session in sessions.items() if session["earlyClose"]]}
    return result


def is_session(market, now):
    name, timezone_name, _ = MARKETS[market]
    day = now.astimezone(ZoneInfo(timezone_name)).date()
    calendar = xcals.get_calendar(name, start=day - timedelta(days=7), end=day + timedelta(days=7))
    return calendar.is_session(day.isoformat())


def write_calendar(output, payload):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement of this derived display file only.
    descriptor, name = tempfile.mkstemp(prefix=output.name + ".", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
        os.replace(name, output)
    finally:
        Path(name).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/report/market-calendar.json"))
    parser.add_argument("--now")
    parser.add_argument("--is-session", choices=MARKETS)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    now = utc_now(args.now)
    if args.is_session:
        active = is_session(args.is_session, now)
        line = "is_session=" + str(active).lower()
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        print(line)
        return
    payload = build(now)
    write_calendar(args.output, payload)
    print(f"Calendar {payload['validFrom']}..{payload['validTo']}: " + ", ".join(
        f"{market} {len(item['sessions'])} sessions" for market, item in payload["markets"].items()))


if __name__ == "__main__":
    main()
