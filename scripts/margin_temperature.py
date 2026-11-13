"""上市融資餘額溫度。Stdlib only; data and source kept separate from other indicators.

Usage:
  python scripts/margin_temperature.py build
  python scripts/margin_temperature.py update [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import re
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "research_data" / "margin_temperature"
OUTPUT = ROOT / "docs" / "report" / "margin-temperature.json"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*", "Accept-Language": "zh-TW,zh;q=0.9"}
TWSE = "https://www.twse.com.tw/rwd/zh/"
TPE = ZoneInfo("Asia/Taipei")


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def build_rows(index_rows: list[dict[str, str]], margin_rows: list[dict[str, str]]) -> list[dict]:
    """Pandas reference parity: merge-on-date, W-SUN group last valid balance, interpolate(limit=2)."""
    margins = {row["date"]: float(row["mrg_amt"]) for row in margin_rows if row.get("mrg_amt")}
    weekly: dict[tuple[int, int], dict] = {}
    for row in sorted(index_rows, key=lambda r: r["date"]):
        day = date.fromisoformat(row["date"])
        if day < date(2007, 1, 1):
            continue
        key = day.isocalendar()[:2]
        item = weekly.setdefault(key, {})
        item["date"] = row["date"]
        item["close"] = float(row["close"])
        if row["date"] in margins:
            item["mrg_amt"] = margins[row["date"]]
    rows = list(weekly.values())
    # pandas Series.interpolate(limit=2) fills at most two gaps after each observed value.
    original = [row.get("mrg_amt") for row in rows]
    values = original.copy()
    i = 0
    while i < len(values):
        if original[i] is not None:
            i += 1
            continue
        start = i
        while i < len(values) and original[i] is None:
            i += 1
        before = start - 1
        after = i
        if before < 0:
            continue
        for j in range(start, min(after, start + 2)):
            if after < len(values):
                values[j] = original[before] + (original[after] - original[before]) * (j - before) / (after - before)
            else:
                values[j] = original[before]
    clean = [dict(row, mrg_amt=v) for row, v in zip(rows, values) if v is not None]
    logs = [math.log(row["mrg_amt"]) for row in clean]
    for i, row in enumerate(clean):
        h = logs[max(0, i - 155):i + 1]
        k = logs[max(0, i - 51):i + 1]
        pct = 100 * sum(x < h[-1] for x in h[:-1]) / len(h[:-1]) if len(h) >= 104 else None
        mdd = 100 * (math.exp(k[-1] - max(k)) - 1) if len(k) >= 26 else None
        row["pct"] = pct
        row["mdd"] = mdd
        row["hot"] = pct is not None and pct >= 90
        row["cold"] = mdd is not None and mdd <= -30
    return clean


def to_report(rows: list[dict], today: date | None = None) -> dict:
    if not rows:
        raise ValueError("沒有可發布的融資資料")
    last = rows[-1]
    last_date = date.fromisoformat(last["date"])
    today = today or datetime.now(TPE).date()
    provisional = last_date.isocalendar()[:2] == today.isocalendar()[:2] and last_date.weekday() < 4
    def public(row: dict) -> dict:
        return {key: row[key] for key in ("date", "close", "pct", "mdd", "hot", "cold")}
    return {
        "version": 1,
        "asOf": last["date"],
        "provisional": provisional,
        "scope": "TWSE listed only; amount in thousand NTD, not market value",
        "latest": public(last),
        "rows": [public(row) for row in rows],
    }


def make_report(idx: list[dict[str, str]], mg: list[dict[str, str]], today: date | None = None) -> dict:
    return to_report(build_rows(idx, mg), today)


def atomic_write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, prefix=".tmp-", delete=False) as out:
            name = out.name
            out.write(contents)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def render_csv(rows: list[dict[str, str]], columns: tuple[str, ...]) -> str:
    from io import StringIO
    file = StringIO(newline="")
    writer = csv.DictWriter(file, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return file.getvalue()


class TwseClient:
    def __init__(self, opener=None, sleeper=time.sleep, clock=time.monotonic):
        self.opener = opener or urlopen
        self.sleep = sleeper
        self.clock = clock
        self.last_request = float("-inf")

    def fetch(self, route: str, params: dict[str, str]) -> dict:
        url = TWSE + route + "?" + urlencode(params)
        for attempt in range(15):
            now = self.clock()
            self.sleep(max(0, 1.05 - (now - self.last_request)))
            self.last_request = self.clock()
            try:
                with self.opener(Request(url, headers=HEADERS), timeout=30) as response:
                    content_type = response.headers.get("Content-Type", "").lower()
                    payload = response.read()
                    code = response.getcode()
                if code == 307 or b"<html" in payload[:500].lower() or "text/html" in content_type:
                    raise ValueError("TWSE responded with HTML/redirect")
                obj = json.loads(payload)
                if not isinstance(obj, dict):
                    raise ValueError("TWSE JSON is not an object")
                return obj
            except (HTTPError, URLError, ValueError, json.JSONDecodeError) as exc:
                if attempt == 14:
                    raise RuntimeError(f"TWSE {route} 連續15次失敗；保留舊資料") from exc
                self.sleep(random.uniform(1, 3))
        raise AssertionError("unreachable")


def numeric(raw: object) -> float:
    return float(str(raw).replace(",", "").strip())


def parse_margin(payload: dict) -> dict[str, float] | None:
    if payload.get("stat") != "OK":
        return None
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables:
        raise ValueError("TWSE 融資回傳缺少 tables[0]")
    for row in tables[0].get("data", []):
        if not isinstance(row, list) or not row:
            continue
        name = re.sub(r"[\s（）()]", "", str(row[0]))
        if name in {"融資金額仟元", "融資金額千元"}:
            if len(row) < 6:
                raise ValueError("TWSE 融資欄位不足六欄")
            amount = numeric(row[5])
            if not math.isfinite(amount) or amount <= 0:
                raise ValueError("TWSE 融資金額不合理")
            # TWSE may present the shares row separately.  Never mislabel unavailable numbers.
            out = {"mrg_amt": amount}
            for other in tables[0].get("data", []):
                if not isinstance(other, list) or len(other) < 6:
                    continue
                label = re.sub(r"[\s（）()]", "", str(other[0]))
                if label.startswith("融資股數"):
                    out["mrg_lots"] = numeric(other[5])
                if label.startswith("融券股數"):
                    out["short_lots"] = numeric(other[5])
            return out
    raise ValueError("TWSE 融資金額(仟元) 今日餘額欄缺失")


def parse_index(payload: dict, target: date) -> dict[str, float] | None:
    if payload.get("stat") != "OK":
        return None
    candidates = payload.get("data") or [r for t in payload.get("tables", []) for r in t.get("data", [])]
    for row in candidates:
        if not isinstance(row, list) or len(row) < 5:
            continue
        ds = str(row[0]).strip()
        # MI_5MINS_HIST uses ROC calendar dates (e.g. 115/11/12).
        parts = re.split(r"[/\-]", ds)
        if len(parts) != 3 or not all(x.isdigit() for x in parts):
            continue
        year, month, day = (int(x) for x in parts)
        try:
            value_date = date(year + 1911 if year < 1911 else year, month, day)
        except ValueError:
            continue
        if value_date == target:
            values = [numeric(row[i]) for i in range(1, 5)]
            if any(not math.isfinite(v) or v <= 0 for v in values):
                raise ValueError("TWSE 加權指數 OHLC 無效")
            return dict(zip(("open", "high", "low", "close"), values))
    return None


def update(target: date, client: TwseClient | None = None) -> str:
    if target > datetime.now(TPE).date():
        raise ValueError("不可抓取未來日期")
    client = client or TwseClient()
    stamp = target.strftime("%Y%m%d")
    margin = parse_margin(client.fetch("marginTrading/MI_MARGN", {"date": stamp, "selectType": "MS", "response": "json"}))
    index = parse_index(client.fetch("indices/MI_5MINS_HIST", {"date": stamp, "response": "json"}), target)
    if margin is None and index is None:
        return f"{target}: 休市，未寫入資料"
    if margin is None or index is None:
        raise RuntimeError(f"{target}: 融資與加權指數資料不一致，保留舊資料")
    idx = csv_rows(DATA / "taiex.csv")
    mg = csv_rows(DATA / "margin_daily.csv")
    if "mrg_lots" not in margin or "short_lots" not in margin:
        # Historical package includes lots, but value-only updates must keep blanks,
        # never invent missing share lots or replace valid historical values.
        pass
    margin_row = {"date": target.isoformat(), "mrg_lots": margin.get("mrg_lots", ""), "short_lots": margin.get("short_lots", ""), "mrg_amt": margin["mrg_amt"]}
    index_row = {"date": target.isoformat(), **index}
    def merged(old: list[dict[str, str]], new: dict) -> list[dict[str, str]]:
        result = {row["date"]: row for row in old}
        result[new["date"]] = {k: str(v) for k, v in new.items()}
        return sorted(result.values(), key=lambda r: r["date"])
    idx2 = merged(idx, index_row)
    mg2 = merged(mg, margin_row)
    report = make_report(idx2, mg2)
    # Validate computations and serialization before replacing any on-disk data.
    outputs = [
        (DATA / "taiex.csv", render_csv(idx2, ("date", "open", "high", "low", "close"))),
        (DATA / "margin_daily.csv", render_csv(mg2, ("date", "mrg_lots", "short_lots", "mrg_amt"))),
        (OUTPUT, json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n"),
    ]
    for path, value in outputs:
        atomic_write(path, value)
    return f"{target}: 已更新，最新熱度 {report['latest']['pct']}, 冷度 {report['latest']['mdd']}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "update"))
    parser.add_argument("--date", type=date.fromisoformat, default=datetime.now(TPE).date())
    args = parser.parse_args()
    if args.command == "build":
        report = make_report(csv_rows(DATA / "taiex.csv"), csv_rows(DATA / "margin_daily.csv"))
        atomic_write(OUTPUT, json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n")
        print(f"已生成 {OUTPUT.relative_to(ROOT)}，資料日 {report['asOf']}")
    else:
        print(update(args.date))


if __name__ == "__main__":
    main()
