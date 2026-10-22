"""Incremental official EDGAR 8-K / 8-K/A radar. Bounded, resumable, no AI summaries.

Reuse the repository's exact CIK normalizer/ticker mapping and configured SEC contact.
At most 1.5 requests/s in this serial process; 403/429 persists cooldown, no UA/IP rotation.
Daily master index discovers market-wide filings, submissions prioritise report/watchlist symbols.
"""

from __future__ import annotations
import argparse
import os
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
from research_io import ROOT, SourceError, meta, read, retain, stamp, universe, write

sys.path.insert(0, str(ROOT / "atlas/scripts"))
from seed_financials_sec import parse_ticker_map, resolve_user_agent, TICKERS_URL

ITEMS = {
    "1.01": ("agreement", "重大正式協議"),
    "1.02": ("agreement", "重大協議終止"),
    "1.03": ("debt", "破產或接管"),
    "2.01": ("merger", "收購或資產處分"),
    "2.02": ("financial", "營運結果與財務狀況"),
    "2.03": ("debt", "新增重大債務"),
    "2.04": ("debt", "債務加速到期或違約觸發"),
    "2.05": ("financial", "退出或處分活動成本"),
    "2.06": ("financial", "重大資產減損"),
    "4.01": ("accountant", "會計師異動"),
    "4.02": ("accountant", "財報或查核報告不再可信賴"),
    "5.01": ("governance", "控制權異動"),
    "5.02": ("governance", "董事或高階主管異動"),
    "5.03": ("governance", "章程或會計年度異動"),
    "5.07": ("governance", "股東表決結果"),
    "7.01": ("other", "公平揭露"),
    "8.01": ("other", "其他重大事件"),
    "9.01": ("exhibits", "財報與附件"),
}


class Cooldown(SourceError):
    pass


class NotPublished(SourceError):
    """A missing official index (404) must not advance the index cursor."""


class SecHttp:
    def __init__(self, state, opener=urllib.request.urlopen, sleeper=time.sleep):
        self.state, self.opener, self.sleep, self.last = state, opener, sleeper, 0
        self.agent = resolve_user_agent(os.environ)

    def get(self, url):
        if self.state.get("cooldownUntil", 0) > time.time():
            raise Cooldown(
                "SEC cooldown active; retry after " + str(self.state["cooldownUntil"])
            )
        self.sleep(max(0, 0.7 - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.agent,
                "Accept": "application/json,text/html,text/plain",
            },
        )
        try:
            with self.opener(req, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotPublished("SEC HTTP 404") from exc
            if exc.code in (403, 429):
                try:
                    wait = max(300, int(exc.headers.get("Retry-After", "300")))
                except ValueError:
                    wait = 900
                self.state["cooldownUntil"] = time.time() + wait
                raise Cooldown(
                    f"SEC HTTP {exc.code}; persisted {wait}s cooldown"
                ) from exc
            raise SourceError(f"SEC HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise SourceError(str(exc)) from exc

    def json(self, url):
        try:
            import json

            return json.loads(self.get(url))
        except (ValueError, UnicodeError) as exc:
            raise SourceError("SEC invalid JSON") from exc


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.ignored += 1
        elif tag in ("p", "div", "br", "tr"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.ignored = max(0, self.ignored - 1)

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def extract(body):
    parser = PlainText()
    parser.feed(body.decode("utf-8", errors="replace"))
    text = re.sub(r"\s+", " ", "".join(parser.parts)).strip()
    matches = list(re.finditer(r"\bItem\s+([1-9]\.\d{2})\b", text, re.I))
    # Short repeated headings are usually a contents table; choose substantive section.
    sections = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[m.end() : end].strip(" .:-")
        if len(content) > 80 and m.group(1) != "9.01":
            sections.append((m.group(1), content[:1200]))
    return sections


def event(symbol, cik, name, filing, fetched):
    form = filing.get("form")
    accession = filing.get("accessionNumber", "")
    if form not in ("8-K", "8-K/A") or not re.fullmatch(
        r"\d{10}-\d{2}-\d{6}", accession
    ):
        return None
    day = filing.get("filingDate", "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return None
    items = re.findall(r"\b[1-9]\.\d{2}\b", filing.get("items", ""))
    doc = filing.get("primaryDocument") or ""
    base = f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace("-","")}/'
    document = (
        base + doc
        if re.fullmatch(r"[A-Za-z0-9_.-]+\.html?", doc)
        else base + accession + "-index.html"
    )
    categories = [
        {
            "item": i,
            "category": ITEMS.get(i, ("other", "其他重大事件"))[0],
            "title": ITEMS.get(i, ("other", "其他重大事件"))[1],
        }
        for i in items
    ]
    return dict(
        id=f"{int(cik)}:{accession}",
        symbol=symbol,
        cik=str(int(cik)).zfill(10),
        companyName=name,
        form=form,
        date=day,
        acceptedAt=filing.get("acceptanceDateTime") or None,
        accession=accession,
        items=items,
        categories=categories,
        title="、".join(dict.fromkeys(c["title"] for c in categories))
        or "重大事件申報",
        summary=None,
        summaryStatus="unparsed",
        sourceUrl=document,
        fetchedAt=fetched,
        publishedAt=filing.get("acceptanceDateTime") or None,
        amendment=form == "8-K/A",
        methodVersion="research-v1",
    )


def submissions(data, symbol, cik, since):
    if int(data.get("cik", -1)) != int(cik):
        raise SourceError("CIK mismatch")
    recent = data.get("filings", {}).get(
        "recent", data if "accessionNumber" in data else {}
    )
    result = []
    for i, day in enumerate(recent.get("filingDate", [])):
        if day < since:
            continue
        fields = {
            k: v[i] for k, v in recent.items() if isinstance(v, list) and len(v) > i
        }
        row = event(symbol, cik, data.get("name", ""), fields, stamp())
        if row:
            result.append(row)
    return result


def parse_index(body, mapping):
    """Official pipe-delimited daily master index; each accession, not ticker, is identity."""
    out = []
    for line in body.decode("latin1").splitlines():
        parts = line.split("|")
        if (
            len(parts) != 5
            or not parts[0].isdigit()
            or parts[2] not in ("8-K", "8-K/A")
        ):
            continue
        cik, name, form, day, path = parts
        if re.fullmatch(r"\d{8}", day):
            day = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        symbols = mapping.get(str(int(cik)).zfill(10), [])
        match = re.search(r"(\d{10}-\d{2}-\d{6})\.txt$", path)
        if not match:
            continue
        for symbol in symbols:
            row = event(
                symbol,
                cik,
                name,
                dict(form=form, filingDate=day, accessionNumber=match.group(1)),
                stamp(),
            )
            if row:
                row["sourceUrl"] = "https://www.sec.gov/Archives/" + path
                out.append(row)
    return out


def enrich(row, http):
    sections = extract(http.get(row["sourceUrl"]))
    if sections:
        row["summary"] = (
            "原文節錄："
            + " ".join(f"Item {item}: {text}" for item, text in sections)[:1600]
        )
        row["summaryStatus"] = "parsed"
        if not row["items"]:
            row["items"] = list(dict.fromkeys(i for i, _ in sections))
            row["categories"] = [
                dict(
                    item=i,
                    category=ITEMS.get(i, ("other", "其他重大事件"))[0],
                    title=ITEMS.get(i, ("other", "其他重大事件"))[1],
                )
                for i in row["items"]
            ]
            row["title"] = "、".join(
                dict.fromkeys(c["title"] for c in row["categories"])
            )
        if re.search(
            r"\b(litigation|lawsuit|subpoena|regulatory investigation)\b",
            row["summary"],
            re.I,
        ):
            row["categories"].append(
                dict(
                    item="content",
                    category="legal",
                    title="監管／訴訟相關揭露（原文關鍵詞）",
                )
            )


def radar_events(events, since):
    """One issuer/accession in the global feed, with all covered share-class aliases."""
    unique = {}
    for row in events:
        if row["date"] < since:
            continue
        old = unique.get(row["id"])
        aliases = set(row.get("symbols", [row["symbol"]]))
        if old:
            aliases.update(old["symbols"])
        chosen = (
            row
            if not old
            or (row["summaryStatus"] == "parsed" and old["summaryStatus"] != "parsed")
            else old
        )
        unique[row["id"]] = dict(chosen, symbols=sorted(aliases))
    return sorted(unique.values(), key=lambda r: (r["date"], r["id"]), reverse=True)


def build(args):
    state = read(args.cache / "sec-radar-state.json", {})
    previous = read(args.out / "sec-radar.json", {})
    http = SecHttp(state)
    start = time.monotonic()
    mapping = read(args.cache / "sec-tickers.json", {})
    error = None
    indexed = set(state.get("indexedDates", []))
    checked = dict(previous.get("checkedSymbols", {}))
    allsymbols = universe()["US"]
    rows = {}

    def add(row):
        symbol = row["symbol"]
        if symbol not in rows:
            stored = read(args.out / "sec" / f"{symbol}.json", {})
            rows[symbol] = {e["id"]: e for e in stored.get("events", [])}
        old = rows[symbol].get(row["id"])
        if old and old.get("summaryStatus") == "parsed":
            row.update(
                summary=old["summary"],
                summaryStatus="parsed",
                items=old["items"],
                categories=old["categories"],
                title=old["title"],
                fetchedAt=old["fetchedAt"],
            )
        rows[symbol][row["id"]] = row

    try:
        if not mapping or time.time() - state.get("tickerMapAt", 0) > 86400:
            mapping = parse_ticker_map(http.json(TICKERS_URL))
            write(args.cache / "sec-tickers.json", mapping)
            state["tickerMapAt"] = time.time()
        reverse = {}
        for s, c in mapping.items():
            if s in allsymbols:
                reverse.setdefault(c, []).append(s)
        since = (args.as_of - timedelta(days=args.days)).isoformat()
        priority = [s for s in args.symbols.split(",") if s in mapping]
        # Rotate the full report list, oldest successful check first; bounded runtime.
        report = [
            p.stem
            for p in (ROOT / "data").glob("*.csv")
            if p.stem in allsymbols and p.stem in mapping
        ]
        order = list(
            dict.fromkeys(
                priority
                + sorted(report, key=lambda s: checked.get(s, {}).get("fetchedAt", ""))
            )
        )
        for symbol in order[: args.limit]:
            if time.monotonic() - start > args.budget * 0.65:
                break
            cik = mapping[symbol]
            data = http.json(f"https://data.sec.gov/submissions/CIK{cik}.json")
            found = submissions(data, symbol, cik, since)
            # Historical SEC submission files are fetched only where their actual date range overlaps.
            for page in data.get("filings", {}).get("files", []):
                if (
                    page.get("filingTo", "") >= since
                    and re.fullmatch(
                        r"CIK\d{10}-submissions-\d+\.json", page.get("name", "")
                    )
                    and time.monotonic() - start < args.budget * 0.65
                ):
                    archive = http.json(
                        "https://data.sec.gov/submissions/" + page["name"]
                    )
                    found += submissions(
                        dict(archive, cik=cik, name=data.get("name", "")),
                        symbol,
                        cik,
                        since,
                    )
            for row in found:
                add(row)
            checked[symbol] = dict(
                cik=cik,
                fetchedAt=stamp(),
                asOf=args.as_of.isoformat(),
                status="ok",
                applicability=(
                    "8-K"
                    if data.get("entityType") == "operating"
                    and any(
                        f in ("10-K", "10-Q", "8-K", "8-K/A")
                        for f in data.get("filings", {})
                        .get("recent", {})
                        .get("form", [])
                    )
                    else "check-foreign-issuer-6-K-not-8-K"
                ),
            )
            if symbol not in rows:
                rows[symbol] = {
                    e["id"]: e
                    for e in read(args.out / "sec" / f"{symbol}.json", {}).get(
                        "events", []
                    )
                }
        # Parse followed issuers before a historical index refusal can consume the remaining budget.
        for symbol in priority:
            for row in sorted(
                rows.get(symbol, {}).values(), key=lambda r: r["date"], reverse=True
            ):
                if time.monotonic() - start > args.budget * 0.8:
                    break
                if row["summaryStatus"] != "parsed" and not row["sourceUrl"].endswith(
                    "-index.html"
                ):
                    enrich(row, http)
        # Market-wide discovery uses one official index per trading date rather than issuer polling.
        for offset in range(1, args.days + 1):
            day = args.as_of - timedelta(days=offset)
            if day.weekday() > 4 or day.isoformat() in indexed:
                continue
            if time.monotonic() - start > args.budget * 0.85:
                break
            q = (day.month - 1) // 3 + 1
            url = f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{q}/master.{day:%Y%m%d}.idx"
            try:
                body = http.get(url)
            except Cooldown:
                raise
            except NotPublished:
                continue  # holiday or not published: never mark the cursor complete
            if not re.search(
                rb"CIK\|Company Name\|Form Type\|Date Filed\|File ?[Nn]ame", body
            ):
                raise SourceError("SEC daily index header changed")
            for row in parse_index(body, reverse):
                add(row)
            indexed.add(day.isoformat())
        for group in rows.values():
            for row in sorted(group.values(), key=lambda r: r["date"], reverse=True):
                if (
                    row["summaryStatus"] == "parsed"
                    or time.monotonic() - start > args.budget
                ):
                    continue
                if row["sourceUrl"].endswith("-index.html"):
                    continue
                enrich(row, http)
    except (SourceError, ValueError) as exc:
        error = str(exc)
        print(error, flush=True)
    for symbol, group in rows.items():
        values = sorted(
            group.values(), key=lambda r: (r["date"], r["id"]), reverse=True
        )
        values = [
            r
            for r in values
            if r["date"] >= (args.as_of - timedelta(days=400)).isoformat()
        ]
        write(
            args.out / "sec" / f"{symbol}.json",
            dict(
                meta(
                    "SEC EDGAR",
                    "https://data.sec.gov/",
                    max((r["date"] for r in values), default=None),
                    "stale" if error else "ok",
                    error,
                    checked.get(symbol, {}).get("fetchedAt"),
                ),
                symbol=symbol,
                events=values,
                coverage=checked.get(symbol),
            ),
        )
    recent = []
    for path in (args.out / "sec").glob("*.json"):
        recent += read(path, {}).get("events", [])
    state.update(indexedDates=sorted(indexed), checkedAt=stamp())
    write(args.cache / "sec-radar-state.json", state)
    write(
        args.out / "sec-radar.json",
        dict(
            meta(
                "SEC EDGAR",
                "https://data.sec.gov/",
                max((r["date"] for r in recent), default=None),
                "stale" if error and previous else "unavailable" if error else "ok",
                error,
                stamp(),
            ),
            version=1,
            universe=len(allsymbols),
            mapped=sum(s in mapping for s in allsymbols),
            checkedSymbols=checked,
            indexedDates=sorted(indexed),
            events=radar_events(
                recent, (args.as_of - timedelta(days=min(args.days, 400))).isoformat()
            ),
            coverageNote="Only checked issuers / indexed dates are covered; zero events is not a whole-market no-event claim. 6-K is never recast as 8-K.",
        ),
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=ROOT / "docs/report/research")
    p.add_argument("--cache", type=Path, default=ROOT / ".cache/research")
    p.add_argument("--days", type=int, default=95)
    p.add_argument("--budget", type=int, default=240)
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--symbols", default="AAPL,MSFT,NVDA,TSLA,AMD,META,AMZN,GOOGL")
    p.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    build(p.parse_args())
