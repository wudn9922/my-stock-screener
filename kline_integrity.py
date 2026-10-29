"""Shared K-line acceptance, quarantine and append-only correction history.

Permanent CSVs retain every existing date, including quarantined evidence. Readers
use usable_frame(); writers validate fresh inputs before merging with retained rows.
"""
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
import csv
import io
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import pandas as pd

from scripts.kline_journal import dump_journal, fact, merge_logs, prune_main_journal

ROOT = Path(__file__).resolve().parent
LOG_PATH = Path(os.environ.get("KLINE_CORRECTIONS_LOG", ROOT / "docs/report/kline-corrections.json"))
FIELDS = ["Open", "High", "Low", "Close", "Volume"]


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def _json_value(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int, float)) or hasattr(value, "item"):
        value = float(value)
        return value if math.isfinite(value) else None
    return str(value)


def bar_values(row):
    return {key: _json_value(row.get(key)) for key in FIELDS}


def violation(row):
    try:
        o, h, l, c = (float(row[k]) for k in FIELDS[:4])
    except (KeyError, TypeError, ValueError):
        return "missing-or-nonnumeric-OHLC"
    if not all(math.isfinite(v) and v > 0 for v in (o, h, l, c)):
        return "nonpositive-or-nonfinite-price"
    if not l <= min(o, c) <= max(o, c) <= h:
        return "invalid-OHLC-bounds"
    volume = row.get("Volume")
    if volume is not None and not pd.isna(volume):
        try:
            if not math.isfinite(float(volume)) or float(volume) < 0:
                return "invalid-volume"
        except (ValueError, TypeError):
            return "invalid-volume"
    return None


@lru_cache(maxsize=8)
def _load_log(path, mtime):
    if mtime is None:
        return {"version": 1, "quarantined": [], "events": []}
    data = json.loads(Path(path).read_text())
    if data.get("version") != 1 or not isinstance(data.get("quarantined"), list) or not isinstance(data.get("events"), list):
        raise ValueError("Invalid K-line correction log; refusing to ignore quarantine")
    return data


# While batched_journal() is active: path -> _Journal held in memory, written once.
_batch = None


class _Journal:
    """A loaded journal plus the indexes log_event needs per call."""
    def __init__(self, data):
        self.data, self.dirty = data, False
        self.ids = {e["id"] for e in data["events"]}
        self.last = {(e["symbol"], e["date"]): e for e in data["events"]}


def read_log(path=None):
    path = Path(path or LOG_PATH)
    if _batch is not None and str(path) in _batch:
        return _batch[str(path)].data
    return _load_log(str(path), path.stat().st_mtime_ns if path.exists() else None)


def write_log(path, data):
    """Every main-journal writer: retention/dedup (kline_journal) and the line-per-event format."""
    _atomic(path, dump_journal(prune_main_journal(data)))
    _load_log.cache_clear()


@contextmanager
def batched_journal():
    """Hold correction-journal writes in memory for one run and write each file once.

    Without it, every event rewrote the whole journal. Call flush_journal() before
    anything outside this process (git add, a subprocess) reads the file.
    """
    global _batch
    if _batch is not None:
        yield
        return
    _batch = {}
    try:
        yield
    finally:
        try:
            flush_journal()
        finally:
            _batch = None


def flush_journal():
    for path, journal in list((_batch or {}).items()):
        if journal.dirty:
            write_log(Path(path), journal.data)
        del _batch[path]


def quarantined_dates(symbol, log_path=None):
    return {row["date"] for row in read_log(log_path)["quarantined"] if row["symbol"] == symbol}


def log_event(symbol, day, status, old=None, new=None, source_url=None,
              fetched_at=None, reason=None, log_path=None):
    if status == "deferred":
        # An unfinished session is simply not written yet; recording each sighting
        # was a quarter of the journal and is never read.
        return
    path = Path(log_path or LOG_PATH)
    if _batch is not None and str(path) in _batch:
        journal = _batch[str(path)]
    else:
        journal = _Journal(json.loads(json.dumps(read_log(path))))
    data = journal.data
    event = dict(symbol=symbol, date=str(day)[:10], status=status, old=old, new=new,
                 sourceUrl=source_url, fetchedAt=fetched_at, reason=reason)
    identity = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
    key = (symbol, event["date"])
    # Retries/no-op replays must not manufacture correction events; neither may a
    # later fetch reporting the same bad or revised bar as the previous event.
    if identity in journal.ids or (key in journal.last and fact(journal.last[key]) == fact(event)):
        return
    event.update(id=identity, loggedAt=utcnow())
    data["events"].append(event)
    journal.ids.add(identity)
    journal.last[key] = event
    if status == "quarantined" and not any((q["symbol"], q["date"]) == key for q in data["quarantined"]):
        data["quarantined"].append(dict(symbol=symbol, date=event["date"], reason=reason))
        data["quarantined"].sort(key=lambda q: (q["symbol"], q["date"]))
    elif status == "corrected":
        data["quarantined"] = [q for q in data["quarantined"] if (q["symbol"], q["date"]) != key]
    if _batch is not None:
        journal.dirty = True
        _batch[str(path)] = journal
        return
    write_log(path, data)


def _atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".kline-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as file:
            file.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def usable_frame(frame, symbol=None, log_path=None):
    """Computation-only view. Never write this view over a permanent record directly."""
    if frame is None or frame.empty:
        return frame
    symbol = symbol or frame.attrs.get("symbol")
    frame = frame.copy()
    mask = pd.Series(True, index=frame.index)
    if all(k in frame for k in FIELDS[:4]):
        p = frame[FIELDS[:4]].apply(pd.to_numeric, errors="coerce")
        mask &= p.notna().all(axis=1) & (p > 0).all(axis=1) & (p < math.inf).all(axis=1)
        mask &= (p.Low <= p[["Open", "Close"]].min(axis=1)) & (p.High >= p[["Open", "Close"]].max(axis=1))
    if symbol:
        excluded = quarantined_dates(symbol, log_path)
        mask &= ~pd.Index(frame.index).map(lambda d: str(d)[:10]).isin(excluded)
        frame.attrs["symbol"] = symbol
    return frame.loc[mask]


def read_csv(path, **kwargs):
    kwargs.setdefault("float_precision", "round_trip")
    frame = pd.read_csv(path, **kwargs)
    frame.attrs["symbol"] = Path(path).stem
    return usable_frame(frame, Path(path).stem)


def session_complete(day, symbol, now=None, zone=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Session validation requires a timezone-aware clock")
    tw = symbol.endswith((".TW", ".TWO")) or symbol in ("^TWII", "^TWOII")
    local = now.astimezone(ZoneInfo(zone or ("Asia/Taipei" if tw else "America/New_York")))
    day = pd.Timestamp(day).date()
    if day < local.date():
        return True
    if day > local.date():
        return False
    # FX/futures daily sessions can span midnight; only a previous source date is
    # accepted. Equal volumes on adjacent dates are not evidence of a partial bar.
    if zone or symbol.startswith(("CMD-", "FX-")):
        return False
    return local.weekday() < 5 and local.time() >= (time(14, 45) if tw else time(16, 30))


@lru_cache(maxsize=16)
def _official_payload(market, day):
    if market == "TW":
        url = "https://www.twse.com.tw/exchangeReport/MI_INDEX?" + urllib.parse.urlencode(
            dict(response="json", date=day.replace("-", ""), type="ALLBUT0999"))
    else:
        url = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?" + urllib.parse.urlencode(
            dict(date=day.replace("-", "/"), id="", response="json"))
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    return payload, url, utcnow()


def official_bar(symbol, day):
    """Dated whole-market source; one cached request per market/date, exact identity."""
    market = "TWO" if symbol.endswith(".TWO") else "TW" if symbol.endswith(".TW") else None
    if market is None:
        return None
    payload, url, fetched = _official_payload(market, day)
    reported = str(payload.get("date", "")).replace("/", "").replace("-", "")
    expected = day.replace("-", "")
    if len(reported) == 7:
        reported = str(int(reported[:3]) + 1911) + reported[3:]
    if reported != expected:
        raise ValueError("Official quote report date mismatch")
    names = ["證券代號", "開盤價", "最高價", "最低價", "收盤價", "成交股數"] if market == "TW" else ["代號", "開盤", "最高", "最低", "收盤", "成交股數"]
    for table in payload.get("tables", []):
        fields = table.get("fields", [])
        if not all(k in fields for k in names):
            continue
        for values in table.get("data", []):
            row = dict(zip(fields, values))
            if str(row[names[0]]).strip() == symbol.split(".")[0]:
                bar = {f: float(str(row[name]).replace(",", "")) for f, name in zip(FIELDS, names[1:])}
                if violation(bar):
                    raise ValueError("Official quote also has invalid OHLC")
                return bar, url, fetched
    return None


def accept_frame(frame, symbol, *, refetch=None, official=official_bar, source_url=None,
                 fetched_at=None, now=None, zone=None, log_path=None):
    """Validate raw new inputs; refetch the latest invalid session at most once.

    A known quarantine is never silently released by an unrelated new download.
    Explicit, verifiable repairs use correct_bar() instead.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(columns=FIELDS)
    frame = frame.copy()
    frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    blocked = quarantined_dates(symbol, log_path)
    completed = [d for d in frame.index if session_complete(d, symbol, now, zone)]
    latest_completed = completed[-1] if completed else None
    retry = None
    rows = []
    for stamp, row in frame.iterrows():
        day = stamp.date().isoformat()
        old = bar_values(row)
        if day in blocked:
            continue
        if not session_complete(day, symbol, now, zone):
            log_event(symbol, day, "deferred", old=old, source_url=source_url,
                      fetched_at=fetched_at, reason="unfinished-session", log_path=log_path)
            continue
        reason = violation(row)
        if reason:
            candidate = None
            if stamp == latest_completed:
                if refetch:
                    try:
                        response = refetch()
                        retry, retry_url, retry_at = response if isinstance(response, tuple) else (response, source_url, utcnow())
                        retry.index = pd.to_datetime(retry.index).tz_localize(None).normalize()
                        if stamp in retry.index and not violation(retry.loc[stamp]):
                            candidate = (bar_values(retry.loc[stamp]), retry_url, retry_at)
                    except Exception as exc:
                        reason += "; refetch:" + type(exc).__name__
                if candidate is None and official and symbol.endswith((".TW", ".TWO")):
                    try:
                        candidate = official(symbol, day)
                    except Exception as exc:
                        reason += "; official:" + type(exc).__name__
            if candidate is None:
                log_event(symbol, day, "rejected", old=old, source_url=source_url,
                          fetched_at=fetched_at, reason=reason, log_path=log_path)
                continue
            values, url, fetched = candidate
            log_event(symbol, day, "corrected", old=old, new=values, source_url=url,
                      fetched_at=fetched, reason="invalid-source-bar-replaced", log_path=log_path)
            row = pd.Series(values)
        rows.append((stamp, row))
    result = pd.DataFrame([r for _, r in rows], index=[d for d, _ in rows], columns=frame.columns)
    result.index.name = "Date"
    result.attrs["symbol"] = symbol
    return result


def write_csv(path, frame, *, symbol=None, source_url=None, fetched_at=None, log_path=None, **kwargs):
    """Permanent union merge, validated writes, atomic file and correction audit."""
    path = Path(path)
    symbol = symbol or path.stem
    accepted = accept_frame(frame[FIELDS], symbol, source_url=source_url,
                            fetched_at=fetched_at, log_path=log_path, **kwargs)
    retained = pd.read_csv(path, index_col=0, parse_dates=True, float_precision="round_trip") if path.exists() else pd.DataFrame(columns=FIELDS)
    for day, row in accepted.iterrows():
        if day in retained.index:
            old, new = bar_values(retained.loc[day]), bar_values(row)
            if old != new:
                log_event(symbol, day.date().isoformat(), "corrected", old=old, new=new,
                          source_url=source_url, fetched_at=fetched_at or utcnow(),
                          reason="source-refresh", log_path=log_path)
    merged = pd.concat([retained, accepted])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    merged.index.name = "Date"
    # Existing invalid rows are evidence, never silently dropped on a filtered rewrite.
    for day, row in retained.iterrows():
        if violation(row) and day not in accepted.index:
            log_event(symbol, day.date().isoformat(), "quarantined", old=bar_values(row),
                      reason=violation(row), log_path=log_path)
    _write_preserving_lines(path, accepted)
    return usable_frame(merged, symbol, log_path)


def _write_preserving_lines(path, updates):
    """Preserve unmodified permanent rows byte-for-byte, including source precision."""
    path = Path(path)
    lines = path.read_text().splitlines(keepends=True) if path.exists() else ["Date," + ",".join(FIELDS) + "\n"]
    records = {}
    for line in lines[1:]:
        day = next(csv.reader([line]))[0]
        if day in records:
            raise ValueError("Refusing to collapse duplicate permanent records")
        records[day] = line
    for stamp, row in updates.iterrows():
        day = stamp.date().isoformat()
        old = records.get(day)
        if old is not None:
            values = next(csv.reader([old]))
            if bar_values(dict(zip(FIELDS, map(lambda v: float(v) if v else None, values[1:])))) == bar_values(row):
                continue
        buffer = io.StringIO(newline="")
        csv.writer(buffer, lineterminator="\n").writerow([day] + [bar_values(row)[f] for f in FIELDS])
        records[day] = buffer.getvalue()
    _atomic(path, lines[0] + "".join(records[d] for d in sorted(records)))


def correct_bar(path, symbol, day, values, source_url, fetched_at, log_path=None):
    if violation(values):
        raise ValueError("Refusing an invalid replacement")
    frame = pd.read_csv(path, index_col=0, parse_dates=True, float_precision="round_trip")
    target = pd.Timestamp(day)
    if target not in frame.index:
        raise ValueError("Correction must target an existing date")
    old = bar_values(frame.loc[target])
    new = bar_values(values)
    if old != new:
        log_event(symbol, day, "corrected", old=old, new=new, source_url=source_url,
                  fetched_at=fetched_at, reason="verified-official-correction", log_path=log_path)
        for field in FIELDS:
            frame.loc[target, field] = values[field]
        _write_preserving_lines(path, frame.loc[[target]])
