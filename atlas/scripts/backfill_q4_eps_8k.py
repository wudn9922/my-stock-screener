#!/usr/bin/env python3
"""
Q4 EPS from the 8-K earnings release (EX-99.1) of US issuers, for the Q4 periods that have none.

A table is used only when it agrees with what the file already holds for that quarter:
  - exactly one column shows the stored Q4 net income, after a unit scale of 1, 1e3, 1e6 or 1e9
    (within NI_TOLERANCE);
  - the diluted EPS in that column has the sign of net income;
  - when the table shows diluted weighted shares: |net income / shares - EPS| <= max(5% of |EPS|, 0.02).
Tables from one filing must agree on diluted EPS.

Modes:
  backtest  run the same extraction on Q4 periods that already hold a diluted EPS and report the
            errors; writes nothing.
  fill      write accepted values into null fields only (never overwrites) and record the source of
            each Q4 in q4EpsSources.

SEC access: User-Agent from SEC_USER_AGENT (or the repo default), at most about 5 requests per second,
stop at the first HTTP 403, and --budget requests per run. docs/report/fundamentals/q4-8k-state.json
records finished tickers so the next run continues where this one stopped.
"""
import argparse
import json
import os
import re
import statistics
import sys
import time
from collections import Counter
from datetime import date
from html.parser import HTMLParser
from urllib import error, request

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import build_financials as fin  # noqa: E402
import seed_financials_sec as seed  # noqa: E402

REPO = os.path.dirname(os.path.dirname(SCRIPTS))
FIN_DIR = os.path.join(REPO, "docs", "report", "financials")
STATE_PATH = os.path.join(REPO, "docs", "report", "fundamentals", "q4-8k-state.json")
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/"

SCALES = (1, 1e3, 1e6, 1e9)
NI_TOLERANCE = 0.002       # relative: stored Q4 net income against the table (plus half a unit of its scale)
SHARES_TOLERANCE = 0.05    # relative: net income / shares against EPS (absolute floor EPS_FLOOR)
EPS_FLOOR = 0.02
FILING_DAYS = (15, 150)    # an 8-K with Item 2.02 this many days after the quarter end
MAX_FILINGS = 3
MAX_EXHIBITS = 4
EXTRA_DOCS = 2             # other .htm documents of the filing, read only when no EX-99 table shows the quarter
REQUEST_INTERVAL = 0.2     # seconds between SEC requests
RETRIES = 3
DASHES = ("", "-", "--", "—", "–")

NI_RE = re.compile(r"net\s*(\(loss\)\s*|loss\s*)?(income|earnings)|net\s+loss")
# non-GAAP and adjusted rows sit next to the GAAP rows in most releases and must never be picked
NON_GAAP = ("non-gaap", "non gaap", "nongaap", "adjusted", "excluding", "pro forma")
NI_AVOID = ("per share", "noncontrolling", "non-controlling", "minority", "continuing", "discontinued",
            "before", "comprehensive") + NON_GAAP
EPS_AVOID = ("weighted", "shares", "number of", "average", "continuing") + NON_GAAP   # total EPS, like the net income row


class Forbidden(RuntimeError):
    """SEC answered HTTP 403: the run stops at once."""


class OutOfBudget(RuntimeError):
    """The --budget of SEC requests is used up: the run pauses and the next run continues."""


class NetworkError(RuntimeError):
    """SEC could not be reached after the retries."""


class _TableParser(HTMLParser):
    """Rows of cell texts for every table of an HTML document (no nested tables)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._table, self._row, self._cell = [], None, None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def tables_of(text):
    parser = _TableParser()
    parser.feed(text)
    parser.close()
    return parser.tables


def number(text):
    """'1,234' → 1234, '(567' or '(567)' → -567, '$ 0.25' → 0.25, dashes and words → None."""
    compact = re.sub(r"[\s$,]", "", text or "")
    if compact in DASHES:
        return None
    negative = compact[:1] in ("(", "-", "−")
    digits = compact.strip("()-−")
    if not re.fullmatch(r"\d+(\.\d+)?", digits):
        return None
    value = float(digits)
    return -value if negative else value


def row_of(cells):
    """(label, numeric values in column order; None where a cell is a dash) of one table row."""
    label, values = None, []
    for cell in cells:
        text = cell.strip()
        if text in ("", "$", "(", ")", "%"):
            continue
        if label is None and number(text) is None and re.search(r"[A-Za-z]", text):
            label = text
        elif label is not None:
            values.append(number(text))
    return label, values


def is_net_income(label):
    return bool(NI_RE.search(label)) and not any(word in label for word in NI_AVOID)


def is_eps(label, kind):
    return (kind in label and ("per share" in label or "eps" in label or "earnings per" in label)
            and not any(word in label for word in EPS_AVOID))


def is_diluted_shares(label):
    return "diluted" in label and ("weighted" in label or "shares" in label) and "per share" not in label


def _pick(rows, predicate):
    for label, values in rows:
        if predicate(label):
            return values
    return None


MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]


def shows_period(text, end):
    """The table names the quarter's month and year, e.g. 'Three Months Ended January 31, 2026'."""
    when = date.fromisoformat(end)
    name = MONTHS[when.month - 1]
    pattern = rf"\b(?:{name}|{name[:3]})\.?\s+\d{{1,2}},?\s+{when.year}\b"
    return re.search(pattern, text, re.I) is not None


def split_factor(doc, filing_date):
    """Product of the split ratios dated after the filing: the stored EPS and shares are on today's basis."""
    factor = 1.0
    for event in (doc.get("split") or {}).get("events") or []:
        if isinstance(event, (list, tuple)) and len(event) >= 2:
            try:
                when, ratio = str(event[0]), float(event[1])
            except (TypeError, ValueError):
                continue
            if ratio > 0 and when > filing_date:
                factor *= ratio
    return factor


def table_eps(table, target, end):
    """
    One table → (result dict, None), (None, reason) when it is the quarter but fails a check, or (None, None)
    when it does not show this quarter's net income at all.
    """
    if not shows_period(" ".join(" ".join(row) for row in table), end):
        return None, None
    rows = []
    for cells in table:
        label, values = row_of(cells)
        if label:
            rows.append((label.lower(), values))
    eps_label, eps_row = next(((label, values) for label, values in rows if is_eps(label, "diluted")),
                              (None, None))
    if eps_row is None:
        return None, None
    hits = []                       # (column, unit scale, net values, net label) over every net income row
    for label, values in rows:
        if not is_net_income(label):
            continue
        for j, value in enumerate(values):
            for scale in SCALES:
                if value is not None and abs(value * scale - target) <= max(NI_TOLERANCE * abs(target), 0.5 * scale):
                    hits.append((j, scale, values, label))
    if not hits:
        return None, None
    if len({(j, scale) for j, scale, _, _ in hits}) != 1:
        return None, "net income matches more than one column or unit"
    j, scale, net_row, net_label = hits[0]
    if len(net_row) != len(eps_row):
        return None, "net income and EPS rows have different column counts"
    eps = eps_row[j]
    if eps is None:
        return None, "EPS is empty in the matched column"
    if eps == 0:
        return None, "EPS is zero"
    net = net_row[j] * scale
    if (eps > 0) != (net > 0):
        return None, "EPS sign differs from net income"
    shares_row = _pick(rows, is_diluted_shares)
    diluted_shares = None
    if shares_row is not None and len(shares_row) == len(net_row) and shares_row[j]:
        if shares_row[j] <= 0:
            return None, "shares are not positive"
        implied = net_row[j] / shares_row[j]          # both in the table's own unit, so the scale cancels
        if abs(implied - eps) > max(SHARES_TOLERANCE * abs(eps), EPS_FLOOR):
            return None, "net income divided by shares differs from EPS"
        diluted_shares = shares_row[j] * scale
    basic_row = _pick(rows, lambda label: is_eps(label, "basic"))
    basic = basic_row[j] if basic_row is not None and len(basic_row) == len(net_row) else None
    per_share_rows = [[label, values] for label, values in rows if "per share" in label or "earnings per" in label]
    return {"epsDiluted": eps, "epsBasic": basic, "dilutedShares": diluted_shares, "scale": scale,
            "netLabel": net_label, "epsLabel": eps_label, "perShareRows": per_share_rows[:8]}, None


class Sec:
    """Minimal SEC client: identified User-Agent, pacing, 403 stops the run, budget of requests."""

    def __init__(self, user_agent, budget=None, opener=None, sleep=time.sleep, clock=time.monotonic):
        self.user_agent = user_agent
        self.budget = budget
        self.opener = opener or self._open
        self.sleep = sleep
        self.clock = clock
        self.requests = 0
        self._last = None

    @staticmethod
    def _open(req):
        with request.urlopen(req, timeout=30) as response:
            return response.read()

    def get(self, url):
        """Body bytes, or None for 404."""
        if self.budget is not None and self.requests >= self.budget:
            raise OutOfBudget(url)
        if self._last is not None:
            wait = REQUEST_INTERVAL - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        req = request.Request(url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "identity"})
        for attempt in range(RETRIES):
            self.requests += 1
            self._last = self.clock()
            try:
                return self.opener(req)
            except error.HTTPError as exc:
                if exc.code == 403:
                    raise Forbidden(url) from exc
                if exc.code == 404:
                    return None
                if exc.code == 429 or exc.code >= 500:
                    self.sleep(2 * (attempt + 1))
                    continue
                raise
            except (error.URLError, TimeoutError, ConnectionError):
                self.sleep(2 * (attempt + 1))
        raise NetworkError(url)

    def get_json(self, url):
        body = self.get(url)
        return None if body is None else json.loads(body.decode("utf-8", "replace"))

    def get_text(self, url):
        body = self.get(url)
        return None if body is None else body.decode("utf-8", "replace")


def filing_rows(sec, cik):
    """Every filing of a CIK (recent block plus the older submission pages), newest first."""
    main = sec.get_json(SUBMISSIONS_URL.format(cik=int(cik)))
    if not main:
        return []
    filings = main.get("filings") or {}
    blocks = [filings.get("recent") or {}]
    for page in filings.get("files") or []:
        block = sec.get_json("https://data.sec.gov/submissions/" + page["name"])
        if block:
            blocks.append(block)
    rows = []
    for block in blocks:
        count = len(block.get("form") or [])
        for i in range(count):
            rows.append({name: (block.get(name) or [""] * count)[i]
                         for name in ("form", "filingDate", "accessionNumber", "items")})
    return rows


def candidate_filings(rows, fiscal_end):
    """8-K filings with Item 2.02 (results of operations) inside FILING_DAYS after the quarter end, nearest first."""
    picked = []
    for row in rows:
        if row["form"] != "8-K" or "2.02" not in (row["items"] or ""):
            continue
        try:
            days = (date.fromisoformat(row["filingDate"]) - fiscal_end).days
        except ValueError:
            continue
        if FILING_DAYS[0] <= days <= FILING_DAYS[1]:
            picked.append((days, row))
    return [row for _, row in sorted(picked, key=lambda item: item[0])]


def is_release_exhibit(item):
    name = item.get("name") or ""
    kind = (item.get("type") or "").upper()
    return name.lower().endswith((".htm", ".html")) and (
        kind.startswith("EX-99") or re.search(r"(ex|exhibit)[-_ ]?99", name, re.I) is not None)


def index_exhibits(page):
    """EX-99 document names from the Type column of a filing's index page (the file names do not say it)."""
    names = []
    for table in tables_of(page or ""):
        for row in table:
            cells = [cell.strip() for cell in row]
            kind = any(re.fullmatch(r"EX-99(\.\d+)?", cell, re.I) for cell in cells)
            name = next((cell for cell in cells if re.fullmatch(r"[\w.-]+\.html?", cell, re.I)), None)
            if kind and name and name not in names:
                names.append(name)
    return names


def from_filing(sec, cik, filing, target, end, debug=None):
    accession = filing["accessionNumber"]
    base = ARCHIVE_URL.format(cik=int(cik), folder=accession.replace("-", ""))
    index = sec.get_json(base + "index.json")
    if index is None:
        return None, "filing index not available"
    items = (index.get("directory") or {}).get("item") or []
    page = sec.get_text(base + accession + "-index.html") or sec.get_text(base + accession + "-index.htm")
    by_name = [item["name"] for item in items if is_release_exhibit(item)]
    exhibits = list(dict.fromkeys(index_exhibits(page) + by_name))[:MAX_EXHIBITS]
    # when no EX-99 table shows the quarter, the release may sit in another document of the same filing
    extra = [item["name"] for item in items if (item.get("name") or "").lower().endswith((".htm", ".html"))
             and item["name"] not in exhibits][:EXTRA_DOCS]
    if not exhibits and not extra:
        if debug is not None:
            debug.append({"index_items": [item.get("name") for item in items][:12]})
        return None, "no EX-99 exhibit in the filing"
    found = []
    reason = "no table shows this quarter's net income"
    for position, name in enumerate(exhibits + extra):
        if position >= len(exhibits) and found:
            break
        url = base + name
        text = sec.get_text(url)
        if text is None:
            continue
        for table in tables_of(text):
            result, why = table_eps(table, target, end)
            if result is not None:
                found.append(dict(result, accession=accession, url=url))
            elif why:
                reason = why
    if not found:
        if debug is not None:
            text = sec.get_text(base + (exhibits + extra)[0]) or ""
            debug.append({"net_like_text": re.findall(r">\s*([^<>]{0,80}net[^<>]{0,60})<", text, re.I)[:6]})
        return None, reason
    if len({item["epsDiluted"] for item in found}) > 1:
        return None, "tables disagree on diluted EPS"
    return found[0], None


def find_quarter(sec, cik, filings, end, target, debug=None):
    """The first candidate filing that gives this quarter → (result, None), else (None, reason)."""
    reason = "no 8-K with Item 2.02 in the window"
    for filing in candidate_filings(filings, date.fromisoformat(end))[:MAX_FILINGS]:
        result, why = from_filing(sec, cik, filing, target, end, debug)
        if result is not None:
            return dict(result, end=end, filingDate=filing["filingDate"]), None
        reason = why or reason
    return None, reason


def q4_periods(doc):
    """Every Q4 quarter that holds net income: index, end, stored net income and stored diluted EPS."""
    quarterly = doc.get("quarterly") or {}
    ends, fq = quarterly.get("end") or [], quarterly.get("fq") or []
    values = quarterly.get("values") or {}

    def at(field, i):
        column = values.get(field) or []
        return column[i] if i < len(column) else None

    return [{"index": i, "end": end, "target": at("netIncome", i), "eps": at("epsDiluted", i)}
            for i, end in enumerate(ends) if i < len(fq) and fq[i] == 4 and at("netIncome", i)]


def process_ticker(sec, key, cik, doc, mode, rows):
    """Appends one row per period to rows; True when every period of the ticker was looked at."""
    periods = q4_periods(doc)
    if mode == "backtest":
        periods = [p for p in periods if p["eps"] is not None]
    else:
        periods = [p for p in periods if p["eps"] is None]
    if not periods:
        return True
    filings = filing_rows(sec, cik)
    for p in periods:
        debug = []
        result, reason = find_quarter(sec, cik, filings, p["end"], p["target"], debug)
        if result is not None:
            factor = split_factor(doc, result["filingDate"])
            if factor != 1:
                result = dict(result, asReported=result["epsDiluted"], splitFactor=factor,
                              epsDiluted=round(result["epsDiluted"] / factor, 6),
                              epsBasic=None if result["epsBasic"] is None else round(result["epsBasic"] / factor, 6),
                              dilutedShares=None if result["dilutedShares"] is None
                              else round(result["dilutedShares"] * factor, 1))
        rows.append({"key": key, "end": p["end"], "stored": p["eps"], "target": p["target"],
                     "result": result, "reason": reason, "debug": debug[:2]})
    return True


def apply_fill(doc, found):
    """Writes accepted values into null fields only; records the source of every filled quarter."""
    quarterly = doc["quarterly"]
    ends = quarterly["end"]
    values = quarterly.setdefault("values", {})
    sources = doc.setdefault("q4EpsSources", {})
    for i, end in enumerate(ends):
        result = found.get(end)
        if not result:
            continue
        for field in ("epsDiluted", "epsBasic", "dilutedShares"):
            value = result.get(field)
            if value is None:
                continue
            column = values.setdefault(field, [None] * len(ends))
            if len(column) < len(ends):
                column.extend([None] * (len(ends) - len(column)))
            if column[i] is None:
                column[i] = value
        sources[end] = {"method": "8-K EX-99.1", "accession": result["accession"], "url": result["url"],
                        "filingDate": result["filingDate"]}


def backtest_error(estimate, stored):
    return abs(estimate - stored) / max(abs(stored), 0.10)


def within_tolerance(estimate, stored):
    return abs(estimate - stored) <= max(0.02 * abs(stored), 0.011)


def load_state(path):
    doc = seed.read_json(path) if os.path.exists(path) else None
    if isinstance(doc, dict) and isinstance(doc.get("done"), dict):
        return doc
    return {"version": 1, "done": {}}


def summarize(rows, mode):
    accepted = [r for r in rows if r["result"]]
    out = {"mode": mode, "periods": len(rows), "accepted": len(accepted),
           "reasons": dict(Counter(r["reason"] for r in rows if not r["result"]).most_common())}
    if mode == "backtest" and rows:
        out["coverage"] = round(len(accepted) / len(rows), 4)
        if accepted:
            errors = [backtest_error(r["result"]["epsDiluted"], r["stored"]) for r in accepted]
            out["median_error"] = round(statistics.median(errors), 4)
            right = sum(within_tolerance(r["result"]["epsDiluted"], r["stored"]) for r in accepted)
            out["correct_share_of_all_periods"] = round(right / len(rows), 4)
    return out


def run(args, sec):
    cik_of = seed.parse_ticker_map(sec.get_json(TICKERS_URL) or {})
    state = load_state(STATE_PATH)
    wanted = {t.strip().upper() for t in args.tickers.split(",") if t.strip()} if args.tickers else None
    keys = sorted(name[:-5] for name in os.listdir(FIN_DIR) if name.endswith(".json") and name != "index.json")
    keys = [k for k in keys if fin.market_of(k) == "US" and (wanted is None or k in wanted)]
    if args.limit:
        keys = keys[:args.limit]
    rows, stop, filled = [], None, 0
    today = date.today().isoformat()
    for key in keys:
        if args.mode == "fill" and key in state["done"]:
            continue
        path = os.path.join(FIN_DIR, key + ".json")
        doc = seed.read_json(path)
        cik = cik_of.get(key)
        if cik is None or not isinstance(doc, dict):
            continue
        ticker_rows, complete = [], False
        try:
            complete = process_ticker(sec, key, cik, doc, args.mode, ticker_rows)
        except (OutOfBudget, Forbidden, NetworkError) as exc:
            stop = f"{type(exc).__name__}: {exc}"
        rows.extend(ticker_rows)
        if args.mode == "fill":
            found = {r["end"]: r["result"] for r in ticker_rows if r["result"]}
            if found:
                apply_fill(doc, found)
                seed.write_atomic(path, doc)
                filled += len(found)
            if complete:
                state["done"][key] = {"checked": today, "filled": len(found)}
        if stop:
            break
    if args.mode == "fill":
        seed.write_atomic(STATE_PATH, state)
    report = summarize(rows, args.mode)
    mismatches = [{"key": r["key"], "end": r["end"], "stored": r["stored"], "estimate": r["result"]["epsDiluted"],
                   "basic": r["result"]["epsBasic"], "netLabel": r["result"].get("netLabel"),
                   "epsLabel": r["result"].get("epsLabel"), "perShareRows": r["result"].get("perShareRows"),
                   "url": r["result"]["url"]}
                  for r in rows if args.mode == "backtest" and r["result"]
                  and not within_tolerance(r["result"]["epsDiluted"], r["stored"])]
    samples = {}
    for r in rows:
        kind = (r["reason"] or "").split(":")[0][:40]
        if r["reason"] and kind not in samples:
            samples[kind] = {"key": r["key"], "end": r["end"], "stored": r["stored"], "debug": r["debug"]}
    report.update(filled=filled, tickers_done=len(state["done"]), stopped=stop, requests=sec.requests,
                  mismatches=mismatches[:15], samples=samples,
                  rows=[{k: r[k] for k in ("key", "end", "stored", "result", "reason")} for r in rows])
    return report


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Q4 EPS from 8-K earnings releases (see module docstring)")
    parser.add_argument("--mode", choices=("backtest", "fill"), default="backtest")
    parser.add_argument("--tickers", default="", help="comma separated; empty = every US file")
    parser.add_argument("--limit", type=int, default=0, help="only the first N files (0 = all)")
    parser.add_argument("--budget", type=int, default=6000, help="SEC requests this run")
    parser.add_argument("--report", default="", help="also write the report JSON to this path")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    configured = (os.environ.get("SEC_USER_AGENT") or "").strip()
    user_agent = seed.resolve_user_agent({"SEC_USER_AGENT": configured} if configured else {})
    sec = Sec(user_agent, budget=args.budget)
    try:
        report = run(args, sec)
    except Forbidden as exc:
        print(f"STOPPED: SEC returned HTTP 403 for {exc}; files written so far are kept, re-run later.", flush=True)
        return 2
    except NetworkError as exc:
        print(f"STOPPED: SEC could not be reached ({exc}); re-run later.", flush=True)
        return 2
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as file:
            file.write(text)
    summary = {k: v for k, v in report.items() if k != "rows"}
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
