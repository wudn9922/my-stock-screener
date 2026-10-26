"""Real daily Yahoo OHLC for world charts; durable CSV history, overwritten view snapshots.

Futures are Yahoo's provider-stitched front contracts, not spot/back-adjusted continuous
contracts. Never synthesize OHLC from statistical single-price observations.
"""

from datetime import datetime, timezone
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo
from research_io import ROOT, Http, SourceError, meta, read, retain, stamp, write
import kline_integrity as kline

ASSETS = {
    "wti": ("WTI 原油", "CL=F", "CMD-WTI", "USD/桶"),
    "brent": ("Brent 原油", "BZ=F", "CMD-BRENT", "USD/桶"),
    "gas": ("天然氣", "NG=F", "CMD-GAS", "USD/MMBtu"),
    "gold": ("黃金", "GC=F", "CMD-GOLD", "USD/金衡盎司"),
    "silver": ("白銀", "SI=F", "CMD-SILVER", "USD/金衡盎司"),
    "copper": ("銅", "HG=F", "CMD-COPPER", "USD/磅"),
    "usdtwd": ("USD/TWD", "TWD=X", "FX-USDTWD", "TWD/USD"),
    "usdjpy": ("USD/JPY", "JPY=X", "FX-USDJPY", "JPY/USD"),
    "eurusd": ("EUR/USD", "EURUSD=X", "FX-EURUSD", "USD/EUR"),
    "usdcny": ("USD/CNY", "CNY=X", "FX-USDCNY", "CNY/USD"),
    "usdkrw": ("USD/KRW", "KRW=X", "FX-USDKRW", "KRW/USD"),
}
FIELDS = ["Date", "Open", "High", "Low", "Close", "Volume"]
PRICE_PRECISION = {"wti": 2, "brent": 2, "gas": 3, "gold": 2, "silver": 3, "copper": 4}


def parse_ohlc(payload, symbol, now=None, refetch=None, log_path=None):
    try:
        item = payload["chart"]["result"][0]
        info = item["meta"]
        if info["symbol"].upper() != symbol.upper():
            raise SourceError("World candle source symbol mismatch")
        if info.get("dataGranularity") != "1d":
            raise SourceError("Source is not daily OHLC")
        zone = ZoneInfo(info["exchangeTimezoneName"])
        clock = now or datetime.now(timezone.utc)
        today = clock.astimezone(zone).date()
        identity = next((a[2] for a in ASSETS.values() if a[1] == symbol), symbol)
        excluded_dates = kline.quarantined_dates(identity, log_path)
        period = info.get("currentTradingPeriod", {}).get("regular", {})
        source_url = "https://query1.finance.yahoo.com/v8/finance/chart/" + quote(symbol, safe="")
        completed = [i for i, ts in enumerate(item["timestamp"])
                     if datetime.fromtimestamp(ts, zone).date() < today
                     and not (period.get("start", float("inf")) <= ts and clock.timestamp() < period.get("end", 0))]
        data = item["indicators"]["quote"][0]
        rows, excluded = {}, 0
        for i, ts in enumerate(item["timestamp"]):
            day = datetime.fromtimestamp(ts, zone).date()
            if day >= today or (period.get("start", float("inf")) <= ts and clock.timestamp() < period.get("end", 0)):
                continue  # forming session excluded; date is the source's calendar date
            if day.isoformat() in excluded_dates:
                continue
            values = [data[k][i] for k in ("open", "high", "low", "close")]
            if any(
                v is None or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in values
            ):
                continue
            o, h, l, c = map(float, values)
            if kline.violation(dict(zip(FIELDS[1:5], (o, h, l, c)))):
                if completed and i == completed[-1] and refetch is not None:
                    # A single raw source retry, never a fabricated/clamped candle.
                    retried, metadata = parse_ohlc(refetch(), symbol, now=clock, log_path=log_path)
                    repaired = next((r for r in retried if r[0] == day.isoformat()), None)
                    if repaired is not None:
                        kline.log_event(identity, day.isoformat(), 'corrected',
                            old=dict(zip(FIELDS[1:5], (o,h,l,c))), new=dict(zip(FIELDS[1:], repaired[1:])),
                            source_url=source_url, fetched_at=stamp(), reason='world-invalid-source-bar-refetched', log_path=log_path)
                    return retried, metadata
                excluded += 1
                kline.log_event(identity, day.isoformat(), "rejected",
                                old=dict(zip(FIELDS[1:5], (o, h, l, c))),
                                source_url=source_url, fetched_at=clock.isoformat(),
                                reason="invalid-world-OHLC", log_path=log_path)
                continue  # vendor FX close/range inconsistencies must never be repaired into fake candles
            volume = data.get("volume", [None] * len(item["timestamp"]))[i]
            if volume is not None and (not math.isfinite(volume) or volume < 0):
                raise SourceError("Invalid source volume")
            if volume is not None:
                volume = int(volume) if float(volume).is_integer() else float(volume)
            rows[day.isoformat()] = [day.isoformat(), o, h, l, c, volume]
        if not rows:
            raise SourceError("No completed daily OHLC observations")
        return [rows[d] for d in sorted(rows)], dict(info, excludedInvalidOHLC=excluded)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise SourceError("World OHLC response schema changed") from exc


def read_csv(path):
    if not path.exists():
        return []
    with path.open() as f:
        return [
            [
                r["Date"],
                *[float(r[k]) for k in FIELDS[1:5]],
                (
                    (
                        int(float(r["Volume"]))
                        if float(r["Volume"]).is_integer()
                        else float(r["Volume"])
                    )
                    if r["Volume"]
                    else None
                ),
            ]
            for r in csv.DictReader(f)
        ]


def save_csv(path, rows, now=None, zone="America/New_York", source_url=None):
    if path.exists() and not {r[0] for r in read_csv(path)} <= {r[0] for r in rows}:
        raise ValueError("Refusing to delete retained K-line dates")
    old = {r[0]: r for r in read_csv(path)}
    accepted = []
    for row in rows:
        values = dict(zip(FIELDS[1:], row[1:]))
        if not kline.session_complete(row[0], path.stem, now=now, zone=zone) and row != old.get(row[0]):
            kline.log_event(path.stem, row[0], 'deferred', old=values,
                            reason='unfinished-world-session', fetched_at=stamp())
            if row[0] in old:
                accepted.append(old[row[0]])
            continue
        # Existing evidence remains intact; new invalid values never enter storage.
        if kline.violation(values) and row != old.get(row[0]):
            raise ValueError("Refusing invalid world OHLC write")
        if row[0] in old and row != old[row[0]]:
            kline.log_event(path.stem, row[0], "corrected",
                            old=dict(zip(FIELDS[1:], old[row[0]][1:])), new=values,
                            source_url=source_url or "https://query1.finance.yahoo.com/v8/finance/chart/" + next((a[1] for a in ASSETS.values() if a[2] == path.stem), path.stem),
                            fetched_at=stamp(), reason="world-source-refresh")
        accepted.append(row)
    rows = accepted
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(FIELDS)
    writer.writerows(rows)
    body = buffer.getvalue()
    if path.exists() and path.read_text() == body:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    try:
        temp.write_text(body)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def build_world_candles(out, data_dir=None, http=None):
    out = Path(out)
    data_dir = Path(data_dir or ROOT / "data" / "world")
    previous = read(out, {})
    assets, http = {}, http or Http(delay=0.8)
    blocked = None
    for key, (name, ticker, identity, unit) in ASSETS.items():
        path = data_dir / (identity + ".csv")
        old = previous.get("series", {}).get(key)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}?range={'1mo' if path.exists() else '5y'}&interval=1d"
        try:
            if blocked is not None:
                raise blocked
            fresh, info = parse_ohlc(http.json(url), ticker, refetch=lambda: http.json(url))
            kind = "forex" if identity.startswith("FX-") else "futures"
            if info.get("instrumentType") != (
                "CURRENCY" if kind == "forex" else "FUTURE"
            ):
                raise SourceError("Unexpected instrument type")
            expected_currency = unit.split("/")[0]
            if info.get("currency") != expected_currency:
                raise SourceError("Source currency disagrees with quote direction")
            rows = {r[0]: r for r in read_csv(path)}
            rows.update({r[0]: r for r in fresh})
            rows = [rows[d] for d in sorted(rows)]
            save_csv(path, rows)
            # Build the snapshot from the permanent record itself, so a bar the record
            # deferred (unfinished session) can never appear in the chart snapshot.
            rows = read_csv(path)
            relative = f"candles/{key}.json"
            excluded_dates = kline.quarantined_dates(identity)
            usable = [r for r in rows if r[0] not in excluded_dates
                      and not kline.violation(dict(zip(FIELDS[1:], r[1:])))
                      and kline.session_complete(r[0], identity, zone=info["exchangeTimezoneName"])]
            if not usable:
                raise SourceError("No usable, completed world candles")
            candle = dict(version=1, symbol=identity, interval="1D", fields=FIELDS, bars=usable)
            write(out.parent / relative, candle)
            body = (out.parent / relative).read_bytes()
            assets[key] = dict(
                meta(
                    "Yahoo Finance daily OHLC",
                    "https://finance.yahoo.com/quote/" + quote(ticker, safe=""),
                    usable[-1][0],
                    fetched=stamp(),
                ),
                name=name,
                pricePrecision=PRICE_PRECISION.get(key, 4),
                symbol=identity,
                providerSymbol=ticker,
                frequency="daily",
                unit=unit,
                instrumentType=kind,
                contract=info.get("shortName"),
                timezone=info["exchangeTimezoneName"],
                latest=usable[-1][4],
                candlePath=relative,
                candleSha256=hashlib.sha256(body).hexdigest(),
                csvSha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                quoteDirection=(
                    f"每 1 {name[:3]} 兌 {name[4:]}" if kind == "forex" else None
                ),
                baseCurrency=name[:3] if kind == "forex" else None,
                quoteCurrency=name[4:] if kind == "forex" else None,
                priceBasis=(
                    "Source OHLC, unadjusted; provider front-contract stitching and roll dates not independently specified"
                    if kind == "futures"
                    else "Source daily OTC FX OHLC, indicative and delayed; no central exchange volume"
                ),
                coverageFrom=usable[0][0],
                observations=len(usable),
                excludedInvalidOHLC=info["excludedInvalidOHLC"],
            )
        except SourceError as exc:
            if exc.status in (403, 429):
                blocked = exc
            print(key, str(exc), flush=True)
            # Preserve previously validated candle snapshots and their real dates.
            assets[key] = retain(old, "Yahoo Finance daily OHLC", url, exc)
    from research_crypto import build_btc_usdt
    assets["btcusdt"] = build_btc_usdt(out, data_dir, http, previous.get("series", {}).get("btcusdt"))
    write(
        out,
        dict(
            version=1,
            methodVersion="research-candles-v1",
            generatedAt=stamp(),
            series=assets,
            limitations="Futures OHLC are provider front-contract histories, not EIA spot or monthly benchmarks; provider roll schedule/adjustment is unspecified. FX are indicative daily OHLC, not Fed H10 statistics, bank retail rates or live quotes. BTC/USDT is Binance spot, quoted in USDT with BTC volume, using 24/7 UTC sessions. Only completed source sessions are included.",
        ),
    )
