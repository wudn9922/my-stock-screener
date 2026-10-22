"""Official EIA daily spot energy, World Bank monthly metals, Federal Reserve H.10 daily FX.

No paid keys, browser secrets, futures stitching or interpolation. Monthly 1d/1w is N/A.
"""

from __future__ import annotations
import argparse
import calendar
import csv
import io
from datetime import date, timedelta
from pathlib import Path
import re
from zipfile import BadZipFile
from xlrd.biffh import XLRDError
import pandas as pd
from research_io import ROOT, Http, SourceError, meta, read, retain, stamp, write

WB = "https://thedocs.worldbank.org/en/doc/5d903e848db1d1b83e0ec8f744e55570-0350012021/related/CMO-Historical-Data-Monthly.xlsx"
ENERGY = [
    ("wti", "WTI 原油", "pet", "RWTCd", "USD/桶"),
    ("brent", "Brent 原油", "pet", "RBRTEd", "USD/桶"),
    ("gas", "Henry Hub 天然氣", "ng", "RNGWHHDd", "USD/MMBtu"),
]
METALS = [
    ("gold", "黃金", "Gold", "USD/金衡盎司"),
    ("silver", "白銀", "Silver", "USD/金衡盎司"),
    ("copper", "銅", "Copper", "USD/公噸"),
]
FX = [
    ("usdtwd", "USD/TWD", "ta", "USD", "TWD"),
    ("usdjpy", "USD/JPY", "ja", "USD", "JPY"),
    ("eurusd", "EUR/USD", "eu", "EUR", "USD"),
    ("usdcny", "USD/CNY", "ch", "USD", "CNY"),
    ("usdkrw", "USD/KRW", "ko", "USD", "KRW"),
]
ECB = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.csv"


def parse_ecb(body, base, quote):
    out = []
    for row in csv.DictReader(io.StringIO(body.decode("utf-8-sig"))):
        try:
            base_value = 1.0 if base == "EUR" else float(row[base])
            quote_value = 1.0 if quote == "EUR" else float(row[quote])
        except (ValueError, KeyError):
            continue
        if base_value > 0 and quote_value > 0:
            out.append((row["Date"], quote_value / base_value))
    return points(out)


def points(values):
    out = {}
    for day, value in values:
        try:
            v = float(value)
        except (ValueError, TypeError):
            continue
        if pd.notna(v):
            out[str(day)[:10]] = v
    return [[d, out[d]] for d in sorted(out)]


def changes(history, frequency, today=None):
    if not history:
        return {}
    asof, latest = history[-1]
    day = date.fromisoformat(asof)
    today = today or date.today()

    def months_ago(months):
        year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
        month += 1
        return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))

    result = {}
    for key, offset in [
        ("1d", 1),
        ("1w", 7),
        ("1m", 30),
        ("3m", 90),
        ("ytd", None),
        ("1y", 365),
    ]:
        target = (
            date(day.year, 1, 1) - timedelta(days=1)
            if offset is None
            else day - timedelta(days=offset)
        )
        if key in ("1m", "3m", "1y"):
            target = months_ago({"1m": 1, "3m": 3, "1y": 12}[key])
        if key == "ytd" and day.year != today.year:
            result[key] = None
            continue
        prior = next(
            (
                [d, v]
                for d, v in reversed(history[:-1])
                if date.fromisoformat(d) <= target
            ),
            None,
        )
        # A statistical month cannot produce a daily/weekly return. Never forward fill.
        tolerance = 45 if frequency == "monthly" else 7
        result[key] = (
            (latest / prior[1] - 1) * 100
            if prior
            and prior[1] != 0
            and (target - date.fromisoformat(prior[0])).days <= tolerance
            and not (
                (frequency == "monthly" and key in ("1d", "1w"))
                or (frequency == "weekly" and key == "1d")
            )
            else None
        )
    return result


def parse_h10(body):
    try:
        tables = pd.read_html(io.BytesIO(body))
    except ValueError as exc:
        raise SourceError("H10 missing daily exchange rate table") from exc
    for table in reversed(tables):
        if list(table.columns) != ["Date", "Rate"]:
            continue
        parsed = pd.to_datetime(table["Date"], format="%d-%b-%y", errors="coerce")
        return points(
            (d.date().isoformat(), v)
            for d, v in zip(parsed, table["Rate"])
            if pd.notna(d)
        )
    raise SourceError("H10 Date/Rate columns changed")


def parse_eia(body):
    try:
        frame = pd.read_excel(io.BytesIO(body), sheet_name="Data 1", skiprows=2)
    except (ValueError, KeyError, BadZipFile, XLRDError) as exc:
        raise SourceError("EIA XLS Data 1 schema changed") from exc
    if len(frame.columns) != 2 or frame.columns[0] != "Date":
        raise SourceError("EIA Date/value columns changed")
    return points(
        (d.date().isoformat(), v)
        for d, v in frame.itertuples(index=False, name=None)
        if isinstance(d, pd.Timestamp)
    )


def parse_wb(body):
    try:
        frame = pd.read_excel(io.BytesIO(body), sheet_name="Monthly Prices", header=4)
    except (ValueError, KeyError, BadZipFile, XLRDError) as exc:
        raise SourceError("World Bank XLSX schema changed") from exc
    out = {}
    for _, _, key, _ in METALS:
        if key not in frame:
            raise SourceError("World Bank missing " + key)
        out[key] = points(
            (str(row.iloc[0])[:4] + "-" + str(row.iloc[0])[-2:] + "-01", row[key])
            for _, row in frame.iterrows()
            if re.fullmatch(r"\d{4}M\d{2}", str(row.iloc[0]))
        )
    return out


def series(history, name, source, url, frequency, unit, kind, http, **extra):
    if not history:
        raise SourceError("No valid observations")
    # 15 years of daily / monthly history, original sampling, never filled.
    history = [
        p
        for p in history
        if p[0] >= (date.today() - timedelta(days=15 * 366)).isoformat()
    ]
    asof = history[-1][0]
    age = (date.today() - date.fromisoformat(asof)).days
    return dict(
        meta(
            source,
            url,
            asof,
            "stale" if age > (62 if frequency == "monthly" else 10) else "ok",
            None,
            stamp(),
            None,
        ),
        sourceLastModified=http.receipts[-1].get("lastModified"),
        name=name,
        frequency=frequency,
        unit=unit,
        currency="USD" if kind != "statistical-fx" else None,
        instrumentType=kind,
        latest=history[-1][1],
        history=history,
        changes=changes(history, frequency),
        **extra,
    )


def build(out):
    previous = read(out, {})
    assets = dict(previous.get("series", {}))
    http = Http(delay=0.75)
    for key, name, area, code, unit in ENERGY:
        url = f"https://www.eia.gov/dnav/{area}/hist_xls/{code}.xls"
        try:
            assets[key] = series(
                parse_eia(http.get(url)),
                name,
                "US EIA",
                url,
                "daily",
                unit,
                "spot",
                http,
            )
        except SourceError as exc:
            assets[key] = retain(assets.get(key), "US EIA", url, exc)
    try:
        metals = parse_wb(http.get(WB))
        for key, name, column, unit in METALS:
            assets[key] = series(
                metals[column],
                name,
                "World Bank Pink Sheet",
                WB,
                "monthly",
                unit,
                "monthly-physical-market-benchmark",
                http,
            )
    except SourceError as exc:
        for key, _, _, _ in METALS:
            assets[key] = retain(assets.get(key), "World Bank Pink Sheet", WB, exc)
    ecb = None
    for key, name, code, base, quote in FX:
        url = f"https://www.federalreserve.gov/releases/h10/hist/dat00_{code}.htm"
        try:
            assets[key] = series(
                parse_h10(http.get(url)),
                name,
                "Federal Reserve H.10",
                url,
                "daily",
                f"{quote}/{base}",
                "statistical-fx",
                http,
                baseCurrency=base,
                quoteCurrency=quote,
                quoteDirection=f"每 1 {base} 兌 {quote}",
                timezone="America/New_York",
            )
        except SourceError as exc:
            if base != "EUR" and quote == "TWD":
                assets[key] = retain(assets.get(key), "Federal Reserve H.10", url, exc)
                continue
            try:
                if ecb is None:
                    ecb = http.get(ECB)
                assets[key] = series(
                    parse_ecb(ecb, base, quote),
                    name,
                    "ECB daily reference rates",
                    ECB,
                    "daily",
                    f"{quote}/{base}",
                    "statistical-fx",
                    http,
                    baseCurrency=base,
                    quoteCurrency=quote,
                    quoteDirection=f"每 1 {base} 兌 {quote}",
                    timezone="Europe/Frankfurt",
                    calculation="EUR reference cross: EUR/quote divided by EUR/base, same observation date",
                    fallbackReason=str(exc),
                )
            except SourceError as fallback:
                assets[key] = retain(
                    assets.get(key), "ECB / Federal Reserve", ECB, fallback
                )
    write(
        out,
        dict(
            version=1,
            methodVersion="research-v1",
            generatedAt=stamp(),
            series=assets,
            receipts=http.receipts,
            licensing={
                "EIA": "https://www.eia.gov/about/copyrights_reuse.php",
                "Federal Reserve": "https://www.federalreserve.gov/disclaimer.htm",
                "World Bank": "https://www.worldbank.org/en/about/legal/terms-of-use-for-datasets",
            },
            limitations="Statistical daily observations are delayed, not bank retail rates or live FX. World Bank monthly series are monthly averages, not futures or live spot quotes.",
        ),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "docs/report/research/world-assets.json"
    )
    build(parser.parse_args().out)
