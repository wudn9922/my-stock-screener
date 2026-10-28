#!/usr/bin/env python3
"""Read-only freshness inventory. Only --output is written; source records stay untouched."""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.request import Request, urlopen

import exchange_calendars as xcals
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STATES = ("ok", "late", "stale", "missing")


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    market: str
    path: str
    date_field: str
    normal_delay: int = 0
    stale_after: int = 3
    kind: str = "daily"
    calendar: str = "XTAI"
    publish_minutes: int = 120
    generated_field: str = "generatedAt"
    counts_field: str = ""


# The daily report and full-market universe publish around 23:20-23:40 Taipei (15:20-15:40Z):
# Taiwan data for that day ~10h after the 13:30 close, and US data for the previous session
# ~20h after its close. Grace covers that schedule with slack, so normal timing is not "late".
TW_PIPELINE_MINUTES = 12 * 60
US_PIPELINE_MINUTES = 22 * 60

# All cadence, date selectors, publication grace and thresholds live in this table.
# Quarterly entries monitor refresh coverage, NOT whether a new fiscal quarter is due.
SOURCES = (
    Source("report", "每日報告", "無", "latest.json", "reportDate", publish_minutes=TW_PIPELINE_MINUTES),
    Source("universe-tw", "全市場台股", "TW", "universe.json", "markets.TW.asOf", publish_minutes=TW_PIPELINE_MINUTES, counts_field="markets.TW.counts"),
    Source("universe-us", "全市場美股", "US", "universe.json", "markets.US.asOf", calendar="XNYS", publish_minutes=US_PIPELINE_MINUTES, counts_field="markets.US.counts"),
    Source("breakouts-tw", "台股突破", "TW", "breakouts.json", "markets.tw.asOf|asOf", publish_minutes=TW_PIPELINE_MINUTES),
    Source("breakouts-us", "美股突破", "US", "breakouts.json", "markets.us.asOf|asOf", calendar="XNYS", publish_minutes=US_PIPELINE_MINUTES),
    Source("breadth-tw", "台股市場寬度", "TW", "breadth.json", "markets.tw.asOf|generatedAt", publish_minutes=TW_PIPELINE_MINUTES),
    Source("breadth-us", "美股市場寬度", "US", "breadth.json", "markets.us.asOf|generatedAt", calendar="XNYS", publish_minutes=US_PIPELINE_MINUTES),
    Source("themes-us", "美股題材", "US", "themes.json", "asOf", calendar="XNYS", publish_minutes=US_PIPELINE_MINUTES),
    Source("themes-tw", "台股題材", "TW", "themes-tw.json", "asOf", publish_minutes=TW_PIPELINE_MINUTES),
    Source("rates", "利率（FRED）", "全球", "rates.json", "asOf", 3, 6, calendar="XNYS", publish_minutes=360),
    Source("chips", "台股籌碼", "TW", "tw-chips.json", "generatedAt", 5, 10, "weekly"),
    Source("growth", "成長財務彙整", "無", "growth.json", "generatedAt", 1, 3),
    Source("sec", "美股財報（SEC）", "US", "fundamentals/us-sec.json", "asOf", 5, 10, "weekly", "XNYS", 360),
    Source("tw-financials", "台股財報（MOPS）", "TW", "fundamentals/tw.json", "fetch.latest-season.at", 65, 85, "quarterly", generated_field="fetch.latest-season.at"),
    Source("tw-finmind", "台股財報（FinMind）", "TW", "fundamentals/tw-finmind.json", "fetch.*.0", 65, 85, "quarterly", generated_field="fetch.*.0"),
    Source("trending", "熱門股", "US", "trending-stocks.json", "fetchedAt", calendar="XNYS", publish_minutes=360, generated_field="fetchedAt"),
    Source("archive-tw", "台股 K 線封存（市場最新）", "TW", "@archive", "markets.tw.lastDate", publish_minutes=TW_PIPELINE_MINUTES),
    Source("archive-us", "美股 K 線封存（市場最新）", "US", "@archive", "markets.us.lastDate", calendar="XNYS", publish_minutes=US_PIPELINE_MINUTES),
)
WORKFLOWS = {
    "schedule.yml": "每日報告", "universe-full.yml": "全市場資料／K 線封存",
    "financials.yml": "財報歷史", "fundamentals.yml": "官方財報",
    "tw-chips.yml": "台股籌碼", "themes-tw.yml": "台股題材",
    "research-data.yml": "研究資料", "refresh-us-rates.yml": "美國利率",
}


def parsed_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T.+)?", value):
        raise ValueError("日期欄位不是有效的 ISO 日期或時間")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


@lru_cache(maxsize=4)
def calendar_for(name, year):
    # Explicit bounds, like market_clock.get_exchange_calendar; don't silently
    # substitute weekdays when the exchange calendar is absent/out of range.
    return xcals.get_calendar(name, start=f"{year - 10}-01-01", end=f"{year + 1}-12-31")


def expected_date(source, now):
    cal = calendar_for(source.calendar, now.year)
    today = now.astimezone(cal.tz).date()
    sessions = cal.sessions_in_range(today - timedelta(days=40), today)
    for session in reversed(sessions):
        available = cal.session_close(session).to_pydatetime() + timedelta(minutes=source.publish_minutes)
        if available <= now:
            return session.date().isoformat()
    raise ValueError("交易日曆找不到已過發布寬限的交易日")


def lag_days(source, data_date, expected):
    cal = calendar_for(source.calendar, date.fromisoformat(expected).year)
    if data_date >= expected:
        return 0
    return len(cal.sessions_in_range(date.fromisoformat(data_date) + timedelta(days=1), expected))


def select(document, field):
    for alternative in field.split("|"):
        if alternative == "fetch.latest-season.at":
            fetch = document.get("fetch", {})
            seasons = sorted(k for k in fetch if re.fullmatch(r"\d{4}Q[1-4]", k))
            values = [part.get("at") for part in fetch[seasons[-1]].values() if isinstance(part, dict)] if seasons else []
            # Old final fiscal periods are intentionally immutable. Check every
            # exchange of the newest collected period, not the newest timestamp alone.
            if not values or any(not isinstance(v, str) for v in values):
                raise ValueError("最新季別 fetch.at 缺少或無效")
            return min(values)
        if alternative == "fetch.*.0":
            values = [v[0] if isinstance(v, list) and v else None for v in document.get("fetch", {}).values()]
            if not values or any(not isinstance(v, str) for v in values):
                raise ValueError("fetch 各股更新時間缺少或無效")
            return min(values)
        value = document
        for key in alternative.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        if value is not None:
            return value
    raise ValueError(f"找不到日期欄位 {field}")


def evaluate(source, document, now, *, archive_generated=None, error=None):
    result = {"id": source.id, "name": source.name, "market": source.market,
              "path": source.path, "dateField": source.date_field, "type": source.kind,
              "normalDelayTradingDays": source.normal_delay, "staleAfterTradingDays": source.stale_after,
              "calendar": source.calendar, "publishGraceMinutes": source.publish_minutes,
              "dataDate": None, "generatedAt": None, "expectedDate": None,
              "lagTradingDays": None, "status": "missing", "reason": ""}
    try:
        result["expectedDate"] = expected_date(source, now)
        if error:
            raise ValueError(error)
        if not isinstance(document, dict):
            raise ValueError("資料不是 JSON 物件")
        # Date-only fields are source session labels. Timestamp-only sources use
        # their exchange timezone; a fresh collection is not a new fiscal period.
        value = select(document, source.date_field)
        timestamp = parsed_time(value)
        day = timestamp.date().isoformat() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else timestamp.astimezone(calendar_for(source.calendar, now.year).tz).date().isoformat()
        result["dataDate"] = day
        if day > now.astimezone(calendar_for(source.calendar, now.year).tz).date().isoformat():
            raise ValueError("來源日期在未來，無法判定更新狀態")
        generated = archive_generated if source.path == "@archive" else select(document, source.generated_field)
        # A fetch date has no time-of-day; don't invent midnight UTC / 08:00 TW.
        if generated:
            generated_time = parsed_time(generated)
            result["generatedAt"] = generated if re.fullmatch(r"\d{4}-\d{2}-\d{2}", generated) else generated_time.isoformat()
        lag = lag_days(source, day, result["expectedDate"])
        result["lagTradingDays"] = lag
        result["status"] = "ok" if lag <= source.normal_delay else "stale" if lag >= source.stale_after else "late"
        result["reason"] = ("已達正常更新範圍" if result["status"] == "ok" else f"落後 {lag} 個交易日") + f"；正常容許 {source.normal_delay} 個交易日。"
        if source.kind == "quarterly":
            result["reason"] += "此日期為涵蓋範圍內最早擷取日，不代表財報季度。"
        elif source.date_field in ("generatedAt", "fetchedAt"):
            result["reason"] += "此日期為擷取或彙整日，不保證每筆內容均已更新。"
        if source.path == "@archive":
            result["reason"] += "只檢查市場最新 K 線日期，不代表每檔均已補齊。"
        if source.counts_field:
            counts = select(document, source.counts_field)
            result["counts"] = {k: counts.get(k) for k in ("stale", "missing")}
            result["reason"] += f"個股落後 {counts.get('stale', '未知')} 檔、缺少 {counts.get('missing', '未知')} 檔。"
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        result["status"], result["reason"] = "missing", f"無法判定：{exc}。"
    return result


def github_json(path, repository, token):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("未設定有效的 GITHUB_REPOSITORY")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Atlas-data-status", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(f"https://api.github.com/repos/{repository}/{path}", headers=headers), timeout=15) as response:
        return json.load(response)


def workflow_status(repository, token, request=github_json):
    rows = []
    for filename, name in WORKFLOWS.items():
        row = {"file": filename, "name": name, "status": "unknown", "conclusion": None,
               "startedAt": None, "updatedAt": None, "url": f"https://github.com/{repository}/actions/workflows/{filename}"}
        try:
            runs = request(f"actions/workflows/{filename}/runs?branch=main&per_page=1", repository, token)["workflow_runs"]
            if not runs:
                raise ValueError("沒有執行紀錄")
            run = runs[0]
            row.update(status=run["status"], conclusion=run.get("conclusion"), startedAt=run.get("run_started_at"), updatedAt=run.get("updated_at"), url=run["html_url"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Never serialize request headers or the token in public reports.
            row["reason"] = f"無法取得執行紀錄（{type(exc).__name__}）"
        rows.append(row)
    return rows


def build(root=ROOT, now=None, archive=None, archive_generated=None, archive_error=None, workflows=()):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    loaded, sources = {}, []
    for source in SOURCES:
        if source.path == "@archive":
            doc, error = archive, archive_error or (None if archive else "無法讀取 kline-archive manifest.json")
        else:
            if source.path not in loaded:
                try:
                    loaded[source.path] = (json.loads((Path(root) / "docs/report" / source.path).read_text()), None)
                except (OSError, ValueError) as exc:
                    loaded[source.path] = (None, f"檔案無法讀取 {source.path}（{type(exc).__name__}）")
            doc, error = loaded[source.path]
        sources.append(evaluate(source, doc, now, archive_generated=archive_generated, error=error))
    return {"version": 1, "generatedAt": now.isoformat(), "summary": {state: sum(row["status"] == state for row in sources) for state in STATES}, "sources": sources, "workflows": list(workflows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/report/data-status.json")
    parser.add_argument("--archive-ref", help="Read local git ref:manifest.json; never checkout or mutate the archive")
    parser.add_argument("--now", help="ISO UTC time for deterministic verification")
    parser.add_argument("--offline", action="store_true", help="No GitHub requests")
    args = parser.parse_args()
    now = parsed_time(args.now) if args.now else datetime.now(timezone.utc)
    repository = os.environ.get("GITHUB_REPOSITORY", "wudn9922/my-stock-screener")
    token = os.environ.get("GITHUB_TOKEN", "")
    archive, generated, error = None, None, None
    try:
        if args.archive_ref:
            archive = json.loads(subprocess.check_output(["git", "show", f"{args.archive_ref}:manifest.json"], cwd=args.root))
            generated = subprocess.check_output(["git", "show", "-s", "--format=%cI", args.archive_ref], cwd=args.root).decode().strip()
        elif not args.offline:
            body = github_json("contents/manifest.json?ref=kline-archive", repository, token)
            archive = json.loads(base64.b64decode(body["content"]))
            commits = github_json("commits?sha=kline-archive&path=manifest.json&per_page=1", repository, token)
            generated = commits[0]["commit"]["committer"]["date"]
    except (OSError, ValueError, KeyError, IndexError, subprocess.CalledProcessError) as exc:
        error = f"無法讀取 kline-archive（{type(exc).__name__}）"
    result = build(args.root, now, archive, generated, error, () if args.offline else workflow_status(repository, token))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(args.output)
    print(f"Data status: {result['summary']}; {args.output.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
