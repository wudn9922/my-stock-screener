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


def parse_ohlc(payload, symbol, now=None):
    try:
        item = payload["chart"]["result"][0]
        info = item["meta"]
        if info["symbol"].upper() != symbol.upper():
            raise SourceError("World candle source symbol mismatch")
        if info.get("dataGranularity") != "1d":
            raise SourceError("Source is not daily OHLC")
        zone = ZoneInfo(info["exchangeTimezoneName"])
        today = (now or datetime.now(timezone.utc)).astimezone(zone).date()
        data = item["indicators"]["quote"][0]
        rows, excluded = {}, 0
        for i, ts in enumerate(item["timestamp"]):
            day = datetime.fromtimestamp(ts, zone).date()
            if day >= today:
                continue  # forming session excluded; date is the source's calendar date
            values = [data[k][i] for k in ("open", "high", "low", "close")]
            if any(
                v is None or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in values
            ):
                continue
            o, h, l, c = map(float, values)
            if h < max(o, c, l) or l > min(o, c, h):
                excluded += 1
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


def save_csv(path, rows):
    if path.exists() and not {r[0] for r in read_csv(path)} <= {r[0] for r in rows}:
        raise ValueError("Refusing to delete retained K-line dates")
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
            fresh, info = parse_ohlc(http.json(url), ticker)
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
            relative = f"candles/{key}.json"
            candle = dict(
                version=1, symbol=identity, interval="1D", fields=FIELDS, bars=rows
            )
            write(out.parent / relative, candle)
            body = (out.parent / relative).read_bytes()
            assets[key] = dict(
                meta(
                    "Yahoo Finance daily OHLC",
                    "https://finance.yahoo.com/quote/" + quote(ticker, safe=""),
                    rows[-1][0],
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
                latest=rows[-1][4],
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
                coverageFrom=rows[0][0],
                observations=len(rows),
                excludedInvalidOHLC=info["excludedInvalidOHLC"],
            )
        except SourceError as exc:
            if exc.status in (403, 429):
                blocked = exc
            print(key, str(exc), flush=True)
            # Preserve previously validated candle snapshots and their real dates.
            assets[key] = retain(old, "Yahoo Finance daily OHLC", url, exc)
    write(
        out,
        dict(
            version=1,
            methodVersion="research-candles-v1",
            generatedAt=stamp(),
            series=assets,
            limitations="Futures OHLC are provider front-contract histories, not EIA spot or monthly benchmarks; provider roll schedule/adjustment is unspecified. FX are indicative daily OHLC, not Fed H10 statistics, bank retail rates or live quotes. Only completed source sessions are included.",
        ),
    )
