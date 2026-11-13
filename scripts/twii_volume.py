"""Fail-closed FMTQIK share-volume conversion for Yahoo's ^TWII index only.

Yahoo ^TWII index volume is NOT the FMTQIK market-wide share count. Never
copy the official shares directly into the Yahoo-basis archive. Conversion
requires >=20 positive, date-matched historical observations and low dispersion.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import math
from statistics import median
from urllib.parse import urlencode

START_DATE = "2026-11-05"
FIELDS = ("日期", "成交股數", "成交金額", "成交筆數", "發行量加權股價指數")
MIN_OVERLAP = 20
MAX_MEDIAN_REL_DEVIATION = 0.06
MAX_P90_REL_DEVIATION = 0.15
CLOSE_TOLERANCE = 0.005
MONTHS = 4


def month_start(day):
    return date.fromisoformat(str(day)[:10]).replace(day=1)


def months_back(day, count=MONTHS):
    first = month_start(day)
    for offset in range(count - 1, -1, -1):
        index = first.year * 12 + first.month - 1 - offset
        year, month = divmod(index, 12)
        yield date(year, month + 1, 1)


def fmtqik_url(month):
    return "https://www.twse.com.tw/exchangeReport/FMTQIK?" + urlencode(
        {"response": "json", "date": month.strftime("%Y%m01")}
    )


def _decimal(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("FMTQIK missing numeric field")
    raw = str(value).replace(",", "").strip()
    if not raw or raw in ("--", "---", "-"):
        raise ValueError("FMTQIK missing numeric field")
    try:
        result = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("FMTQIK invalid numeric field") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("FMTQIK invalid numeric field")
    return result


def parse_fmtqik(doc, month):
    """Reject wrong-month, partial, HTML, malformed and duplicate official data."""
    if not isinstance(doc, dict) or doc.get("stat") != "OK":
        raise ValueError("FMTQIK response is not a successful JSON object")
    stamp = doc.get("date")
    if not isinstance(stamp, str) or len(stamp) != 8 or not stamp.isascii() or not stamp.isdigit():
        raise ValueError("FMTQIK missing/invalid response date")
    try:
        response_date = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
    except ValueError as exc:
        raise ValueError("FMTQIK invalid response date") from exc
    if response_date.year != month.year or response_date.month != month.month:
        raise ValueError("FMTQIK response month differs from requested month")
    fields = doc.get("fields")
    data = doc.get("data")
    if (not isinstance(fields, list) or any(not isinstance(f, str) for f in fields)
            or len(set(fields)) != len(fields) or not set(FIELDS).issubset(fields)
            or not isinstance(data, list) or not data):
        raise ValueError("FMTQIK missing official fields/data")
    positions = [fields.index(field) for field in FIELDS]
    result = {}
    for row in data:
        if not isinstance(row, list) or len(row) != len(fields):
            raise ValueError("FMTQIK row width mismatch")
        raw_day, raw_shares, raw_amount, raw_count, raw_close = [row[i] for i in positions]
        parts = str(raw_day).split("/")
        if len(parts) != 3 or not all(p.isascii() and p.isdigit() for p in parts):
            raise ValueError("FMTQIK invalid ROC date")
        try:
            observation = date(int(parts[0]) + 1911, int(parts[1]), int(parts[2]))
        except ValueError as exc:
            raise ValueError("FMTQIK invalid ROC date") from exc
        if (observation.year, observation.month) != (month.year, month.month):
            raise ValueError("FMTQIK row belongs to another month")
        shares = _decimal(raw_shares)
        amount = _decimal(raw_amount)
        trades = _decimal(raw_count)
        close = _decimal(raw_close)
        if (shares <= 0 or shares != shares.to_integral_value() or amount <= 0
                or trades <= 0 or trades != trades.to_integral_value() or close <= 0):
            raise ValueError("FMTQIK missing/nonpositive market observation")
        key = observation.isoformat()
        if key in result:
            raise ValueError("FMTQIK duplicate date")
        result[key] = (int(shares), close)
    return result


def fetch_official(fetch_json, day, log=lambda message: None):
    """Month-scoped fetch. A bad month contributes *no* data; never trust partial rows."""
    official, errors = {}, []
    for month in months_back(day):
        url = fmtqik_url(month)
        try:
            rows = parse_fmtqik(fetch_json(url), month)
            official.update(rows)
        except Exception as exc:  # noqa: BLE001 - transport, HTTP and parsing fail closed
            reason = f"{month:%Y-%m}: {type(exc).__name__}: {exc}"
            errors.append(reason)
            log("⚠️ ^TWII 官方 FMTQIK 未使用：" + reason)
    return official, errors


def calibrated_ratio(yahoo, official, min_overlap=MIN_OVERLAP):
    """Return (ratio, diagnostic); ratio=None means all official fills must be skipped.

    ratio = Yahoo archived volume / official FMTQIK traded shares, NOT its inverse.
    Use the 40 most recent eligible positive-volume overlapping sessions.
    """
    observations = []
    for day in sorted(set(yahoo) & set(official)):
        try:
            raw = yahoo[day]
            value = float(raw) if raw is not None else 0
            shares = official[day][0]
            if math.isfinite(value) and value > 0 and shares > 0:
                observations.append((day, value / shares))
        except (TypeError, ValueError, OverflowError, KeyError, IndexError):
            continue
    observations = observations[-40:]
    result = {"overlap": len(observations), "required": min_overlap, "ratio": None,
              "medianRelativeDeviation": None, "p90RelativeDeviation": None,
              "firstOverlap": observations[0][0] if observations else None,
              "lastOverlap": observations[-1][0] if observations else None}
    if len(observations) < min_overlap:
        return None, {**result, "reason": "insufficient-overlap"}
    factor = median(r for _, r in observations)
    if not math.isfinite(factor) or factor <= 0:
        return None, {**result, "reason": "invalid-ratio"}
    deviations = sorted(abs(r / factor - 1) for _, r in observations)
    mad = median(deviations)
    p90 = deviations[math.ceil(0.9 * len(deviations)) - 1]
    result.update(ratio=factor, medianRelativeDeviation=mad, p90RelativeDeviation=p90)
    if mad > MAX_MEDIAN_REL_DEVIATION or p90 > MAX_P90_REL_DEVIATION:
        return None, {**result, "reason": "unstable-ratio"}
    return factor, {**result, "reason": "stable"}


def fill_rows(rows, official, factor):
    """Preserve date/OHLC/order; edit only missing/zero ^TWII volumes after START_DATE."""
    if factor is None or not math.isfinite(factor) or factor <= 0:
        return list(rows), []
    output, changes = [], []
    for row in rows:
        row = tuple(row)
        day = str(row[0])[:10]
        raw = row[5]
        try:
            missing = raw is None or str(raw).strip().lower() in ("", "nan", "none", "<na>")
            zero = not missing and float(raw) == 0
            if day < START_DATE or not (missing or zero) or day not in official:
                output.append(row)
                continue
            shares, official_close = official[day]
            yahoo_close = Decimal(str(row[4]))
            if (yahoo_close <= 0 or abs(official_close / yahoo_close - 1) > Decimal(str(CLOSE_TOLERANCE))):
                output.append(row)
                continue
            value = int((Decimal(shares) * Decimal(str(factor))).quantize(
                Decimal("1"), rounding=ROUND_HALF_EVEN))
            if value <= 0:
                output.append(row)
                continue
            replacement = (*row[:5], value)
            output.append(replacement)
            changes.append({"date": day, "old": raw, "new": value})
        except (InvalidOperation, ValueError, TypeError, OverflowError):
            output.append(row)
    return output, changes


def fill_yahoo_rows(symbol, rows, fetch_json, log=lambda message: None):
    """Network-bound adapter for the normal Yahoo benchmark path; all other symbols untouched."""
    if symbol != "^TWII" or not any(
        str(r[0])[:10] >= START_DATE and (r[5] is None or r[5] == 0) for r in rows
    ):
        return rows
    official, errors = fetch_official(fetch_json, rows[-1][0], log)
    factor, stats = calibrated_ratio({r[0]: r[5] for r in rows}, official)
    log(f"^TWII FMTQIK basis: {stats}; officialErrors={len(errors)}")
    if factor is None:
        return rows
    changed, changes = fill_rows(rows, official, factor)
    for item in changes:
        log(f"^TWII {item['date']} Yahoo {item['old']} → FMTQIK-adjusted {item['new']} (ratio {factor:.10g})")
    return changed
