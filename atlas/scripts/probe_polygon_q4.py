#!/usr/bin/env python3
"""Read-only probe: does Polygon.io carry a diluted EPS value for the US Q4 rows that the repo is missing?

Offline pieces (unit-tested in tests/test_probe_polygon_q4.py): the row and key plan from the repo files, filing
matching, labels, tolerances, Wilson bounds, the checkpoint and the report. The network piece is NetworkSource, which
runs only in .github/workflows/polygon-probe.yml. --replay-dir runs the same pipeline over saved responses, offline.

Network use: one GET per distinct symbol to https://api.polygon.io/vX/reference/financials with ticker, timeframe
"quarterly" and limit 100, plus next_url when Polygon returns one. At most 5 requests per minute (one request every
12 seconds, retries included). The key is read from POLYGON_API_KEY, sent only as the apiKey query parameter to
api.polygon.io, and never printed.

Symbols: the distinct symbols of the missing rows come first. Set b (grade-A Q4 rows, quarterly.epsQuality[i].epsDiluted
== "A") also adds the symbols it covers that have no missing row. Sets a and ad (SEC Q1-Q3 rows, src == "sec"; ad is
derived == 1, a is derived != 1) are measured only on symbols that have missing rows. All sets come from
docs/report/financials (market "US").

Split policy: repo values are split-adjusted and Polygon's as-reported values are not, so a row whose period end is
before a split is flagged split_after_end. Set b is judged on the rows without a split; the split rows are reported
apart. The gate counts covered, testable rows that differ from the subtraction and have no split after the end.

Tickers: Polygon writes share classes with a dot (BRK-B is requested as BRK.B). Keys and file names keep the repo symbol.

Output: a report of counts, (symbol, end) keys and flags. Polygon values stay in memory during a run. The checkpoint
keeps flags only, so no EPS value is written to the report, the log or the checkpoint. The workflow commits the report
to the runs branch; --check-commit-paths refuses any path outside that run's directory or a protected path.

Unverified: the Polygon path and field names (vX/reference/financials, timeframe, limit, next_url, end_date,
fiscal_period, filing_date, source_filing_url, financials.income_statement.diluted_earnings_per_share.value). The
authoring environment could not reach Polygon's documentation. The run stops with SchemaMismatch at the first response
that does not carry the EPS field.

Exit codes: 0 finished, or stopped at the time budget (see report status); 1 configuration or checkpoint problem;
2 key rejected; 3 response schema mismatch; 4 rate limit persisted after retries; 5 other API error.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CLASSIFICATION = ROOT / "docs" / "report" / "q4-eps-missing-classification.json"
DEFAULT_FINANCIALS_DIR = ROOT / "docs" / "report" / "financials"

CLASSIFICATION_SCHEMA = "q4-eps-missing-classification-v1"
CHECKPOINT_SCHEMA = "polygon-q4-probe-checkpoint-v1"
REPORT_SCHEMA = "polygon-q4-probe-report-v1"
CLASS_ORDER = ("no-q4-net-income", "no-annual-diluted-eps", "q1-q3-incomplete", "derivation-rejected", "ledger-blocked")

API_HOST = "api.polygon.io"
ENDPOINT = f"https://{API_HOST}/vX/reference/financials"
TIMEFRAME = "quarterly"
LIMIT = 100
EPS_FIELD = "diluted_earnings_per_share"
PACE_SECONDS = 12.0                       # 5 requests per minute (free tier), retries included
MAX_PAGES = 5
RETRY_429_SECONDS = (60, 120, 240, 480, 960)
RETRY_5XX_SECONDS = (30, 60, 120)
NETWORK_RETRY_SECONDS = (15, 30, 60)
WINDOW_DAYS = 7
ABS_TOL = 0.011
REL_TOL = 0.005
Z95 = 1.959963984540054
GATE_THRESHOLD = 500
DEFAULT_BUDGET_MINUTES = 320
UNVERIFIED_FIELDS = [
    "GET /vX/reference/financials",
    "query: ticker, timeframe=quarterly, limit, apiKey",
    "response: results[].end_date, fiscal_period, timeframe, filing_date, source_filing_url, next_url",
    "results[].financials.income_statement.diluted_earnings_per_share.value",
]
_FORM_10K = re.compile(r"(?<![a-z0-9])10-?k(?![a-z0-9])", re.IGNORECASE)


class ProbeError(RuntimeError):
    exit_code = 1


class SchemaMismatch(ProbeError):
    exit_code = 3


class KeyRejected(ProbeError):
    exit_code = 2


class RateLimited(ProbeError):
    exit_code = 4


class ApiError(ProbeError):
    exit_code = 5


class CheckpointMismatch(ProbeError):
    exit_code = 1


# ---------------------------------------------------------------- arithmetic

def tolerance(reference):
    return max(ABS_TOL, REL_TOL * abs(reference))


def polygon_ticker(symbol):
    """Polygon writes share classes with a dot (BRK-B is BRK.B). Keys and file names keep the repo symbol."""
    return symbol.replace("-", ".")


def agrees(value, reference):
    return abs(value - reference) <= tolerance(reference)


def opposite_signs(value, reference):
    return value * reference < 0


def wilson_lower(successes, n):
    """Lower bound of the 95% Wilson score interval for successes out of n. None when n is 0."""
    if n == 0:
        return None
    p = successes / n
    z2 = Z95 * Z95
    centre = p + z2 / (2 * n)
    margin = Z95 * ((p * (1 - p) / n + z2 / (4 * n * n)) ** 0.5)
    return (centre - margin) / (1 + z2 / n)


def looks_like_10k(url):
    return bool(url) and _FORM_10K.search(url) is not None


# ---------------------------------------------------------------- Polygon responses

@dataclass(frozen=True)
class Record:
    end: date
    fiscal_period: str
    eps: float | None
    filing_date: str
    url: str


@dataclass
class ParseState:
    field_seen: bool = False


_ABSENT = object()


def parse_page(symbol, payload):
    """Return (records, skipped, field_count) for one response page. Raises SchemaMismatch on a structural mismatch."""
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise SchemaMismatch(f"{symbol}: response has no results list")
    records, skipped, field_count = [], 0, 0
    for item in payload["results"]:
        if not isinstance(item, dict):
            raise SchemaMismatch(f"{symbol}: a result is not an object")
        timeframe = item.get("timeframe")
        if timeframe is not None and timeframe != TIMEFRAME:
            skipped += 1
            continue
        for name in ("end_date", "fiscal_period"):
            if name not in item:
                raise SchemaMismatch(f"{symbol}: a result has no {name}")
        financials = item.get("financials")
        if not isinstance(financials, dict) or not isinstance(financials.get("income_statement"), dict):
            raise SchemaMismatch(f"{symbol}: a result has no financials.income_statement")
        income = financials["income_statement"]
        try:
            end = date.fromisoformat(item["end_date"])
        except (TypeError, ValueError):
            raise SchemaMismatch(f"{symbol}: end_date is not YYYY-MM-DD") from None
        if not isinstance(item["fiscal_period"], str):
            raise SchemaMismatch(f"{symbol}: fiscal_period is not a string")
        cell = income.get(EPS_FIELD, _ABSENT)
        eps = None
        if cell is not _ABSENT:
            field_count += 1
            if not isinstance(cell, dict) or "value" not in cell:
                raise SchemaMismatch(f"{symbol}: {EPS_FIELD} has no value entry")
            value = cell["value"]
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise SchemaMismatch(f"{symbol}: {EPS_FIELD}.value is not a number")
                eps = float(value)
        records.append(Record(end, item["fiscal_period"], eps,
                              str(item.get("filing_date") or ""), str(item.get("source_filing_url") or "")))
    return records, skipped, field_count


def parse_pages(symbol, pages, state):
    """Parse every page of one symbol. The first response with quarterly results must carry the EPS field."""
    records, skipped, field_count = [], 0, 0
    for payload in pages:
        page_records, page_skipped, page_fields = parse_page(symbol, payload)
        records.extend(page_records)
        skipped += page_skipped
        field_count += page_fields
    if field_count:
        state.field_seen = True
    elif records and not state.field_seen:
        raise SchemaMismatch(f"{symbol}: no quarterly result carries {EPS_FIELD}; the field name is not confirmed")
    stats = {"records": len(records), "no_eps": sum(1 for r in records if r.eps is None), "skipped": skipped}
    return records, stats


# ---------------------------------------------------------------- plan from the repo

@dataclass(frozen=True)
class Key:
    group: str                 # "m" missing Q4 row; "b" grade-A Q4; "a" SEC Q1-Q3 (derived != 1); "ad" SEC Q1-Q3 (derived == 1)
    symbol: str
    end: date
    fq: int
    fy: int | None
    cls: str | None            # missing rows only
    reference: float | None    # agreement sets only: the repo value
    subtraction: float | None  # missing rows only: repo annual - (Q1+Q2+Q3) when testable, else None
    split_after_end: bool      # repo values are split-adjusted and a split falls after this period end

    @property
    def name(self):
        return f"{self.group}|{self.fq}|{self.end.isoformat()}"


@dataclass
class Plan:
    symbols: list
    keys: dict
    missing_symbols: list
    missing_rows: int


def load_missing_rows(path):
    doc = json.loads(Path(path).read_bytes())
    if not isinstance(doc, dict) or doc.get("schema") != CLASSIFICATION_SCHEMA:
        raise ProbeError(f"{path}: not a {CLASSIFICATION_SCHEMA} file")
    rows = doc.get("rows")
    if not isinstance(rows, list):
        raise ProbeError(f"{path}: rows is not a list")
    for row in rows:
        if not isinstance(row, dict) or not {"symbol", "end", "class", "fy"} <= set(row):
            raise ProbeError(f"{path}: a row lacks symbol, end, class or fy")
    return rows


def _at(column, i):
    return column[i] if i < len(column) else None


def symbol_keys(symbol, doc, missing_rows, with_a_sets=True):
    q = doc.get("quarterly") or {}
    ends, fqs, fys = q.get("end") or [], q.get("fq") or [], q.get("fy") or []
    srcs, derived, quals = q.get("src") or [], q.get("derived") or [], q.get("epsQuality") or []
    eps = (q.get("values") or {}).get("epsDiluted") or []
    annual = doc.get("annual") or {}
    a_fys = annual.get("fy") or []
    a_eps = (annual.get("values") or {}).get("epsDiluted") or []
    split = doc.get("split") or {}
    adjusted = split.get("adjusted") is True
    events = [date.fromisoformat(e[0]) for e in (split.get("events") or [])]

    def quarter_value(fy, fq):
        idx = [i for i in range(len(fqs)) if _at(fys, i) == fy and fqs[i] == fq]
        return _at(eps, idx[0]) if len(idx) == 1 else None

    def annual_value(fy):
        idx = [i for i, f in enumerate(a_fys) if f == fy]
        return _at(a_eps, idx[0]) if len(idx) == 1 else None

    def subtraction(fy):
        a = annual_value(fy)
        qs = [quarter_value(fy, fq) for fq in (1, 2, 3)]
        if a is None or any(v is None for v in qs):
            return None
        return a - (qs[0] + qs[1] + qs[2])

    def split_after(end):
        return adjusted and any(ev > end for ev in events)

    q4_at = {}
    for i, fq in enumerate(fqs):
        if fq == 4 and i < len(ends):
            q4_at.setdefault(ends[i], []).append(i)

    keys = []
    for row in missing_rows:
        if len(q4_at.get(row["end"], [])) != 1:
            raise ProbeError(f"{symbol}: missing row {row['end']} is not exactly one Q4 index in its file")
        end = date.fromisoformat(row["end"])
        keys.append(Key("m", symbol, end, 4, row["fy"], row["class"], None,
                        subtraction(row["fy"]), split_after(end)))
    for i in range(min(len(fqs), len(ends))):
        end = date.fromisoformat(ends[i])
        fq = fqs[i]
        value = _at(eps, i)
        if with_a_sets and fq in (1, 2, 3) and _at(srcs, i) == "sec" and value is not None:
            group = "ad" if _at(derived, i) == 1 else "a"
            keys.append(Key(group, symbol, end, fq, _at(fys, i), None, value, None, split_after(end)))
        qual = _at(quals, i)
        if fq == 4 and value is not None and isinstance(qual, dict) and qual.get("epsDiluted") == "A":
            keys.append(Key("b", symbol, end, 4, _at(fys, i), None, value, None, split_after(end)))
    return keys


def build_plan(classification_path, financials_dir):
    rows = load_missing_rows(classification_path)
    missing = {}
    for row in rows:
        missing.setdefault(row["symbol"], []).append(row)
    keys = {}
    for path in sorted(Path(financials_dir).glob("*.json")):
        doc = json.loads(path.read_bytes())
        if not isinstance(doc, dict) or doc.get("market") != "US":
            continue
        symbol = path.stem
        if doc.get("symbol") != symbol:
            raise ProbeError(f"{path.name}: symbol field differs from the file name")
        found = symbol_keys(symbol, doc, missing.get(symbol, []), with_a_sets=symbol in missing)
        names = [k.name for k in found]
        if len(set(names)) != len(names):
            raise ProbeError(f"{symbol}: two keys share a group, quarter and end")
        if found:
            keys[symbol] = found
    absent = sorted(s for s in missing if s not in keys)
    if absent:
        raise ProbeError(f"{len(absent)} symbols with missing rows have no US financials file")
    missing_symbols = sorted(missing)
    others = sorted(s for s in keys if s not in missing)
    return Plan(symbols=missing_symbols + others, keys=keys,
                missing_symbols=missing_symbols, missing_rows=len(rows))


def fingerprint(classification_path, financials_dir):
    digest = hashlib.sha256()
    digest.update(b"classification\n")
    digest.update(Path(classification_path).read_bytes())
    for path in sorted(Path(financials_dir).glob("*.json")):
        digest.update(path.name.encode("utf-8") + b"\n")
        digest.update(path.read_bytes())
    return digest.hexdigest()


# ---------------------------------------------------------------- matching and flags

def select(candidates):
    """Pick one value for one period. Returns (status, record); status is ok, no_record, no_value or ambiguous."""
    if not candidates:
        return "no_record", None
    valued = [r for r in candidates if r.eps is not None]
    if not valued:
        return "no_value", None
    first = valued[0]
    if any(not agrees(r.eps, first.eps) for r in valued):
        return "ambiguous", None
    best = max(valued, key=lambda r: (r.filing_date, r.url))
    return "ok", best


def evaluate_symbol(keys, records):
    """Flags per key. Values are read here and then dropped; only flags leave this function."""
    oldest = min((r.end for r in records), default=None)
    flags = {}
    for key in keys:
        window = [r for r in records
                  if r.fiscal_period == f"Q{key.fq}" and abs((r.end - key.end).days) <= WINDOW_DAYS]
        status, best = select(window)
        entry = {"status": status}
        if status != "ok":
            entry["before_window"] = oldest is not None and (oldest - key.end).days > WINDOW_DAYS
        elif key.group == "m":
            equal = agrees(best.eps, key.subtraction) if key.subtraction is not None else None
            by_arithmetic = bool(equal)
            by_form = looks_like_10k(best.url)
            entry.update(label="derived" if (by_arithmetic or by_form) else "reported",
                         derived_arithmetic=by_arithmetic, derived_form=by_form, equal_subtraction=equal)
        else:
            entry.update(agree=agrees(best.eps, key.reference),
                         sign_error=opposite_signs(best.eps, key.reference))
        flags[key.name] = entry
    return flags


# ---------------------------------------------------------------- sources

class Pacer:
    def __init__(self, interval, clock=time.monotonic, sleep=time.sleep):
        self.interval = interval
        self._clock = clock
        self._sleep = sleep
        self._last = None

    def wait(self):
        if self._last is not None:
            gap = self.interval - (self._clock() - self._last)
            if gap > 0:
                self._sleep(gap)
        self._last = self._clock()


def _retry_after(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


class NetworkSource:
    """Polygon REST client. Never prints the key or a URL: error text names the symbol and the HTTP status only."""

    def __init__(self, api_key, pacer, opener=urllib.request.urlopen, sleep=time.sleep):
        self._key = api_key
        self._pacer = pacer
        self._opener = opener
        self._sleep = sleep
        self.calls = 0
        self.retries = 0

    def _request(self, url, symbol):
        attempts_429 = attempts_5xx = attempts_net = 0
        while True:
            self._pacer.wait()
            self.calls += 1
            request = urllib.request.Request(url, headers={"Accept": "application/json",
                                                           "User-Agent": "atlas-q4-probe/1"})
            body, status, retry_after = None, None, None
            try:
                with self._opener(request, timeout=60) as response:
                    status = response.status
                    body = response.read()
            except urllib.error.HTTPError as err:
                status = err.code
                retry_after = err.headers.get("Retry-After") if err.headers is not None else None
            except (urllib.error.URLError, OSError) as err:
                if attempts_net >= len(NETWORK_RETRY_SECONDS):
                    raise ApiError(f"{symbol}: network error {type(err).__name__} after retries") from None
                self._sleep(NETWORK_RETRY_SECONDS[attempts_net])
                attempts_net += 1
                self.retries += 1
                continue
            if status == 200 and body is not None:
                try:
                    return json.loads(body)
                except ValueError:
                    raise ApiError(f"{symbol}: response body is not JSON") from None
            if status in (401, 403):
                raise KeyRejected(f"Polygon answered HTTP {status} for {symbol}: the key was rejected or the plan "
                                  f"does not include this endpoint. Stopped; nothing was retried.")
            if status == 429:
                if attempts_429 >= len(RETRY_429_SECONDS):
                    raise RateLimited(f"{symbol}: still rate limited (HTTP 429) after {attempts_429} retries")
                wait = _retry_after(retry_after)
                self._sleep(wait if wait is not None else RETRY_429_SECONDS[attempts_429])
                attempts_429 += 1
                self.retries += 1
                continue
            if status is not None and 500 <= status < 600:
                if attempts_5xx >= len(RETRY_5XX_SECONDS):
                    raise ApiError(f"{symbol}: HTTP {status} after {attempts_5xx} retries")
                self._sleep(RETRY_5XX_SECONDS[attempts_5xx])
                attempts_5xx += 1
                self.retries += 1
                continue
            raise ApiError(f"{symbol}: HTTP {status}")

    def _next_url(self, next_url, symbol):
        parts = urllib.parse.urlsplit(next_url)
        if parts.scheme != "https" or parts.netloc != API_HOST:
            raise SchemaMismatch(f"{symbol}: next_url points outside {API_HOST}; not followed")
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not any(name == "apiKey" for name, _ in query):
            query.append(("apiKey", self._key))
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path,
                                        urllib.parse.urlencode(query), ""))

    def fetch(self, symbol):
        url = ENDPOINT + "?" + urllib.parse.urlencode({"ticker": polygon_ticker(symbol), "timeframe": TIMEFRAME,
                                                       "limit": LIMIT, "apiKey": self._key})
        pages = []
        for _ in range(MAX_PAGES):
            payload = self._request(url, symbol)
            pages.append(payload)
            next_url = payload.get("next_url") if isinstance(payload, dict) else None
            if not next_url:
                return pages
            url = self._next_url(next_url, symbol)
        raise ApiError(f"{symbol}: more than {MAX_PAGES} pages")


class ReplaySource:
    """Offline stand-in for NetworkSource: reads <dir>/<symbol>.json, one page or a list of pages."""

    def __init__(self, directory):
        self._dir = Path(directory)
        self.calls = 0
        self.retries = 0

    def fetch(self, symbol):
        self.calls += 1
        path = self._dir / f"{symbol}.json"
        if not path.exists():
            return [{"results": []}]
        data = json.loads(path.read_bytes())
        return data if isinstance(data, list) else [data]


# ---------------------------------------------------------------- checkpoint (flags only, JSON lines)

def _line(obj):
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


class Checkpoint:
    def __init__(self, path, input_sha, fresh=False):
        self.path = Path(path)
        self.input_sha = input_sha
        self.symbols = {}
        if fresh and self.path.exists():
            self.path.unlink()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() or self.path.stat().st_size == 0:
            self.path.write_bytes(_line({"schema": CHECKPOINT_SCHEMA, "input": input_sha}))
            return
        self._load()

    def _load(self):
        data = self.path.read_bytes()
        header_seen = False
        good_end = 0
        lines = data.splitlines(keepends=True)
        for position, chunk in enumerate(lines):
            is_last = position == len(lines) - 1
            if not chunk.endswith(b"\n"):
                break
            try:
                obj = json.loads(chunk)
            except ValueError:
                if is_last:
                    break
                raise CheckpointMismatch(f"{self.path.name}: corrupt line before the end; use --fresh") from None
            if not header_seen:
                if not isinstance(obj, dict) or obj.get("schema") != CHECKPOINT_SCHEMA:
                    raise CheckpointMismatch(f"{self.path.name}: not a probe checkpoint; use --fresh")
                if obj.get("input") != self.input_sha:
                    raise CheckpointMismatch("the checkpoint was made from different input files; "
                                             "rerun with --fresh to start over")
                header_seen = True
            else:
                symbol = obj.get("symbol")
                if symbol in self.symbols:
                    raise CheckpointMismatch(f"{self.path.name}: a symbol is recorded twice; use --fresh")
                self.symbols[symbol] = obj
            good_end += len(chunk)
        if not header_seen:
            self.path.write_bytes(_line({"schema": CHECKPOINT_SCHEMA, "input": self.input_sha}))
            self.symbols = {}
            return
        if good_end < len(data):
            with open(self.path, "r+b") as handle:
                handle.truncate(good_end)

    def append(self, record):
        with open(self.path, "ab") as handle:
            handle.write(_line(record))
            handle.flush()
            os.fsync(handle.fileno())
        self.symbols[record["symbol"]] = record


# ---------------------------------------------------------------- report

def _new_measure(rows_in_set):
    return {"rows_in_set": rows_in_set, "run": 0, "compared": 0, "agreements": 0, "disagreements": 0,
            "sign_errors": 0, "split_after_end_compared": 0, "split_after_end_disagreements": 0}


def _finish_measure(m):
    out = dict(m)
    out["uncovered"] = m["run"] - m["compared"]
    out["wilson_lower_95"] = None if m["compared"] == 0 else round(wilson_lower(m["agreements"], m["compared"]), 6)
    return out


def aggregate(plan, done, input_sha, status, stop_reason):
    planned = Counter()
    planned_by_class, planned_by_fy, testable_planned_by_class = Counter(), Counter(), Counter()
    planned_b_split = 0
    for symbol in plan.symbols:
        for key in plan.keys[symbol]:
            planned[key.group] += 1
            if key.group == "m":
                planned_by_class[key.cls] += 1
                planned_by_fy[str(key.fy)] += 1
                if key.subtraction is not None:
                    testable_planned_by_class[key.cls] += 1
            elif key.group == "b" and key.split_after_end:
                planned_b_split += 1
    run_by_class, covered_by_class = Counter(), Counter()
    reported_by_class, derived_by_class, covered_by_fy = Counter(), Counter(), Counter()
    status_counts, testable_covered_by_class, equal_by_class = Counter(), Counter(), Counter()
    derived_arithmetic = derived_form = before_window = 0
    testable_covered = equal = covered_not_testable = 0
    not_equal_split_rows = 0
    measures = {"b": _new_measure(planned["b"] - planned_b_split), "bs": _new_measure(planned_b_split),
                "a": _new_measure(planned["a"]), "ad": _new_measure(planned["ad"])}
    rows = []
    symbols_done = no_data = records = no_eps = skipped = 0
    for symbol in plan.symbols:
        if symbol not in done:
            continue
        symbols_done += 1
        stats = done[symbol]
        records += stats["records"]
        no_eps += stats["no_eps"]
        skipped += stats["skipped"]
        if stats["records"] == 0:
            no_data += 1
        for key in plan.keys[symbol]:
            flags = stats["keys"].get(key.name)
            if flags is None:
                raise CheckpointMismatch(f"{symbol}: the checkpoint has no flags for {key.name}")
            covered = flags["status"] == "ok"
            if key.group == "m":
                run_by_class[key.cls] += 1
                status_counts[flags["status"]] += 1
                before_window += bool(flags.get("before_window"))
                rows.append({"symbol": symbol, "end": key.end.isoformat(), "class": key.cls,
                             "status": flags["status"], "label": flags.get("label"),
                             "equal_subtraction": flags.get("equal_subtraction")})
                if not covered:
                    continue
                covered_by_class[key.cls] += 1
                covered_by_fy[str(key.fy)] += 1
                if flags["label"] == "derived":
                    derived_by_class[key.cls] += 1
                else:
                    reported_by_class[key.cls] += 1
                derived_arithmetic += bool(flags["derived_arithmetic"])
                derived_form += bool(flags["derived_form"])
                if key.subtraction is None:
                    covered_not_testable += 1
                else:
                    testable_covered += 1
                    testable_covered_by_class[key.cls] += 1
                    if flags["equal_subtraction"]:
                        equal += 1
                        equal_by_class[key.cls] += 1
                    elif key.split_after_end:
                        not_equal_split_rows += 1
                continue
            name = ("bs" if key.split_after_end else "b") if key.group == "b" else key.group
            measure = measures[name]
            measure["run"] += 1
            if not covered:
                continue
            measure["compared"] += 1
            if flags["agree"]:
                measure["agreements"] += 1
            else:
                measure["disagreements"] += 1
            measure["sign_errors"] += bool(flags["sign_error"])
            if key.split_after_end:
                measure["split_after_end_compared"] += 1
                if not flags["agree"]:
                    measure["split_after_end_disagreements"] += 1
    rows.sort(key=lambda r: (r["symbol"], r["end"]))
    run_total = sum(run_by_class.values())
    not_equal_all = testable_covered - equal
    gate_count = not_equal_all - not_equal_split_rows
    return {
        "schema": REPORT_SCHEMA,
        "status": status,
        "stop_reason": stop_reason,
        "input_sha256": input_sha,
        "docs_verified": False,
        "fields_unverified": UNVERIFIED_FIELDS,
        "symbols": {
            "planned": len(plan.symbols),
            "planned_with_missing_rows": len(plan.missing_symbols),
            "agreement_only": len(plan.symbols) - len(plan.missing_symbols),
            "done": symbols_done,
            "no_data": no_data,
            "hyphen_mapped_to_dot": sum(1 for s in plan.symbols if polygon_ticker(s) != s),
        },
        "records": {"returned_quarterly": records, "without_eps": no_eps, "non_quarterly_skipped": skipped},
        "missing_rows": {
            "total": planned["m"],
            "run": run_total,
            "covered": sum(covered_by_class.values()),
            "not_covered_by_status": {name: status_counts[name] for name in ("no_record", "no_value", "ambiguous")},
            "covered_reported": sum(reported_by_class.values()),
            "covered_derived": sum(derived_by_class.values()),
            "derived_by_arithmetic": derived_arithmetic,
            "derived_by_form_url": derived_form,
            "derived_form_url_test": "unverified: source_filing_url format not confirmed",
            "rows_before_response_window": before_window,
            "by_class": {c: {"total": planned_by_class[c], "run": run_by_class[c],
                             "covered": covered_by_class[c], "reported": reported_by_class[c],
                             "derived": derived_by_class[c]} for c in CLASS_ORDER},
            "by_fiscal_year": {fy: {"total": planned_by_fy[fy], "covered": covered_by_fy[fy]}
                               for fy in sorted(planned_by_fy)},
        },
        "subtraction": {
            "testable_total": sum(testable_planned_by_class.values()),
            "not_testable_total": planned["m"] - sum(testable_planned_by_class.values()),
            "testable_covered": testable_covered,
            "equal_to_subtraction": equal,
            "covered_not_equal_subtraction": not_equal_all,
            "covered_not_equal_subtraction_split_after_end": not_equal_split_rows,
            "covered_not_equal_subtraction_no_split_after_end": gate_count,
            "covered_not_testable": covered_not_testable,
            "by_class": {c: {"testable_total": testable_planned_by_class[c],
                             "not_testable": planned_by_class[c] - testable_planned_by_class[c],
                             "testable_covered": testable_covered_by_class[c],
                             "equal": equal_by_class[c]} for c in CLASS_ORDER},
        },
        "agreement": {
            "b_grade_a_q4_no_split_after_end": _finish_measure(measures["b"]),
            "b_grade_a_q4_split_after_end": _finish_measure(measures["bs"]),
            "a_sec_q1_q3_not_derived": _finish_measure(measures["a"]),
            "a_sec_q1_q3_derived_subset": _finish_measure(measures["ad"]),
        },
        "gate": {"threshold": GATE_THRESHOLD, "covered_not_equal_subtraction_no_split_after_end": gate_count,
                 "met": gate_count >= GATE_THRESHOLD, "complete": status == "complete"},
        "rows": rows,
    }


PROTECTED_PREFIXES = ("docs/report/financials/", "docs/report/fundamentals/", "docs/report/q4-8k/",
                      ".github/", "atlas/", "tests/", "supabase/")
PROTECTED_NAMES = re.compile(r"(^|/)(growth\.json|answer-set\.json|q4-eps-[^/]*\.json)$")


def refused_commit_paths(paths, dest):
    """Paths that must not be committed: anything outside dest/, anything with a parent reference, and any protected
    path. Returns the refused paths in input order; an empty list means the commit may proceed."""
    refused = []
    for path in paths:
        if not path:
            continue
        inside = path.startswith(dest.rstrip("/") + "/") and ".." not in path.split("/")
        protected = any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES) or PROTECTED_NAMES.search(path)
        if not inside or protected:
            refused.append(path)
    return refused


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- run

class RunLog:
    def __init__(self, path, echo=False):
        self.path = Path(path) if path else None
        self.echo = echo
        self.lines = []

    def add(self, message):
        self.lines.append(message)
        if self.echo:
            print(message, flush=True)

    def write(self):
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8", newline="\n")


def run_probe(*, classification, financials_dir, checkpoint_path, report_path, log, source,
              budget_seconds, fresh, clock=time.monotonic):
    started = clock()
    input_sha = fingerprint(classification, financials_dir)
    plan = build_plan(classification, financials_dir)
    planned = Counter(k.group for s in plan.symbols for k in plan.keys[s])
    log.add(f"plan: {len(plan.symbols)} symbols ({len(plan.missing_symbols)} with missing rows, listed first); "
            f"keys: missing={planned['m']} b={planned['b']} a={planned['a']} ad={planned['ad']}")
    checkpoint = Checkpoint(checkpoint_path, input_sha, fresh=fresh)
    log.add(f"checkpoint: {len(checkpoint.symbols)} symbols already done")
    state = ParseState()
    stop_reason = None
    status = "partial"
    fetched = 0
    try:
        for symbol in plan.symbols:
            if symbol in checkpoint.symbols:
                continue
            if clock() - started + PACE_SECONDS > budget_seconds:
                stop_reason = "time budget reached; resume with another dispatch"
                break
            pages = source.fetch(symbol)
            records, stats = parse_pages(symbol, pages, state)
            checkpoint.append({"symbol": symbol, "status": "ok" if records else "no_data",
                               "records": stats["records"], "no_eps": stats["no_eps"],
                               "skipped": stats["skipped"],
                               "keys": evaluate_symbol(plan.keys[symbol], records)})
            fetched += 1
    except ProbeError as err:
        stop_reason = f"{type(err).__name__}: {err}"
        raise
    finally:
        status = "complete" if all(s in checkpoint.symbols for s in plan.symbols) else "partial"
        report = aggregate(plan, checkpoint.symbols, input_sha, status, stop_reason)
        write_report(report_path, report)
        log.add(f"symbols fetched this run: {fetched}; requests: {source.calls}; retries: {source.retries}")
        log.add(f"report: status {status}; gate met: {report['gate']['met']}")
        if stop_reason:
            log.add(f"stopped: {stop_reason}")
    return 0


def _scrub(text, secret):
    return text.replace(secret, "<redacted>") if secret else text


def main(argv=None):
    parser = argparse.ArgumentParser(description="Polygon.io Q4 EPS probe (read-only).")
    parser.add_argument("--check-commit-paths", metavar="DEST", default=None,
                        help="read staged paths on stdin; exit 1 if any is outside DEST/ or protected")
    default_tmp = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()) / "polygon-probe"
    parser.add_argument("--classification", default=str(DEFAULT_CLASSIFICATION))
    parser.add_argument("--financials-dir", default=str(DEFAULT_FINANCIALS_DIR))
    parser.add_argument("--checkpoint", default=str(default_tmp / "checkpoint.jsonl"))
    parser.add_argument("--report", default=str(default_tmp / "report.json"))
    parser.add_argument("--log", default=str(default_tmp / "run.log"))
    parser.add_argument("--budget-minutes", type=float, default=DEFAULT_BUDGET_MINUTES)
    parser.add_argument("--fresh", action="store_true", help="ignore and replace an existing checkpoint")
    parser.add_argument("--replay-dir", default=None, help="offline: read saved responses instead of calling Polygon")
    args = parser.parse_args(argv)
    if args.check_commit_paths is not None:
        paths = [line.strip() for line in sys.stdin if line.strip()]
        refused = refused_commit_paths(paths, args.check_commit_paths)
        for path in refused:
            print(f"refused: {path}", file=sys.stderr)
        return 1 if refused else 0

    log = RunLog(args.log, echo=True)
    secret = ""
    code = 0
    try:
        if args.replay_dir:
            source = ReplaySource(args.replay_dir)
        else:
            secret = os.environ.get("POLYGON_API_KEY", "").strip()
            if not secret:
                raise ProbeError("POLYGON_API_KEY is empty; nothing was called")
            source = NetworkSource(secret, Pacer(PACE_SECONDS))
        code = run_probe(classification=args.classification, financials_dir=args.financials_dir,
                         checkpoint_path=args.checkpoint, report_path=args.report, log=log, source=source,
                         budget_seconds=args.budget_minutes * 60, fresh=args.fresh)
    except ProbeError as err:
        log.add(f"error: {_scrub(f'{type(err).__name__}: {err}', secret)}")
        code = err.exit_code
    finally:
        log.write()
    return code


if __name__ == "__main__":
    sys.exit(main())
