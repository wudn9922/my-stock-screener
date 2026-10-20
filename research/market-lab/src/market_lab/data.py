from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


def market_for_symbol(symbol: str) -> str:
    if symbol == "^TWOII" or symbol.endswith((".TW", ".TWO")):
        return "TW"
    return "US"


def instrument_for_symbol(symbol: str) -> str:
    return "index" if symbol.startswith("^") else "equity"


def _parse_dates(values: pd.Series) -> pd.Series:
    def parse_one(value: Any) -> pd.Timestamp:
        try:
            stamp = pd.Timestamp(value)
        except (TypeError, ValueError):
            return pd.NaT
        if pd.isna(stamp):
            return pd.NaT
        # Keep the date printed by the exchange source; do not shift a date-only
        # bar across midnight by converting it to another market's timezone.
        return stamp.tz_localize(None) if stamp.tzinfo is not None else stamp

    return pd.to_datetime(values.map(parse_one), errors="coerce").dt.normalize()


def clean_ohlcv(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Normalize OHLCV while reporting discarded rows; never infer adjustments."""
    stats = {
        "raw_rows": int(len(raw)),
        "invalid_dates": 0,
        "duplicate_dates": 0,
        "weekend_rows": 0,
        "invalid_ohlc_rows": 0,
        "negative_volume_rows": 0,
        "missing_volume_rows": 0,
    }
    if raw.empty or "Date" not in raw.columns:
        return pd.DataFrame(columns=PRICE_COLUMNS, index=pd.DatetimeIndex([], name="Date")), stats

    frame = raw.copy()
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = [column for column in PRICE_COLUMNS if column not in frame.columns]
    if missing:
        return pd.DataFrame(columns=PRICE_COLUMNS, index=pd.DatetimeIndex([], name="Date")), stats

    dates = _parse_dates(frame["Date"])
    stats["invalid_dates"] = int(dates.isna().sum())
    frame = frame.loc[~dates.isna(), list(PRICE_COLUMNS)].copy()
    frame.index = pd.DatetimeIndex(dates.loc[~dates.isna()], name="Date")
    stats["duplicate_dates"] = int(frame.index.duplicated(keep="last").sum())
    frame = frame.loc[~frame.index.duplicated(keep="last")].sort_index()
    stats["weekend_rows"] = int((frame.index.dayofweek >= 5).sum())
    frame = frame.loc[frame.index.dayofweek < 5].copy()

    for column in PRICE_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    stats["missing_volume_rows"] = int(frame["Volume"].isna().sum())
    stats["negative_volume_rows"] = int((frame["Volume"] < 0).fillna(False).sum())

    prices = frame[["Open", "High", "Low", "Close"]]
    positive = (prices > 0).all(axis=1)
    finite = np.isfinite(prices.to_numpy(dtype=float)).all(axis=1)
    coherent = (
        (frame["High"] >= frame[["Open", "Close", "Low"]].max(axis=1))
        & (frame["Low"] <= frame[["Open", "Close", "High"]].min(axis=1))
    )
    valid = positive & finite & coherent & (frame["Volume"].isna() | (frame["Volume"] >= 0))
    stats["invalid_ohlc_rows"] = int((~valid).sum())
    frame = frame.loc[valid, list(PRICE_COLUMNS)].copy()
    return frame, stats


def _period_summary(obj: dict[str, Any], key: str) -> tuple[int, str, str]:
    values = obj.get(key, {}) if isinstance(obj, dict) else {}
    dates = values.get("end", []) if isinstance(values, dict) else []
    dates = [str(value) for value in dates if value]
    return len(dates), (dates[0] if dates else ""), (dates[-1] if dates else "")


def _us_financial_record(path: Path) -> dict[str, Any] | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    annual_count, annual_first, annual_last = _period_summary(obj, "annual")
    quarter_count, quarter_first, quarter_last = _period_summary(obj, "quarterly")
    quarter = obj.get("quarterly", {}) if isinstance(obj, dict) else {}
    # Standardized current snapshots carry period-end dates, not point-in-time
    # filing timestamps. Count only explicit filing-date arrays if supplied.
    announcement_count = 0
    for key in ("filed", "filingDate", "filedAt", "acceptedAt", "acceptanceDate"):
        values = quarter.get(key, []) if isinstance(quarter, dict) else []
        if isinstance(values, list):
            announcement_count = max(announcement_count, sum(bool(value) for value in values))
    return {
        "market": "US",
        "symbol": str(obj.get("symbol", path.stem)).upper(),
        "financial_file_present": True,
        "annual_row_count": annual_count,
        "annual_first_period": annual_first,
        "annual_last_period": annual_last,
        "quarterly_row_count": quarter_count,
        "quarterly_nonempty_rows": quarter_count,
        "quarterly_first_period": quarter_first,
        "quarterly_last_period": quarter_last,
        "financial_generated_at": str(obj.get("generatedAt", "")),
        "financial_currency": str(obj.get("currency", "")),
        "announcement_timestamp_rows": announcement_count,
    }


def load_financial_inventory(
    financial_dir: str | Path | None = None,
    tw_fundamentals: str | Path | None = None,
    inventory_csv: str | Path | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    if inventory_csv and Path(inventory_csv).exists():
        manifest = pd.read_csv(inventory_csv, dtype=str).fillna("")
        for row in manifest.to_dict(orient="records"):
            market, symbol = row.get("market", ""), row.get("symbol", "")
            if not market or not symbol:
                continue
            result[(market, symbol.upper())] = {
                "market": market,
                "symbol": symbol.upper(),
                "financial_file_present": True,
                "annual_row_count": int(float(row.get("annual_count") or 0)),
                "annual_first_period": row.get("annual_first", ""),
                "annual_last_period": row.get("annual_last", ""),
                "quarterly_row_count": int(float(row.get("quarterly_count") or 0)),
                "quarterly_nonempty_rows": int(float(row.get("quarterly_nonempty") or row.get("quarterly_count") or 0)),
                "quarterly_first_period": row.get("quarterly_first", ""),
                "quarterly_last_period": row.get("quarterly_last", ""),
                "financial_generated_at": row.get("generated_at", ""),
                "financial_currency": row.get("currency", ""),
                "announcement_timestamp_rows": int(float(row.get("announcement_date_fields") or 0)),
            }
        return result

    if financial_dir:
        for path in sorted(Path(financial_dir).glob("*.json")):
            record = _us_financial_record(path)
            if record:
                result[("US", record["symbol"])] = record

    if tw_fundamentals and Path(tw_fundamentals).exists():
        try:
            obj = json.loads(Path(tw_fundamentals).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            obj = {}
        seasons = [str(value) for value in obj.get("seasons", [])]
        for symbol, values in obj.get("data", {}).items():
            if not str(symbol).isdigit() or not isinstance(values, list):
                continue
            nonempty = sum(
                1 for row in values
                if isinstance(row, list) and any(value not in (None, "") for value in row)
            )
            result[("TW", str(symbol).upper())] = {
                "market": "TW",
                "symbol": str(symbol).upper(),
                "financial_file_present": nonempty > 0,
                "annual_row_count": 0,
                "annual_first_period": "",
                "annual_last_period": "",
                "quarterly_row_count": len(values),
                "quarterly_nonempty_rows": nonempty,
                "quarterly_first_period": seasons[0] if seasons else "",
                "quarterly_last_period": seasons[-1] if seasons else "",
                "financial_generated_at": str(obj.get("generatedAt", "")),
                "financial_currency": "TWD",
                "announcement_timestamp_rows": 0,
            }
    return result


def _weekday_gap_upper_bound(index: pd.DatetimeIndex) -> int:
    """Weekday gaps include market holidays, so this is an upper bound only."""
    if len(index) < 2:
        return 0
    gaps = 0
    for before, after in zip(index[:-1], index[1:]):
        if (after - before).days <= 1:
            continue
        gaps += max(0, len(pd.bdate_range(before + pd.Timedelta(days=1), after - pd.Timedelta(days=1))))
    return gaps


def load_market_data(
    data_dir: str | Path,
    financial_dir: str | Path | None = None,
    tw_fundamentals: str | Path | None = None,
    financial_inventory_csv: str | Path | None = None,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame]:
    data_path = Path(data_dir)
    finance = load_financial_inventory(financial_dir, tw_fundamentals, financial_inventory_csv)
    frames: dict[str, pd.DataFrame] = {}
    coverage_rows: list[dict[str, Any]] = []
    issue_rows: list[dict[str, Any]] = []

    for path in sorted(data_path.glob("*.csv")):
        symbol = path.name[:-4].upper()
        market = market_for_symbol(symbol)
        instrument = instrument_for_symbol(symbol)
        try:
            raw = pd.read_csv(path)
        except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
            raw = pd.DataFrame()
            issue_rows.append({"scope": "symbol", "market": market, "symbol": symbol, "issue_code": "unreadable_csv", "severity": "high", "details": type(exc).__name__})
        frame, qa = clean_ohlcv(raw)
        if not frame.empty:
            frames[symbol] = frame
        if not frame.empty:
            close_return = frame["Close"].pct_change()
            opening_gap = frame["Open"].div(frame["Close"].shift(1)).sub(1)
            intraday_return = frame["Close"].div(frame["Open"]).sub(1)
            possible_jump = int((close_return.abs() > 0.50).sum())
            possible_gap = int((opening_gap.abs() > 0.50).sum())
            possible_intraday = int((intraday_return.abs() > 0.50).sum())
            possible_discontinuity = int((close_return.abs().gt(0.50) | opening_gap.abs().gt(0.50) | intraday_return.abs().gt(0.50)).sum())
        else:
            possible_jump = possible_gap = possible_intraday = possible_discontinuity = 0
        volume_ratio = frame["Volume"] / frame["Volume"].shift(1).rolling(20, min_periods=10).median() if not frame.empty else pd.Series(dtype=float)
        volume_anomalies = int((volume_ratio > 10).sum())
        finance_symbol = symbol.split(".", 1)[0] if market == "TW" and instrument == "equity" else symbol
        financial = finance.get((market, finance_symbol), {})
        first = frame.index.min().date().isoformat() if not frame.empty else ""
        last = frame.index.max().date().isoformat() if not frame.empty else ""
        span_years = round((frame.index.max() - frame.index.min()).days / 365.25, 2) if len(frame) > 1 else 0.0
        coverage_rows.append({
            "symbol": symbol,
            "market": market,
            "instrument_type": instrument,
            "first_date": first,
            "last_date": last,
            "ohlcv_rows": len(frame),
            "span_years": span_years,
            "weekday_gap_upper_bound": _weekday_gap_upper_bound(frame.index),
            "duplicate_dates_removed": qa["duplicate_dates"],
            "weekend_rows_removed": qa["weekend_rows"],
            "invalid_ohlc_rows_removed": qa["invalid_ohlc_rows"],
            "missing_volume_rows": qa["missing_volume_rows"],
            "possible_gt_50pct_close_jumps": possible_jump,
            "possible_gt_50pct_opening_gaps": possible_gap,
            "possible_gt_50pct_intraday_moves": possible_intraday,
            "possible_gt_50pct_price_discontinuities": possible_discontinuity,
            "volume_gt_10x_prior20_median": volume_anomalies,
            "split_adjustment_status": "unverified; Yahoo auto_adjust=False, actions=False; source CSV has no action columns",
            "dividend_adjusted": False,
            "financial_file_present": bool(financial.get("financial_file_present", False)),
            "annual_financial_rows": financial.get("annual_row_count", ""),
            "annual_first_period": financial.get("annual_first_period", ""),
            "annual_last_period": financial.get("annual_last_period", ""),
            "quarterly_financial_rows": financial.get("quarterly_nonempty_rows", financial.get("quarterly_row_count", "")),
            "quarterly_first_period": financial.get("quarterly_first_period", ""),
            "quarterly_last_period": financial.get("quarterly_last_period", ""),
            "announcement_timestamp_rows": financial.get("announcement_timestamp_rows", 0),
            "current_snapshot_file": True,
            "current_survivor_status": "unknown; file presence is not a historical or current listing-status proof",
            "historical_universe_status": "unavailable",
            "financial_generated_at": financial.get("financial_generated_at", ""),
            "financial_currency": financial.get("financial_currency", ""),
        })

        if qa["raw_rows"] == 0:
            issue_rows.append({"scope": "symbol", "market": market, "symbol": symbol, "issue_code": "empty_file", "severity": "high", "details": "CSV has no readable OHLCV rows"})
        if 0 < len(frame) < 120 and instrument == "equity":
            issue_rows.append({"scope": "symbol", "market": market, "symbol": symbol, "issue_code": "very_short_history", "severity": "high", "details": f"{len(frame)} bars; event windows are severely limited"})
        elif 0 < len(frame) < 252 and instrument == "equity":
            issue_rows.append({"scope": "symbol", "market": market, "symbol": symbol, "issue_code": "less_than_252_bars", "severity": "medium", "details": f"{len(frame)} bars; 252-session event definitions cannot be evaluated"})
        for code, count, severity in (
            ("duplicate_dates_removed", qa["duplicate_dates"], "medium"),
            ("weekend_rows_removed", qa["weekend_rows"], "medium"),
            ("invalid_ohlc_rows_removed", qa["invalid_ohlc_rows"], "high"),
            ("possible_extreme_price_discontinuity", possible_discontinuity, "high"),
            ("extreme_volume_spike", volume_anomalies, "low"),
        ):
            if count:
                issue_rows.append({"scope": "symbol", "market": market, "symbol": symbol, "issue_code": code, "severity": severity, "details": f"count={count}; inspect source before interpreting"})
        if instrument == "equity" and not financial.get("financial_file_present", False):
            issue_rows.append({"scope": "symbol", "market": market, "symbol": symbol, "issue_code": "financial_history_missing", "severity": "medium", "details": "No matching current financial history file/row"})

    coverage = pd.DataFrame(coverage_rows)
    issues = pd.DataFrame(issue_rows, columns=["scope", "market", "symbol", "issue_code", "severity", "details"])
    return frames, coverage, issues

