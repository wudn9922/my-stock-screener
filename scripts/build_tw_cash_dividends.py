"""Independent public cash dividends snapshot. Reads only Taiwan financial records."""
import argparse
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from research_io import read

ROOT = Path(__file__).resolve().parents[1]
SYMBOL = re.compile(r"^[0-9]{4,6}[A-Z]?\.(?:TW|TWO)$")


def iso(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Invalid dividend date")
    return value


def stock_block(block, start, end):
    if not isinstance(block, dict) or not isinstance(block.get("cashEvents"), list):
        raise ValueError("Missing cash dividend rows")
    checked = block.get("checked")
    if checked is not None:
        checked = iso(checked)
    events = []
    for item in block["cashEvents"]:
        stamp = iso(item["exDate"])
        per_share = item["perShare"]
        if type(per_share) not in (int, float) or not math.isfinite(per_share) or per_share <= 0:
            raise ValueError("Invalid cash amount")
        payment = item.get("paymentDate")
        if payment is not None:
            payment = iso(payment)
            if payment < stamp:
                raise ValueError("Payment before ex-dividend date")
        if start <= stamp <= end:
            events.append(dict(exDate=stamp, perShare=per_share, paymentDate=payment, year=str(item.get("year", ""))))
    return dict(checked=checked, events=events)


def build(report, now):
    today = now.astimezone(ZoneInfo("Asia/Taipei")).date()
    start = (today - timedelta(days=367)).isoformat()
    end = (today + timedelta(days=97)).isoformat()
    stocks = {}
    skipped = 0
    for path in sorted((report / "financials").glob("*.json")):
        if not SYMBOL.fullmatch(path.stem):
            continue
        try:
            doc = read(path)
            block = doc.get("stockDividends")
            if not isinstance(block, dict) or "cashEvents" not in block:
                continue
            stocks[path.stem] = stock_block(block, start, end)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
            skipped += 1
    if not stocks:
        raise ValueError("No valid cash dividend records")
    return dict(version=1, generatedAt=now.replace(microsecond=0).isoformat(), stocks=stocks), skipped


def run(report, now, dry_run=False):
    output = report / "tw-cash-dividends.json"
    try:
        result, skipped = build(report, now)
        previous = read(output, {})
        if len(result["stocks"]) < .8 * len(previous.get("stocks", {})):
            raise ValueError("Cash dividend coverage dropped")
        # Measure exactly the compact UTF-8 bytes that would be published.
        body = (json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode()
        upcoming = report / "upcoming-events.json"
        sizes = {"tw-cash-dividends.json": len(body)}
        if upcoming.is_file():
            sizes["upcoming-events.json"] = upcoming.stat().st_size
        print("Cash dividend bytes:", json.dumps(sizes), "stocks:", len(result["stocks"]), "skipped:", skipped)
        if skipped:
            print(f"::warning::Skipped {skipped} invalid cash dividend stock records")
        if not dry_run:
            # Refresh snapshot freshness, even if source events have not changed.
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_suffix(".json.tmp")
            try:
                temporary.write_bytes(body)
                temporary.replace(output)
            finally:
                temporary.unlink(missing_ok=True)
        return True
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        print("::warning::Cash dividend snapshot unavailable; previous output retained")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=ROOT / "docs/report")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    ok = run(args.report_dir, datetime.now(timezone.utc), args.dry_run)
    if args.strict and not ok:
        raise SystemExit(1)
