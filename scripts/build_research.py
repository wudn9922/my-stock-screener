"""Independent research builders. python scripts/build_research.py institutional|volume|alerts

Backfill is bounded by --days/--budget and resumes using validated cached report dates.
Only expected source errors are retained; programmer errors fail the job.
"""

from __future__ import annotations
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
import time
from research_io import (
    ROOT,
    Http,
    SourceError,
    market_paths,
    meta,
    price_rows,
    read,
    retain,
    stamp,
    universe,
    write,
)
from research_metrics import (
    cost_estimate,
    institutional_summary,
    volume_stats,
    enrich_turnover_snapshot,
)
from research_sources import parse_alerts, parse_flows, parse_market_turnover

FLOW_URLS = {
    "TW": "https://www.twse.com.tw/rwd/zh/fund/T86?date={ymd}&selectType=ALLBUT0999&response=json",
    "TWO": "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&date={slash}&id=&response=csv",
}
TURNOVER_URLS = {
    "TW": "https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={ymd}&type=ALLBUT0999",
    "TWO": "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={slash}&id=&response=json",
}
ALERT_URLS = {
    "TW:notice": "https://openapi.twse.com.tw/v1/announcement/notice",
    "TW:disposal": "https://openapi.twse.com.tw/v1/announcement/punish",
    "TWO:notice": "https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information",
    "TWO:disposal": "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information",
}


def institutional(args):
    http, start = Http(delay=1), time.monotonic()
    members, prices = universe(), market_paths(args.cache)
    wanted = members["TW"]
    index = read(args.out / "institutions.json", {})
    sources = dict(index.get("sources", {}))
    for market, template in FLOW_URLS.items():
        old = sources.get(market)
        dates = set((old or {}).get("dates", []))
        error = None
        refreshed = False
        # newest first guarantees current data before cold history consumes the budget
        for offset in range(args.days):
            if time.monotonic() - start > args.budget:
                break
            day = args.as_of - timedelta(days=offset)
            turnover_path = args.out / "turnover" / market / (day.isoformat() + ".json")
            if day.weekday() > 4 or (
                day.isoformat() in dates and offset > 1 and turnover_path.exists()
            ):
                continue
            cache = args.cache / "institutions" / market / (day.isoformat() + ".json")
            try:
                raw = None if offset <= 1 else read(cache)
                if raw is None:
                    url = template.format(
                        ymd=day.strftime("%Y%m%d"), slash=day.strftime("%Y/%m/%d")
                    )
                    raw = parse_flows(http.get(url), market, day.isoformat())
                    if raw:
                        write(cache, raw)
                        refreshed = True
                if not raw:
                    continue  # closed/unpublished; no synthetic zero day or cached empty response
                covered = {s: r for s, r in raw.items() if s in wanted}
                target = {s for s in wanted if s.endswith("." + market)}
                if len(covered) < len(target) * 0.5:
                    raise SourceError(
                        f"{market}: coverage below 50%, refusing partial date"
                    )
                dates.add(day.isoformat())
                # Market-wide official daily cash input, independent of institutional fill prices.
                if offset <= 1 or not turnover_path.exists():
                    quote_url = TURNOVER_URLS[market].format(
                        ymd=day.strftime("%Y%m%d"), slash=day.strftime("%Y/%m/%d")
                    )
                    previous_quote = read(turnover_path)
                    try:
                        quote_rows = parse_market_turnover(
                            http.get(quote_url), market, day.isoformat()
                        )
                        quote_rows = {
                            s: q for s, q in quote_rows.items() if s in wanted
                        }
                        if len(quote_rows) < len(target) * 0.5:
                            raise SourceError("Official turnover coverage below 50%")
                        write(
                            turnover_path,
                            dict(
                                meta(
                                    "TWSE/TPEx official daily turnover",
                                    quote_url,
                                    day.isoformat(),
                                    fetched=stamp(),
                                ),
                                currency="TWD",
                                volumeUnit="shares",
                                rows=quote_rows,
                            ),
                        )
                    except SourceError as exc:
                        print("Turnover retained / proxy fallback:", exc, flush=True)
                        if previous_quote:
                            write(
                                turnover_path,
                                retain(
                                    previous_quote,
                                    "TWSE/TPEx official daily turnover",
                                    quote_url,
                                    exc,
                                ),
                            )
                        if getattr(exc, "status", None) in (403, 429):
                            break

            except SourceError as exc:
                error = str(exc)
                print(error, flush=True)
                break
        if dates:
            sources[market] = dict(
                meta(
                    "TWSE T86" if market == "TW" else "TPEx dailyTrade",
                    template,
                    max(dates),
                    (
                        "stale"
                        if error
                        else "ok" if refreshed else (old or {}).get("status", "ok")
                    ),
                    error
                    or ((old or {}).get("missingReason") if not refreshed else None),
                    stamp() if refreshed else (old or {}).get("fetchedAt"),
                ),
                dates=sorted(dates)[-120:],
            )
        else:
            sources[market] = retain(
                old,
                market,
                template,
                error or "No published observations yet / budget paused",
            )
    cached_dates = {
        m: {
            d: read(args.cache / "institutions" / m / (d + ".json"), {})
            for d in source.get("dates", [])
        }
        for m, source in sources.items()
    }
    # Public raw inputs survive cache eviction, with a bounded rolling archive.
    for market in FLOW_URLS:
        for obsolete in sorted((args.out / "turnover" / market).glob("*.json"))[:-120]:
            obsolete.unlink()
    turnover = {
        m: {p.stem: read(p) for p in (args.out / "turnover" / m).glob("*.json")}
        for m in FLOW_URLS
    }
    for symbol in sorted(wanted):
        market = symbol.split(".")[-1]
        source = sources[market]
        rows = []
        for day in source.get("dates", []):
            raw = cached_dates[market][day]
            if symbol in raw:
                row = dict(raw[symbol])
                quote = turnover[market].get(day, {}).get("rows", {}).get(symbol)
                if quote:
                    row["market"] = quote
                rows.append(row)
        path = args.out / "institutions" / (symbol + ".json")
        prior = read(path, {})
        # Rehydrate from published shards when Actions cache was evicted.
        merged = {r["date"]: r for r in prior.get("rows", [])}
        merged.update({r["date"]: {**merged.get(r["date"], {}), **r} for r in rows})
        rows = [merged[d] for d in sorted(merged)][-120:]
        if not rows:
            continue
        p = price_rows(prices[symbol]) if symbol in prices else []
        # Exclude intraday CSV rows. Cached prices are source-split-adjusted, dividend-unadjusted.
        p = [r for r in p if r["date"] <= args.as_of.isoformat()]
        pmap = {r["date"]: r for r in p}
        # Official same-day closes can complete E's price inputs when a report/cache lags.
        latest_canonical = max(p, key=lambda x: x["date"]) if p else None
        for day, pack in turnover[market].items():
            quote = pack.get("rows", {}).get(symbol)
            if quote and day not in pmap:
                action = (
                    "unverified-price-basis"
                    if latest_canonical
                    and day > latest_canonical["date"]
                    and latest_canonical.get("close")
                    and abs(quote["close"] / latest_canonical["close"] - 1) > 0.105
                    else None
                )
                extra = dict(
                    date=day,
                    close=quote["close"],
                    volume=quote["volume"],
                    amount=quote["amount"],
                    action=action,
                )
                p.append(extra)
                pmap[day] = extra
        # A suspected split/adjustment break must not mix native price scales in E.
        ordered = sorted(p, key=lambda x: x["date"])
        breaks = [
            b["date"]
            for a, b in zip(ordered, ordered[1:])
            if a.get("close")
            and b.get("close")
            and abs(b["close"] / a["close"] - 1) > 0.4
        ]
        if breaks:
            for row in p:
                if row["date"] <= max(breaks):
                    row["action"] = row.get("action") or "unverified-price-basis-break"

        for r in rows:
            quote = turnover[market].get(r["date"], {}).get("rows", {}).get(symbol)
            if quote:
                r["market"] = quote
            price = pmap.get(r["date"])
            r["close"] = price.get("close") if price else None
            volume = r.get("market", {}).get("volume") or ((price or {}).get("volume"))
            r["netVolumePct"] = (
                r["foreign"]["net"] / volume * 100 if volume and volume > 0 else None
            )
        write(
            path,
            dict(
                meta(
                    source["source"],
                    source["sourceUrl"],
                    rows[-1]["date"],
                    source["status"],
                    source.get("missingReason"),
                    source.get("fetchedAt"),
                ),
                symbol=symbol,
                market=market,
                currency="TWD",
                unit="shares",
                rows=rows,
                summary=institutional_summary(rows, source.get("dates")),
                costs={
                    role: {
                        str(w): cost_estimate(rows, p, w, role, source.get("dates"))
                        for w in (5, 20, 60)
                    }
                    for role in ("foreign", "trust", "dealer")
                },
                marketPriceSource=(
                    {
                        k: v
                        for k, v in max(
                            turnover[market].values(), key=lambda x: x["asOf"]
                        ).items()
                        if k != "rows"
                    }
                    if turnover[market]
                    else None
                ),
                priceBasis="source-split-adjusted; dividends unadjusted; corporate-action share consistency not independently verified",
            ),
        )
    counts = {
        m: dict(
            universe=sum(s.endswith("." + m) for s in wanted),
            covered=sum(
                (args.out / "institutions" / (s + ".json")).exists()
                for s in wanted
                if s.endswith("." + m)
            ),
        )
        for m in FLOW_URLS
    }
    write(
        args.out / "institutions.json",
        dict(
            version=1,
            generatedAt=stamp(),
            sources=sources,
            universe=len(wanted),
            covered=counts,
            receipts=http.receipts[-10:],
            historyNote="Up to 120 published sessions; incremental, partial windows expose observations.",
        ),
    )


def volume(args):
    members, paths, counts, latest = universe(), market_paths(args.cache), {}, {}
    turnover = {
        m: {
            p.stem: read(p).get("rows", {})
            for p in (args.out / "turnover" / m).glob("*.json")
        }
        for m in FLOW_URLS
    }
    cash_metadata = {
        m: [
            {k: v for k, v in read(p).items() if k != "rows"}
            for p in (args.out / "turnover" / m).glob("*.json")
        ]
        for m in FLOW_URLS
    }

    def cash_source(symbol, asof):
        candidates = [
            m for m in cash_metadata.get(symbol.split(".")[-1], []) if m["asOf"] <= asof
        ]
        return max(candidates, key=lambda m: m["asOf"]) if candidates else None

    for market, symbols in members.items():
        count = 0
        for symbol in sorted(symbols):
            path = args.out / "volume" / (symbol + ".json")
            previous = read(path)
            if symbol not in paths:
                if previous:
                    if market == "TW":
                        quotes = {
                            d: rows[symbol]
                            for d, rows in turnover[symbol.split(".")[-1]].items()
                            if symbol in rows
                        }
                        previous = enrich_turnover_snapshot(previous, quotes)
                        previous["marketAmountSource"] = cash_source(
                            symbol, previous["asOf"]
                        )
                    stale = (args.as_of - date.fromisoformat(previous["asOf"])).days > 5
                    previous["status"] = "stale" if stale else "ok"
                    write(path, previous)
                continue
            rows = price_rows(paths[symbol])
            # Existing CSV caches are completed-session history. Do not include today's row before close.
            from zoneinfo import ZoneInfo

            now = datetime.now(
                ZoneInfo("Asia/Taipei" if market == "TW" else "America/New_York")
            )
            closed = now.hour >= (15 if market == "TW" else 17)
            rows = [
                r
                for r in rows
                if r["date"] < now.date().isoformat()
                or (closed and r["date"] == now.date().isoformat())
            ]
            if market == "TW":
                exchange = symbol.split(".")[-1]
                for row in rows:
                    quote = turnover[exchange].get(row["date"], {}).get(symbol)
                    if quote:
                        row["amount"] = quote["amount"]
                        row["officialVolume"] = quote["volume"]
            stats = volume_stats(rows)
            if not stats:
                continue
            asof = stats.pop("asOf")
            stale = (args.as_of - date.fromisoformat(asof)).days > 5
            write(
                args.out / "volume" / (symbol + ".json"),
                dict(
                    meta(
                        "Existing daily OHLCV cache",
                        "https://github.com/wudn9922/my-stock-screener",
                        asof,
                        "stale" if stale else "ok",
                        "cached completed daily bars; source retrieval timestamp unavailable",
                        None,
                    ),
                    symbol=symbol,
                    market=market,
                    currency="TWD" if market == "TW" else "USD",
                    priceBasis="source-split-adjusted; dividend-unadjusted; volume adjustment not independently verified",
                    marketAmountSource=(
                        cash_source(symbol, asof) if market == "TW" else None
                    ),
                    **stats,
                ),
            )
            count += 1
            latest[market] = max(latest.get(market, ""), asof)
        existing = [
            read(args.out / "volume" / (s + ".json"))
            for s in symbols
            if (args.out / "volume" / (s + ".json")).exists()
        ]
        count = len(existing)
        latest[market] = max((r["asOf"] for r in existing), default=None)
        report_members = symbols & {p.stem for p in (ROOT / "data").glob("*.csv")}
        counts[market] = dict(
            universe=len(symbols),
            covered=count,
            coveragePct=count / len(symbols) * 100 if symbols else None,
            dailyReport=dict(
                universe=len(report_members),
                covered=sum(
                    (args.out / "volume" / (s + ".json")).exists()
                    for s in report_members
                ),
            ),
            asOf=latest.get(market),
            scope="existing full-market cache + daily-report CSV union",
        )
    write(
        args.out / "volume.json",
        dict(
            version=1, methodVersion="research-v1", generatedAt=stamp(), markets=counts
        ),
    )


def alerts(args):
    http = Http(delay=1)
    previous = read(args.out / "alerts.json", {})
    events = {r["id"]: r for r in previous.get("events", [])}
    sources = dict(previous.get("sources", {}))
    for key, url in ALERT_URLS.items():
        market, kind = key.split(":")
        old = sources.get(key)
        try:
            raw = http.json(url)
            rows = parse_alerts(raw, market, kind, url, stamp())
            # Placeholder/empty latest feeds cannot establish the effective report day.
            recent = max((r["date"] for r in rows), default=None)
            sources[key] = dict(
                meta("TWSE/TPEx official announcement", url, recent, fetched=stamp()),
                historyFrom=(old or {}).get("historyFrom", recent),
            )
            for r in rows:
                events[r["id"]] = r
            begin = args.as_of - timedelta(days=max(args.days, 95))
            if market == "TWO":
                history_url = f'https://www.tpex.org.tw/www/zh-tw/bulletin/{"attention" if kind == "notice" else "disposal"}?startDate={begin:%Y/%m/%d}&endDate={args.as_of:%Y/%m/%d}&response=json'
            else:
                history_url = f'https://www.twse.com.tw/announcement/{"notice" if kind == "notice" else "punish"}?startDate={begin:%Y%m%d}&endDate={args.as_of:%Y%m%d}&response=json'
            history_rows = parse_alerts(
                http.json(history_url), market, kind, history_url, stamp()
            )
            for r in history_rows:
                # Latest feed carries the newest full official disposition text.
                events.setdefault(r["id"], r)
            sources[key]["historyFrom"] = begin.isoformat()
            sources[key]["historyUrl"] = history_url
            sources[key]["asOf"] = max(
                (r["date"] for r in rows + history_rows), default=None
            )
            if sources[key]["asOf"] is None:
                raise SourceError(
                    "No dated announcements; effective report date unknown"
                )
        except SourceError as exc:
            sources[key] = retain(old, "TWSE/TPEx official announcement", url, exc)
    # Keep historical notices; snapshot size bounded to 400 calendar days. Dispositions are
    # retained even beyond scheduled end: suspensions can extend them, never infer "released".
    cutoff = (args.as_of - timedelta(days=400)).isoformat()
    values = sorted(
        (r for r in events.values() if r["date"] >= cutoff or r["kind"] == "disposal"),
        key=lambda r: (r["date"], r["id"]),
        reverse=True,
    )
    write(
        args.out / "alerts.json",
        dict(
            version=1,
            methodVersion="research-v1",
            generatedAt=stamp(),
            asOf=max((r["date"] for r in values), default=None),
            queriedThrough=args.as_of.isoformat(),
            sources=sources,
            events=values,
            receipts=http.receipts,
            disclaimer="Published disposition intervals may extend on suspension/closure. Past scheduled end is not confirmation of release.",
        ),
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("module", choices=["institutional", "volume", "alerts"])
    p.add_argument("--out", type=Path, default=ROOT / "docs/report/research")
    p.add_argument("--cache", type=Path, default=ROOT / ".cache/universe-full")
    p.add_argument("--days", type=int, default=95)
    p.add_argument("--budget", type=int, default=600)
    p.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = p.parse_args()
    globals()[args.module](args)


if __name__ == "__main__":
    main()
